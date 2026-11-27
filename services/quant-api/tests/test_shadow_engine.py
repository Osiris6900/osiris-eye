"""Sealed, lagged shadow runs and durable operator-paper comparison evidence."""

import base64
import hashlib
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from osiris_api.control_plane.api import router
from osiris_api.control_plane.database import Database
from osiris_api.control_plane.identity import totp
from osiris_api.control_plane.shadow import COMPARISONS, RUNS, ShadowService


@pytest.fixture
def shadow(tmp_path):
    db = Database(str(tmp_path / "shadow.db"))
    service = ShadowService(db)
    ticks = [
        {"event_ms": 10, "price": "100", "target": 1},
        {"event_ms": 20, "price": "100", "target": 1},
        {"event_ms": 30, "price": "110", "target": 0},
        {"event_ms": 40, "price": "110", "target": 0},
    ]
    return db, service, ticks


def paper_request(run, *, final_delta="0"):
    observations = []
    for index, (event_ms, equity) in enumerate(zip(run["event_times_ms"], run["equity"])):
        if index == len(run["equity"]) - 1:
            equity = str(Decimal(equity) + Decimal(final_delta))
        observations.append(
            {
                "event_ms": event_ms,
                "available_ms": event_ms + 1,
                "equity": equity,
                "source_sha256": hashlib.sha256(
                    f"operator-paper:{event_ms}:{equity}".encode()
                ).hexdigest(),
            }
        )
    return {
        "run_id": run["run_id"],
        "comparison_as_of_ms": run["event_times_ms"][-1] + 1,
        "paper_observations": observations,
    }


def test_run_is_lagged_sealed_persistent_and_non_executing(shadow):
    db, service, ticks = shadow
    result = service.run("run-a", ticks)
    assert result["policy"] == "lagged-long-flat-independent-risk-v2"
    assert result["event_times_ms"] == [10, 20, 30, 40]
    assert result["fills"][0]["event_ms"] == 20
    assert result["fills"][-1]["event_ms"] == 40
    assert Decimal(result["quantity"]) == 0
    assert result["broker_orders"] == 0
    assert result["live_execution"] is False
    assert (
        result["shadow_run_sha256"]
        == hashlib.sha256(
            json.dumps(
                {key: value for key, value in result.items() if key != "shadow_run_sha256"},
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode()
        ).hexdigest()
    )
    restarted = ShadowService(Database(db.path))
    assert restarted.get_run("run-a") == result
    assert restarted.get_run("missing") is None


def test_daily_loss_halt_retains_independent_risk_controls(shadow):
    _, service, _ = shadow
    ticks = [
        {"event_ms": 1, "price": "100", "target": 1},
        {"event_ms": 2, "price": "100", "target": 1},
        {"event_ms": 3, "price": "1", "target": 1},
        {"event_ms": 4, "price": "1", "target": 1},
    ]
    result = service.run("run-risk", ticks, daily_loss_limit="10")
    assert result["risk_halted"] is True
    assert result["fills"][0]["event_ms"] == 2
    assert result["fills"][-1]["event_ms"] == 3
    assert Decimal(result["cash"]) >= 0
    assert result["broker_orders"] == 0


def test_run_replay_is_normalized_and_conflicts_fail_closed(shadow):
    _, service, ticks = shadow
    result = service.run("run-replay", ticks)
    equivalent = [dict(tick) for tick in ticks]
    equivalent[0]["price"] = "100.0"
    assert service.run("run-replay", equivalent) == result
    changed = [dict(tick) for tick in ticks]
    changed[0]["price"] = "101"
    with pytest.raises(ValueError, match="shadow_run_id_conflict"):
        service.run("run-replay", changed)
    with pytest.raises(ValueError, match="shadow_run_id_conflict"):
        service.run("run-replay", ticks, fee_bps="11")


def test_run_tampering_is_rejected_by_reads_replay_and_legacy_compare(shadow):
    db, service, ticks = shadow
    result = service.run("run-tamper", ticks)
    tampered = dict(result)
    tampered["equity"] = list(result["equity"])
    tampered["equity"][-1] = "1"
    db.put(RUNS, "run-tamper", tampered, expected_version=1)
    with pytest.raises(ValueError, match="shadow_run_digest_mismatch"):
        service.get_run("run-tamper")
    with pytest.raises(ValueError, match="shadow_run_digest_mismatch"):
        service.run("run-tamper", ticks)
    with pytest.raises(ValueError, match="shadow_run_digest_mismatch"):
        service.compare("run-tamper", result["equity"])


def test_concurrent_identical_runs_create_one_sealed_record(shadow):
    db, service, ticks = shadow
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: service.run("run-race", ticks), range(8)))
    assert all(result == results[0] for result in results)
    assert len(db.list(RUNS)) == 1
    assert service.get_run("run-race") == results[0]


