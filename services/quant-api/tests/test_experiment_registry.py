"""Reproducible research IDs bind real backtest inputs, source and result bytes."""
import base64
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from osiris_api.control_plane import experiments
from osiris_api.control_plane.api import router
from osiris_api.control_plane.database import Database, digest
from osiris_api.control_plane.experiments import COLLECTION, ExperimentRegistry
from osiris_api.control_plane.identity import totp

INPUTS = {"signals": [0, 1, 0], "asset_returns": [0, 0.1, -0.1],
          "cost_bps_per_unit_turnover": 10.0}


@pytest.fixture
def registry(tmp_path):
    return ExperimentRegistry(Database(str(tmp_path / "experiments.db")),
                              clock=lambda: datetime(2026, 11, 12, tzinfo=UTC))


def test_actual_lagged_costed_result_has_complete_reproducibility_metadata(registry):
    created = registry.run("experiment-a", "alice", INPUTS)
    record = created["experiment"]
    assert created["replayed"] is False
    assert record["status"] == "completed_research"
    assert record["result"]["strategy_returns"] == [0.0, -0.101]
    assert record["result"]["signal_alignment"] == "signal[t-1]_applied_to_return[t]"
    assert record["manifest"]["parameters"] == {"cost_bps_per_unit_turnover": 10.0}
    assert record["manifest"]["data_sha256"] == digest({k: INPUTS[k] for k in ("signals", "asset_returns")})
    assert all(len(value) == 64 for value in record["manifest"]["code_files"].values())
    assert all(not name.startswith("/") for name in record["manifest"]["code_files"])
    assert record["result_sha256"] == digest(record["result"])
    assert record["live_execution"] is False
    assert record["deployment_authorized"] is False
    reproduced = registry.reproduce("experiment-a")
    assert reproduced["reproduced"] is True
    assert reproduced["expected_result_sha256"] == reproduced["actual_result_sha256"]


def test_identical_retry_recovers_the_original_record_without_reexecuting(registry, monkeypatch):
    first = registry.run("experiment-a", "alice", INPUTS)
    monkeypatch.setattr(experiments, "execute_tool", lambda *args: pytest.fail("retry executed again"))
    second = registry.run("experiment-a", "alice", INPUTS)
    assert second == {"experiment": first["experiment"], "replayed": True}


def test_new_database_object_reads_the_same_immutable_record_and_hashes(registry):
    first = registry.run("experiment-a", "alice", INPUTS)
    restarted = ExperimentRegistry(Database(registry.db.path))
    assert restarted.get("experiment-a") == first["experiment"]
    assert restarted.reproduce("experiment-a")["reproduced"] is True
    assert restarted.list()[0]["experiment_id"] == "experiment-a"


@pytest.mark.parametrize("change", ["data", "parameters", "requester"])
def test_same_id_cannot_overwrite_another_data_parameter_or_requester_contract(registry, change):
    first = registry.run("experiment-a", "alice", INPUTS)["experiment"]
    payload, actor = {**INPUTS}, "alice"
    if change == "data":
        payload["asset_returns"] = [0, 0.1, -0.2]
    elif change == "parameters":
        payload["cost_bps_per_unit_turnover"] = 20.0
    else:
        actor = "bob"
    with pytest.raises(ValueError, match="experiment_id_conflict"):
        registry.run("experiment-a", actor, payload)
    assert registry.get("experiment-a") == first


@pytest.mark.parametrize("change", ["source", "runtime"])
def test_changed_source_or_runtime_requires_a_new_id_and_does_not_claim_reproduction(registry, monkeypatch, change):
    registry.run("experiment-a", "alice", INPUTS)
    if change == "source":
        original = experiments.code_manifest()
        monkeypatch.setattr(experiments, "code_manifest", lambda: {**original, "backtesting.py": "a" * 64})
    else:
        original = experiments.runtime_manifest()
        monkeypatch.setattr(experiments, "runtime_manifest", lambda: {**original, "python": "changed"})
    assert registry.reproduce("experiment-a")["reproduced"] is False
    assert registry.reproduce("experiment-a")["reason"] == "code_or_runtime_changed"
    with pytest.raises(ValueError, match="experiment_id_conflict"):
        registry.run("experiment-a", "alice", INPUTS)


def test_source_change_during_execution_cannot_create_a_completed_record(registry, monkeypatch):
    execute = experiments.execute_tool
    original = experiments.code_manifest()

    def changed(*args):
        result = execute(*args)
        monkeypatch.setattr(experiments, "code_manifest", lambda: {**original, "backtesting.py": "b" * 64})
        return result

    monkeypatch.setattr(experiments, "execute_tool", changed)
    with pytest.raises(ValueError, match="changed_during_run"):
        registry.run("experiment-a", "alice", INPUTS)
    assert registry.get("experiment-a") is None


