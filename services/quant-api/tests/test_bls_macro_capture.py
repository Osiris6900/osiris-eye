"""Actual BLS-shaped vintages retain receive-time availability and policy gates."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta

import pytest

from osiris_api import macro_economic_watch as macro
from osiris_api.data_store import ResearchStore

NOW = datetime(2026, 11, 14, 12, tzinfo=UTC)
COMMIT = "a" * 40
TRUSTED_CAPTURES = {}


def response():
    series = []
    for name in macro.SERIES:
        value = {"CUUR0000SA0": "337.511", "LNS14000000": "4.1", "CES0000000001": "159293"}[name]
        rows = [
            {"year": str(year), "period": f"M{month:02}", "value": value, "footnotes": [{}]}
            for year in (2025, 2026)
            for month in range(1, 13 if year == 2025 else 11)
        ]
        series.append({"seriesID": name, "data": list(reversed(rows))})
    return {"status": "REQUEST_SUCCEEDED", "message": [], "Results": {"series": series}}


def capture(tmp_path, data=None, *, name="capture", now=NOW, status=200, **kwargs):
    raw = json.dumps(response() if data is None else data).encode()
    directory = tmp_path / name
    result = macro.capture_bls(
        directory,
        candidate_commit=COMMIT,
        series=list(macro.SERIES),
        transport=lambda body, timeout: (status, raw),
        clock=lambda: now,
        **kwargs,
    )
    TRUSTED_CAPTURES[directory] = result["capture_sha256"]
    return directory, result, hashlib.sha256(raw).hexdigest()


def verify(directory, digest):
    return macro.verify_bls_capture(
        directory,
        expected_commit=COMMIT,
        expected_response_sha256=digest,
        expected_capture_sha256=TRUSTED_CAPTURES[directory],
    )


def policy():
    # Deterministic policy fixture only; no real vendor rights/review is asserted.
    return {
        macro.PROVIDER: {
            "authorized_source": "synthetic-policy-test",
            "entitlement_reference": "fixture-only",
            "redistribution_rights": "permitted",
            "availability_timestamps_required": True,
            "instruments": ["MACRO:BLS:" + name for name in macro.SERIES],
            "quality_review": {
                "status": "passed",
                "reviewer": "synthetic-fixture",
                "reviewed_at": NOW.isoformat(),
            },
        }
    }


def ingest(store, directory, digest, now=NOW, **kwargs):
    return macro.ingest_bls_capture(
        store,
        directory,
        expected_commit=COMMIT,
        expected_response_sha256=digest,
        expected_capture_sha256=TRUSTED_CAPTURES[directory],
        policy=kwargs.pop("policy", policy()),
        now=now,
        **kwargs,
    )


def test_capture_reproduces_three_monthly_series_without_backdating(tmp_path):
    directory, result, digest = capture(tmp_path)
    assert result["passed"] is True
    assert result["observations"] == 66
    assert verify(directory, digest)["observations"] == 66
    assert json.loads((directory / "request.json").read_bytes()) == {
        "seriesid": sorted(macro.SERIES),
        "startyear": "2025",
        "endyear": "2026",
    }
    rows = json.loads((directory / "observations.json").read_bytes())
    assert {row["available_at"] for row in rows} == {NOW.isoformat()}
    assert all(datetime.fromisoformat(row["event_time"]) < NOW for row in rows)
    assert all(
        row["payload"]["reference_time_basis"] == "period_end_exclusive_not_release_time"
        for row in rows
    )
    assert result["vendor_rights_verified"] is False
    assert result["release_gate_accepted"] is False


def test_explicit_unavailable_statistic_retains_absence_not_numeric_fill(tmp_path, monkeypatch):
    data = response()
    for group in data["Results"]["series"]:
        if group["seriesID"] in {"CUUR0000SA0", "LNS14000000"}:
            row = next(r for r in group["data"] if r["year"] == "2025" and r["period"] == "M10")
            row.update(
                value="-",
                footnotes=[
                    {
                        "code": "X",
                        "text": "Data unavailable due to the 2025 lapse in appropriations",
                    }
                ],
            )
    directory, result, digest = capture(tmp_path, data)
    assert result["passed"] is True
    assert result["numeric_feed_complete"] is False
    assert (result["available_values"], result["unavailable_values"]) == (64, 2)
    rows = json.loads((directory / "observations.json").read_bytes())
    missing = [row for row in rows if row["payload"]["value_status"] == "unavailable"]
    assert len(missing) == 2
    assert all(row["payload"]["value"] is None and row["payload"]["footnotes"] for row in missing)
    monkeypatch.setenv("OSIRIS_P2P_INGEST_ENABLED", "1")
    accepted = ingest(ResearchStore(str(tmp_path / "research.sqlite3")), directory, digest)
    assert accepted["numeric_records_ingested"] == 64
    assert accepted["unavailable_records_ingested"] == 2
    assert accepted["numeric_feed_complete"] is False


def test_ingestion_is_point_in_time_idempotent_and_revision_preserving(tmp_path, monkeypatch):
    monkeypatch.setenv("OSIRIS_P2P_INGEST_ENABLED", "1")
    directory, _, digest = capture(tmp_path)
    store = ResearchStore(str(tmp_path / "research.sqlite3"))
    assert ingest(store, directory, digest)["ingested"] == 66
    assert store.list_as_of(macro.DATASET, (NOW - timedelta(seconds=1)).isoformat()) == []
    original = store.list(macro.DATASET, 100)
    assert ingest(store, directory, digest)["duplicates_skipped"] == 66
    revised = response()
    revised["Results"]["series"][1]["data"][0]["value"] = "4.2"
    later = NOW + timedelta(seconds=60)
    new_dir, _, new_digest = capture(tmp_path, revised, name="revision", now=later)
    accepted = ingest(store, new_dir, new_digest, now=later)
    assert (accepted["ingested"], accepted["duplicates_skipped"], accepted["revisions"]) == (
        1,
        65,
        1,
    )
    assert sorted(
        store.list_as_of(macro.DATASET, NOW.isoformat(), 100), key=lambda r: r["id"]
    ) == sorted(original, key=lambda r: r["id"])
    changed = store.list(macro.DATASET, 100)[0]
    assert changed["available_at"] == later.isoformat()
    assert changed["payload"]["value"] == "4.2"
    assert changed["payload"]["supersedes_record_key"]
    # A return to the original value is a new vintage, not an old duplicate.
    reverted_dir, _, reverted_digest = capture(
        tmp_path, name="reverted", now=later + timedelta(seconds=60)
    )
    reverted = ingest(store, reverted_dir, reverted_digest, now=later + timedelta(seconds=60))
    assert (reverted["ingested"], reverted["duplicates_skipped"], reverted["revisions"]) == (
        1,
        65,
        1,
    )
    assert store.list(macro.DATASET, 100)[0]["payload"]["value"] == "4.1"
    with pytest.raises(ValueError, match="vintage_time_rollback"):
        ingest(store, directory, digest, now=later + timedelta(seconds=60))
    assert len(store.list(macro.DATASET, 100)) == 68


def test_kill_switch_missing_rights_and_stale_capture_refuse_before_writes(tmp_path, monkeypatch):
    directory, _, digest = capture(tmp_path)
    store = ResearchStore(str(tmp_path / "research.sqlite3"))
    monkeypatch.delenv("OSIRIS_P2P_INGEST_ENABLED", raising=False)
    with pytest.raises(ValueError, match="kill_switch"):
        ingest(store, directory, digest)
    monkeypatch.setenv("OSIRIS_P2P_INGEST_ENABLED", "1")
    with pytest.raises(ValueError, match="provider_policy_missing"):
        ingest(store, directory, digest, policy={})
    revoked = policy()
    revoked[macro.PROVIDER]["redistribution_rights"] = "revoked"
    with pytest.raises(ValueError, match="redistribution_not_permitted"):
        ingest(store, directory, digest, policy=revoked)
    with pytest.raises(ValueError, match="stale_or_future"):
        ingest(store, directory, digest, now=NOW + timedelta(seconds=301))
    with pytest.raises(ValueError, match="stale_or_future"):
        ingest(store, directory, digest, now=NOW - timedelta(seconds=1))
    assert store.list(macro.DATASET) == []


@pytest.mark.parametrize(
    "field,value",
    [("available_at", "2020-01-01T00:00:00+00:00"), ("event_time", "2020-01-01T00:00:00+00:00")],
)
def test_rehashed_normalization_cannot_backdate_availability(tmp_path, field, value):
    directory, _, digest = capture(tmp_path)
    path = directory / "observations.json"
    rows = json.loads(path.read_bytes())
    rows[0][field] = value
    path.write_text(json.dumps(rows))
    with pytest.raises(ValueError, match="normalization_mismatch"):
        verify(directory, digest)


@pytest.mark.parametrize("tamper", ["response", "request", "plan", "approval"])
def test_capture_tampering_is_refused(tmp_path, tamper):
    directory, _, digest = capture(tmp_path)
    if tamper == "response":
        (directory / "response.bin").write_bytes(b"{}")
    elif tamper == "request":
        (directory / "request.json").write_bytes(b"{}")
    else:
        path = directory / ("plan.json" if tamper == "plan" else "capture.json")
        data = json.loads(path.read_bytes())
        data["start_year" if tamper == "plan" else "vendor_rights_verified"] = (
            2020 if tamper == "plan" else True
        )
        path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        verify(directory, digest)


@pytest.mark.parametrize(
    "case",
    [
        "missing_series",
        "duplicate_period",
        "gap",
        "future",
        "stale",
        "bad_value",
        "rate_over_100",
        "annual_only",
    ],
)
def test_invalid_source_data_retains_raw_failure_without_observations(tmp_path, case):
    data = response()
    groups = data["Results"]["series"]
    if case == "missing_series":
        groups.pop()
    elif case == "duplicate_period":
        groups[0]["data"].append(copy.deepcopy(groups[0]["data"][0]))
    elif case == "gap":
        del groups[0]["data"][4]
    elif case == "future":
        groups[0]["data"][0]["period"] = "M12"
    elif case == "stale":
        groups[0]["data"] = [r for r in groups[0]["data"] if r["year"] == "2025"]
    elif case == "bad_value":
        groups[0]["data"][0]["value"] = "1e999999999"
    elif case == "rate_over_100":
        groups[1]["data"][0]["value"] = "101"
    else:
        groups[0]["data"] = [{"year": "2026", "period": "M13", "value": "1", "footnotes": []}]
    directory, result, digest = capture(tmp_path, data)
    assert result["passed"] is False
    assert hashlib.sha256((directory / "response.bin").read_bytes()).hexdigest() == digest
    assert not (directory / "observations.json").exists()


def test_refused_http_and_source_clock_changes_retain_failure(tmp_path):
    directory, result, _ = capture(tmp_path, status=302)
    assert result["error"]["message"] == "bls_http_refused"
    assert (directory / "response.bin").exists()
    directory2, result2, _ = capture(tmp_path, name="changed", source_guard=lambda: False)
    assert result2["error"]["message"] == "bls_source_changed_during_capture"
    assert not (directory2 / "observations.json").exists()
    times = iter([NOW, NOW - timedelta(seconds=1)])
    raw = json.dumps(response()).encode()
    result3 = macro.capture_bls(
        tmp_path / "clock",
        candidate_commit=COMMIT,
        series=list(macro.SERIES),
        clock=lambda: next(times),
        transport=lambda body, timeout: (200, raw),
    )
    assert result3["error"]["message"] == "bls_clock_rollback"


def test_output_is_exclusive_and_selection_bounded_before_request(tmp_path):
    directory, _, digest = capture(tmp_path)
    called = []
    transport = lambda *args: called.append(args)
    with pytest.raises(FileExistsError):
        macro.capture_bls(
            directory,
            candidate_commit=COMMIT,
            series=list(macro.SERIES),
            transport=transport,
            clock=lambda: NOW,
        )
    for series in ([], ["unknown"], ["CUUR0000SA0"] * 2):
        with pytest.raises(ValueError, match="explicit_supported"):
            macro.capture_bls(
                tmp_path / "invalid",
                candidate_commit=COMMIT,
                series=series,
                transport=transport,
                clock=lambda: NOW,
            )
    assert called == []
    assert verify(directory, digest)["passed"] is True


def test_oversized_capture_and_duplicate_json_fail_closed(tmp_path):
    result = macro.capture_bls(
        tmp_path / "huge",
        candidate_commit=COMMIT,
        series=list(macro.SERIES),
        transport=lambda body, timeout: (200, b" " * (macro.MAX_RESPONSE_BYTES + 1)),
        clock=lambda: NOW,
    )
    assert result["passed"] is False
    assert not (tmp_path / "huge" / "response.bin").exists()
    raw = b'{"status":"REQUEST_SUCCEEDED","status":"REQUEST_FAILED"}'
    result = macro.capture_bls(
        tmp_path / "duplicate",
        candidate_commit=COMMIT,
        series=list(macro.SERIES),
        transport=lambda body, timeout: (200, raw),
        clock=lambda: NOW,
    )
    assert result["passed"] is False


def test_capture_verification_refuses_symlinked_evidence(tmp_path):
    directory, _, digest = capture(tmp_path)
    original = directory / "response.bin"
    moved = tmp_path / "original-response.bin"
    original.rename(moved)
    original.symlink_to(moved)
    with pytest.raises(ValueError, match="must_be_regular"):
        verify(directory, digest)


def test_http_transport_uses_only_fixed_post_and_refuses_redirects(monkeypatch):
    from io import BytesIO
    from urllib.error import HTTPError

    requests = []

    class Opener:
        def open(self, request, timeout):
            requests.append((request.full_url, request.method, request.data, timeout))
            raise HTTPError(request.full_url, 302, "redirect", {}, BytesIO(b"refused"))

    def opener(handler):
        assert (
            handler.redirect_request(None, None, 302, "redirect", {}, "https://other.example")
            is None
        )
        return Opener()

    monkeypatch.setattr(macro, "build_opener", opener)
    assert macro._http_post(b"{}", 10) == (302, b"refused")
    assert requests == [(macro.ENDPOINT, "POST", b"{}", 10)]


def test_cli_refusal_does_not_provision_a_database(tmp_path, monkeypatch):
    directory, _, digest = capture(tmp_path)
    policy_file = tmp_path / "policy.json"
    policy_file.write_text(json.dumps(policy()))
    database = tmp_path / "must-not-exist.sqlite3"
    monkeypatch.delenv("OSIRIS_P2P_INGEST_ENABLED", raising=False)
    result = subprocess.run(
        [
            sys.executable,
            str(macro.ROOT / "scripts/record_bls_macro.py"),
            "ingest",
            "--capture-dir",
            str(directory),
            "--commit",
            COMMIT,
            "--response-sha256",
            digest,
            "--capture-sha256",
            TRUSTED_CAPTURES[directory],
            "--database",
            str(database),
            "--policy-json",
            str(policy_file),
        ],
        env=os.environ.copy(),
        check=False,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 1
    assert "ingestion_disabled_kill_switch" in result.stdout
    assert not database.exists()