def test_concurrent_conflicting_run_ids_have_one_winner(shadow):
    _, service, ticks = shadow
    changed = [dict(tick) for tick in ticks]
    changed[0]["price"] = "101"

    def create(index):
        try:
            service.run("run-conflict-race", ticks if index == 0 else changed)
            return "created"
        except ValueError as exc:
            assert str(exc) == "shadow_run_id_conflict"
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(create, range(2))) == ["conflict", "created"]


def test_durable_comparison_binds_events_sources_and_exact_decimal_results(shadow):
    db, service, ticks = shadow
    run = service.run("run-compare", ticks)
    request = paper_request(run, final_delta="25")
    created = service.create_comparison("comparison-a", "alice", request)
    report = created["comparison"]
    assert created["replayed"] is False
    assert report["run_id"] == "run-compare"
    assert report["shadow_run_sha256"] == run["shadow_run_sha256"]
    assert (
        report["request"]["paper_observations"][0]["source_sha256"]
        == hashlib.sha256(f"operator-paper:10:{run['initial_equity']}".encode()).hexdigest()
    )
    assert report["rows"][-1]["difference"] == "-25"
    assert report["final_difference"] == "-25"
    assert report["max_absolute_difference"] == "25"
    assert Decimal(report["paper_pnl"]) == Decimal(report["shadow_pnl"]) + 25
    assert report["paper_evidence"] == "operator_supplied"
    assert report["independent_paper_verification"] is False
    assert report["execution_authorized"] is report["live_execution"] is False
    restarted = ShadowService(Database(db.path))
    assert restarted.get_comparison("comparison-a") == report
    assert restarted.get_comparison("missing") is None


@pytest.mark.parametrize(
    "change,error",
    [
        ({"extra": True}, "invalid_shadow_comparison_payload"),
        ({"comparison_as_of_ms": -1}, "invalid_comparison_time"),
        ({"paper_observations": []}, "invalid_paper_observations"),
    ],
)
def test_comparison_envelope_and_collection_validation(shadow, change, error):
    _, service, ticks = shadow
    run = service.run("run-invalid", ticks)
    with pytest.raises(ValueError, match=error):
        service.create_comparison("comparison-invalid", "alice", {**paper_request(run), **change})


@pytest.mark.parametrize("field", ["available_ms", "source_sha256", "equity"])
def test_comparison_observation_validation(shadow, field):
    _, service, ticks = shadow
    run = service.run(f"run-invalid-{field}", ticks)
    request = paper_request(run)
    if field == "available_ms":
        request["paper_observations"][0][field] = 9
    elif field == "source_sha256":
        request["paper_observations"][0][field] = "A" * 64
    else:
        request["paper_observations"][0][field] = "NaN"
    with pytest.raises(ValueError, match="invalid_paper_observations"):
        service.create_comparison("comparison-invalid-observation", "alice", request)


def test_comparison_requires_run_alignment_availability_and_matching_initial_cash(shadow):
    _, service, ticks = shadow
    run = service.run("run-alignment", ticks)
    with pytest.raises(ValueError, match="shadow_run_not_found"):
        service.create_comparison(
            "comparison-missing-run", "alice", {**paper_request(run), "run_id": "missing"}
        )
    misaligned = paper_request(run)
    misaligned["paper_observations"][0]["event_ms"] = 11
    with pytest.raises(ValueError, match="paper_event_alignment_mismatch"):
        service.create_comparison("comparison-misaligned", "alice", misaligned)
    unavailable = paper_request(run)
    unavailable["paper_observations"][-1]["available_ms"] = 100
    with pytest.raises(ValueError, match="invalid_paper_observations"):
        service.create_comparison("comparison-unavailable", "alice", unavailable)
    mismatched = paper_request(run)
    mismatched["paper_observations"][0]["equity"] = "9999"
    with pytest.raises(ValueError, match="paper_initial_equity_mismatch"):
        service.create_comparison("comparison-initial", "alice", mismatched)