@pytest.mark.parametrize("field", ["inputs", "result", "created_at", "manifest", "deployment_authorized"])
def test_record_tampering_is_refused_on_export_and_listing(registry, field):
    registry.run("experiment-a", "alice", INPUTS)
    item = registry.db.get(COLLECTION, "experiment-a")
    item["value"][field] = True if field == "deployment_authorized" else {"changed": True}
    with registry.db.transaction() as conn:
        conn.execute("UPDATE cp_documents SET body=? WHERE collection=? AND id=?",
                     (json.dumps(item["value"]), COLLECTION, "experiment-a"))
    with pytest.raises(ValueError, match="experiment_record_tampered"):
        registry.get("experiment-a")
    with pytest.raises(ValueError, match="experiment_record_tampered"):
        registry.list()


def test_copying_a_record_under_another_id_does_not_create_a_valid_alias(registry):
    record = registry.run("experiment-a", "alice", INPUTS)["experiment"]
    registry.db.put(COLLECTION, "other-id", record)
    with pytest.raises(ValueError, match="experiment_record_tampered"):
        registry.get("other-id")


def test_concurrent_identical_requests_commit_exactly_one_immutable_record(registry):
    with ThreadPoolExecutor(max_workers=4) as pool:
        replies = list(pool.map(lambda _: registry.run("experiment-a", "alice", INPUTS), range(4)))
    assert sum(reply["replayed"] is False for reply in replies) == 1
    assert len({reply["experiment"]["receipt_sha256"] for reply in replies}) == 1
    assert len(registry.list()) == 1


@pytest.mark.parametrize("extra", ["url", "code", "broker_credentials", "command"])
def test_registry_has_no_arbitrary_code_url_or_broker_input(registry, extra):
    with pytest.raises(ValueError, match="invalid_experiment_payload"):
        registry.run("experiment-a", "alice", {**INPUTS, extra: "fixture"})
    assert registry.list() == []


@pytest.mark.parametrize("payload", [
    {"signals": [0], "asset_returns": [0]},
    {"signals": [0, 1], "asset_returns": [0, 1, 2]},
    {"signals": [0, float("nan")], "asset_returns": [0, 0.1]},
    {"signals": [0, 1], "asset_returns": [0, -1]},
    {"signals": [0] * 10001, "asset_returns": [0] * 10001},
])
def test_invalid_or_oversized_experiments_never_create_partial_records(registry, payload):
    with pytest.raises(ValueError):
        registry.run("experiment-a", "alice", payload)
    assert registry.list() == []


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("OSIRIS_CONTROL_DB", str(tmp_path / "api.db"))
    monkeypatch.setenv("OSIRIS_CONTROL_MASTER_KEY", base64.b64encode(b"f" * 32).decode())
    monkeypatch.setenv("OSIRIS_ADMIN_TOKENS", json.dumps({
        "analyst": "fixture-analyst-token", "viewer": "fixture-viewer-token",
    }))
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        yield client


def test_api_requires_mfa_for_writes_and_research_scope_for_reads(api):
    analyst = {"Authorization": "Bearer fixture-analyst-token"}
    viewer = {"Authorization": "Bearer fixture-viewer-token"}
    assert api.get("/control-plane/experiments").status_code == 401
    assert api.get("/control-plane/experiments", headers=viewer).status_code == 403
    assert api.post("/control-plane/experiments", headers=analyst,
                    json={"id": "experiment-a", "payload": INPUTS}).status_code == 403
    enrollment = api.post("/control-plane/auth/enroll", headers=analyst).json()
    login = api.post("/control-plane/auth/login", headers=analyst,
                     json={"code": totp(enrollment["secret"], int(time.time() // 30))}).json()
    session = {"Authorization": "Session " + login["session_token"]}
    created = api.post("/control-plane/experiments", headers=session,
                       json={"id": "experiment-a", "payload": INPUTS})
    assert created.status_code == 200
    exported = api.get("/control-plane/experiments/experiment-a", headers=analyst)
    assert exported.json() == created.json()["experiment"]
    assert "fixture-analyst-token" not in exported.text
    assert login["session_token"] not in exported.text
    assert api.get("/control-plane/experiments", headers=analyst).json()["experiments"][0]["experiment_id"] == "experiment-a"
    assert api.post("/control-plane/experiments/experiment-a/reproduce", headers=session).json()["reproduced"] is True
    assert api.get("/control-plane/experiments/missing", headers=analyst).status_code == 404
    assert api.get("/control-plane/experiments?limit=0", headers=analyst).status_code == 422
    assert api.post("/control-plane/experiments", headers=session,
                    json={"id": "other", "payload": INPUTS, "requester": "forged"}).status_code == 422
