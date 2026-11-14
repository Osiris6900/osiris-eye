"""Forward-capture boundaries and independent offline research reproduction."""
from datetime import UTC, datetime, timedelta
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from osiris_api.control_plane.database import digest
from osiris_api.keyless_research import (
    ORIGIN, build_archive, capture_response, read_archive,
    research_plan, validate_plan, verify_archive,
)

NOW = datetime(2026, 11, 13, 12, tzinfo=UTC)
COMMIT = "a" * 40


def response(kind, payload, when, symbol="BTC-USD"):
    body = json.dumps(payload)
    return {"kind": kind, "url": ORIGIN + "/products/" + symbol + ("/ticker" if kind == "ticker" else ""),
            "request_started_at": (when - timedelta(milliseconds=10)).isoformat(),
            "received_at": when.isoformat(), "body_utf8": body,
            "body_sha256": hashlib.sha256(body.encode()).hexdigest()}


def fixture():
    plan = research_plan(COMMIT, samples=32, interval_seconds=3, created_at=NOW)
    product = response("product", {
        "id": "BTC-USD", "base_currency": "BTC", "quote_currency": "USD", "status": "online",
        "trading_disabled": False, "base_increment": "0.00000001", "quote_increment": "0.01",
    }, NOW + timedelta(milliseconds=100))
    records = [product]
    for i in range(32):
        received = NOW + timedelta(seconds=1 + i * 3)
        records.append(response("ticker", {
            "trade_id": 1000 + i, "price": str(100 + i % 7), "bid": "99", "ask": "108",
            "time": (received - timedelta(milliseconds=50)).isoformat(),
        }, received))
    return plan, records


def archive():
    plan, records = fixture()
    return build_archive(plan, records, frozen_plan_sha256=digest(plan))


def test_forward_research_reproduces_original_data_and_cost_grid_without_network():
    item = archive()
    with patch("osiris_api.keyless_research._get", side_effect=AssertionError("offline")):
        result = verify_archive(item, expected_commit=COMMIT, expected_sha256=item["archive_sha256"])
    assert result["consistent"] is result["reproduced"] is True
    assert result["samples"] == 32 and result["folds"] == 5
    assert all(result[name] is False for name in (
        "source_origin_independently_attested", "independent_model_reviewed",
        "vendor_rights_verified", "release_gate_accepted", "live_trading_authorized",
    ))
    assert item["ticks"][0]["available_time"] == item["captures"][1]["received_at"]
    assert item["ticks"][0]["available_time"] != item["ticks"][0]["event_time"]
    assert item["governance"]["approvals"] == []
    baseline = item["fitted_baseline"]
    split = baseline["model"]["train_rows"]
    assert all(row["label_available_ms"] < baseline["model"]["holdout_start_ms"] for row in baseline["rows"][:split])
    assert baseline["purged_boundary_rows"] == 1
    assert baseline["model"]["independent_model_approval"] is False
    assert baseline["model"]["holdout_rows"] == 4
    reports = item["cost_sensitivity"]
    assert set(reports) == {"0", "10", "25"}
    assert reports["25"]["aggregate"]["oos_total_return_net"] <= reports["0"]["aggregate"]["oos_total_return_net"]


@pytest.mark.parametrize("field,value", [
    ("samples", True), ("samples", 15), ("samples", 201), ("interval_seconds", 0),
    ("interval_seconds", float("nan")), ("interval_seconds", True),
    ("source_origin", "https://example.invalid"), ("symbol", "BTC-USDT"),
    ("live_trading_authorized", True), ("cost_grid_bps", [0]),
])
def test_rejects_unbounded_changed_or_non_usd_plans(field, value):
    plan, _ = fixture()
    plan[field] = value
    with pytest.raises(ValueError):
        validate_plan(plan)


def test_frozen_plan_cannot_be_changed_to_tune_after_capture():
    plan, records = fixture()
    frozen = digest(plan)
    plan["model"]["scale"] = 0.25
    with pytest.raises(ValueError, match="plan_changed"):
        build_archive(plan, records, frozen_plan_sha256=frozen)


@pytest.mark.parametrize("case", ["hash", "source", "stale", "future", "crossed", "same_trade", "clock", "product"])
def test_raw_source_identity_freshness_and_replay_are_enforced(case):
    plan, records = fixture()
    row = records[2]
    data = json.loads(row["body_utf8"])
    if case == "hash":
        row["body_sha256"] = "0" * 64
    elif case == "source":
        row["url"] = row["url"].replace("coinbase.com", "example.invalid")
    elif case in {"stale", "future"}:
        data["time"] = (NOW - timedelta(hours=1) if case == "stale" else NOW + timedelta(hours=1)).isoformat()
    elif case == "crossed":
        data["bid"], data["ask"] = "108", "99"
    elif case == "same_trade":
        data["trade_id"] -= 1
    elif case == "clock":
        row["received_at"] = records[1]["received_at"]
    elif case == "product":
        records[0]["body_utf8"] = records[0]["body_utf8"].replace('"USD"', '"USDT"')
        records[0]["body_sha256"] = hashlib.sha256(records[0]["body_utf8"].encode()).hexdigest()
    if case in {"stale", "future", "crossed", "same_trade"}:
        row["body_utf8"] = json.dumps(data)
        row["body_sha256"] = hashlib.sha256(row["body_utf8"].encode()).hexdigest()
    with pytest.raises(ValueError):
        build_archive(plan, records, frozen_plan_sha256=digest(plan))