def test_comparison_replay_conflicts_and_tampering_fail_closed(shadow):
    db, service, ticks = shadow
    run = service.run("run-comparison-replay", ticks)
    request = paper_request(run)
    created = service.create_comparison("comparison-replay", "alice", request)
    assert service.create_comparison("comparison-replay", "alice", request) == {
        "comparison": created["comparison"],
        "replayed": True,
        "live_execution": False,
    }
    with pytest.raises(ValueError, match="shadow_comparison_id_conflict"):
        service.create_comparison("comparison-replay", "bob", request)
    with pytest.raises(ValueError, match="shadow_comparison_id_conflict"):
        service.create_comparison("comparison-replay", "alice", paper_request(run, final_delta="1"))
    tampered = dict(created["comparison"])
    tampered["paper_pnl"] = "0"
    db.put(COMPARISONS, "comparison-replay", tampered, expected_version=1)
    with pytest.raises(ValueError, match="shadow_comparison_digest_mismatch"):
        service.get_comparison("comparison-replay")


def test_concurrent_identical_comparisons_create_one_record(shadow):
    db, service, ticks = shadow
    run = service.run("run-comparison-race", ticks)
    request = paper_request(run)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(
                lambda _: service.create_comparison("comparison-race", "alice", request),
                range(8),
            )
        )
    assert [result["replayed"] for result in results].count(False) == 1
    assert [result["replayed"] for result in results].count(True) == 7
    assert len(db.list(COMPARISONS)) == 1


def test_legacy_stateless_comparison_remains_compatible(shadow):
    _, service, ticks = shadow
    run = service.run("run-legacy", ticks)
    comparison = service.compare("run-legacy", run["equity"])
    assert comparison["run_id"] == "run-legacy"
    assert [Decimal(value) for value in comparison["equity_differences"]] == [0, 0, 0, 0]
    assert comparison["max_absolute_difference"] == "0"
    assert comparison["execution_authorized"] is False
    with pytest.raises(ValueError, match="aligned_paper_equity_required"):
        service.compare("run-legacy", run["equity"][:-1])


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("OSIRIS_CONTROL_DB", str(tmp_path / "api.db"))
    monkeypatch.setenv("OSIRIS_CONTROL_MASTER_KEY", base64.b64encode(b"k" * 32).decode())
    monkeypatch.setenv(
        "OSIRIS_ADMIN_TOKENS",
        json.dumps({"analyst": "fixture-analyst-token", "viewer": "fixture-viewer-token"}),
    )
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        yield client


def analyst_session(api):
    header = {"Authorization": "Bearer fixture-analyst-token"}
    enrollment = api.post("/control-plane/auth/enroll", headers=header)
    assert enrollment.status_code == 200
    login = api.post(
        "/control-plane/auth/login",
        headers=header,
        json={"code": totp(enrollment.json()["secret"], int(time.time() // 30))},
    )
    assert login.status_code == 200
    return {"Authorization": "Session " + login.json()["session_token"]}


def test_shadow_api_requires_mfa_and_exposes_verified_reports(api):
    db = Database(os.environ["OSIRIS_CONTROL_DB"])
    service = ShadowService(db)
    ticks = [
        {"event_ms": 10, "price": "100", "target": 1},
        {"event_ms": 20, "price": "100", "target": 1},
        {"event_ms": 30, "price": "110", "target": 0},
        {"event_ms": 40, "price": "110", "target": 0},
    ]
    run = service.run("run-api", ticks)
    path = "/control-plane/shadow/comparisons"
    envelope = {"id": "comparison-api", "payload": paper_request(run)}
    analyst = {"Authorization": "Bearer fixture-analyst-token"}
    viewer = {"Authorization": "Bearer fixture-viewer-token"}
    assert api.post(path, json=envelope).status_code == 401
    assert api.post(path, json=envelope, headers=analyst).status_code == 403
    session = analyst_session(api)
    created = api.post(path, json=envelope, headers=session)
    assert created.status_code == 200, created.text
    report = created.json()["comparison"]
    assert report["live_execution"] is False
    assert api.post(path, json=envelope, headers=session).json()["replayed"] is True
    assert api.get("/control-plane/shadow/runs/run-api", headers=viewer).status_code == 403
    assert api.get("/control-plane/shadow/runs/run-api", headers=analyst).json() == run
    assert api.get("/control-plane/shadow/runs/missing", headers=analyst).status_code == 404
    comparison_path = "/control-plane/shadow/comparisons/comparison-api"
    assert api.get(comparison_path, headers=viewer).status_code == 403
    assert api.get(comparison_path, headers=analyst).json() == report
    assert api.get("/control-plane/shadow/comparisons/missing", headers=analyst).status_code == 404
    legacy = api.post(
        "/control-plane/shadow/run-api/compare",
        headers=analyst,
        json={"paper_equity": run["equity"]},
    )
    assert legacy.status_code == 200
    assert legacy.json()["max_absolute_difference"] == "0"
    assert "fixture-analyst-token" not in created.text