def test_purported_performance_and_approval_cannot_pass_even_after_rehashing():
    for mutate in (
        lambda item: item["cost_sensitivity"]["10"]["aggregate"].update(oos_total_return_net=999),
        lambda item: item["governance"]["approvals"].append("self-approved"),
        lambda item: item.update(vendor_rights_verified=True),
        lambda item: item["code_files_sha256"].update({"walk_forward.py": "0" * 64}),
        lambda item: item["ticks"][0].update(available_time=NOW.isoformat()),
    ):
        item = archive()
        mutate(item)
        item["archive_sha256"] = digest({k: v for k, v in item.items() if k != "archive_sha256"})
        with pytest.raises(ValueError, match="reproduction_mismatch"):
            verify_archive(item, expected_commit=COMMIT, expected_sha256=item["archive_sha256"])


def test_verifier_requires_the_trusted_digest_and_expected_commit():
    item = archive()
    with pytest.raises(ValueError, match="hash_mismatch"):
        verify_archive(item, expected_commit=COMMIT, expected_sha256="0" * 64)
    with pytest.raises(ValueError, match="commit_mismatch"):
        verify_archive(item, expected_commit="b" * 40, expected_sha256=item["archive_sha256"])


def test_capture_uses_fixed_keyless_endpoint_and_actual_client_times():
    started = NOW + timedelta(seconds=1)
    finished = NOW + timedelta(seconds=2)
    with patch("osiris_api.keyless_research._get", return_value=b'{"original":"bytes"}') as get:
        value = capture_response("BTC-USD", "ticker", clock=iter([started, finished]).__next__)
    get.assert_called_once_with(ORIGIN + "/products/BTC-USD/ticker", timeout=8)
    assert value["body_utf8"] == '{"original":"bytes"}'
    assert value["received_at"] == finished.isoformat()
    assert "Authorization" not in value
    with pytest.raises(ValueError):
        capture_response("BTC-USD/../../v2/orders", "ticker")


def test_archive_reader_refuses_symlinks_and_missing_data(tmp_path):
    path = tmp_path / "archive.json"
    path.write_text(json.dumps(archive()))
    assert read_archive(path)["schema"]
    link = tmp_path / "alias.json"
    link.symlink_to(path)
    with pytest.raises(ValueError, match="archive_file"):
        read_archive(link)


def command():
    path = Path(__file__).resolve().parents[3] / "scripts/record_keyless_research.py"
    spec = importlib.util.spec_from_file_location("record_keyless_research_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fake_git(args, **kwargs):
    return COMMIT if "rev-parse" in args else b""


def test_capture_command_freezes_plan_retains_bytes_and_refuses_overwrite(tmp_path):
    script = command()
    _, records = fixture()
    target = tmp_path / "capture"
    with patch.object(script.subprocess, "check_output", side_effect=fake_git), \
         patch.object(script, "datetime", SimpleNamespace(now=lambda _: NOW)), \
         patch.object(script, "capture_response", side_effect=records), \
         patch.object(script.time, "sleep"):
        result = script.capture(target, samples=32, interval=3, symbol="BTC-USD")
        assert result["passed"] is True
        with pytest.raises(FileExistsError):
            script.capture(target, samples=32, interval=3, symbol="BTC-USD")
    assert len((target / "capture-responses.jsonl").read_text().splitlines()) == 33
    assert all(path.stat().st_mode & 0o077 == 0 for path in target.iterdir())
    assert read_archive(target / "research-archive.json")["captures"] == records


def test_capture_failure_retains_original_partial_responses_and_failure(tmp_path):
    script = command()
    _, records = fixture()
    target = tmp_path / "capture"
    with patch.object(script.subprocess, "check_output", side_effect=fake_git), \
         patch.object(script, "datetime", SimpleNamespace(now=lambda _: NOW)), \
         patch.object(script, "capture_response", side_effect=[*records[:2], RuntimeError("source_unavailable")]), \
         patch.object(script.time, "sleep"):
        result = script.capture(target, samples=32, interval=3, symbol="BTC-USD")
    assert result["passed"] is False
    assert result["error"]["message"] == "source_unavailable"
    assert not (target / "research-archive.json").exists()
    assert len((target / "capture-responses.jsonl").read_text().splitlines()) == 2
    assert json.loads((target / "capture-result.json").read_text())["passed"] is False
