"""Deterministic drift, calibration, quarantine and API safety evidence."""

import base64
import hashlib
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from osiris_api.control_plane.api import router
from osiris_api.control_plane.database import Database
from osiris_api.control_plane.drift import QUARANTINES, REPORTS, ModelDriftMonitor
from osiris_api.control_plane.features import FeatureStore
from osiris_api.control_plane.identity import totp
from osiris_api.control_plane.models import ModelService


@pytest.fixture
def monitored(tmp_path):
    db = Database(str(tmp_path / "drift.db"))
    store = FeatureStore(db)
    rows = [
        {
            "event_ms": index + 1,
            "available_ms": index + 1,
            "label_available_ms": index + 1,
            "features": [float(index)],
            "target": float(2 * index),
        }
        for index in range(12)
    ]
    ModelService(db).train("model-a", rows, feature_names=["value"])
    return db, store, ModelDriftMonitor(db)


def make_snapshot(store, snapshot_id, values, start_ms):
    joins = []
    for index, value in enumerate(values):
        stamp = start_ms + index
        store.put(
            "value",
            "BTC",
            stamp,
            stamp,
            float(value),
            hashlib.sha256(f"{snapshot_id}:{stamp}".encode()).hexdigest(),
        )
        joins.append({"entity": "BTC", "as_of_ms": stamp})
    return store.create_snapshot(
        snapshot_id, "alice", {"feature_names": ["value"], "joins": joins}
    )["snapshot"]


def request(candidate, baseline="baseline-a", evaluation_as_of_ms=1000, error=0.0):
    return {
        "model_id": "model-a",
        "baseline_snapshot_id": baseline,
        "candidate_snapshot_id": candidate["snapshot_id"],
        "candidate_labels": [
            {"available_ms": row["as_of_ms"] + 1, "value": 2 * row["values"][0] + error}
            for row in candidate["rows"]
        ],
        "evaluation_as_of_ms": evaluation_as_of_ms,
    }


def healthy_pair(store):
    baseline = make_snapshot(store, "baseline-a", range(8), 100)
    candidate = make_snapshot(store, "candidate-a", range(8), 200)
    return baseline, candidate


def test_healthy_report_compares_drift_and_training_baselines(monitored):
    db, store, monitor = monitored
    _, candidate = healthy_pair(store)
    result = monitor.evaluate("report-healthy", "alice", request(candidate))
    report = result["report"]
    assert result["replayed"] is False
    assert report["baseline_rows"] == report["candidate_rows"] == 8
    assert report["alerts"] == []
    assert report["quarantined"] is False
    assert report["quarantine"] is None
    assert report["input_drift"][0]["feature"] == "value"
    assert report["output_drift"]["psi"] >= 0
    assert report["calibration"]["candidate_mse"] >= 0
    assert report["calibration"]["model_holdout_mse"] >= 0
    assert report["calibration"]["model_baseline_mse"] > 0
    assert report["calibration"]["decayed"] is False
    assert report["diagnostic_only"] is True
    assert report["live_execution"] is report["independent_model_approval"] is False
    assert ModelService(db).predict("model-a", [3.0]) == pytest.approx(6.0)
    assert monitor.quarantine("model-a") is None


def test_input_output_drift_creates_persistent_quarantine_and_blocks_prediction(monitored):
    db, store, monitor = monitored
    healthy_pair(store)
    candidate = make_snapshot(store, "candidate-drift", [0, 0, 0, 0, 11, 11, 11, 11], 300)
    report = monitor.evaluate("report-drift", "alice", request(candidate))["report"]
    codes = [alert["code"] for alert in report["alerts"]]
    assert codes == ["input_drift", "output_drift"]
    assert report["input_drift"][0]["psi"] > report["thresholds"]["psi"]
    assert report["output_drift"]["drifted"] is True
    assert report["calibration"]["decayed"] is False
    assert report["quarantined"] is True
    quarantine = report["quarantine"]
    assert quarantine["active"] is quarantine["independent_review_required"] is True
    assert quarantine["report_id"] == "report-drift"
    assert quarantine["live_execution"] is False
    with pytest.raises(ValueError, match="model_quarantined"):
        ModelService(db).predict("model-a", [3.0])
    with pytest.raises(ValueError, match="model_quarantined"):
        ModelService(db).predict_from_store("model-a", "BTC", 307)
    restarted = ModelDriftMonitor(Database(db.path))
    assert restarted.get("report-drift") == report
    assert restarted.quarantine("model-a") == quarantine


def test_calibration_decay_alerts_and_later_healthy_report_does_not_release(monitored):
    _, store, monitor = monitored
    _, healthy = healthy_pair(store)
    decayed = monitor.evaluate("report-decay", "alice", request(healthy, error=100.0))["report"]
    assert [alert["code"] for alert in decayed["alerts"]] == ["calibration_decay"]
    assert decayed["calibration"]["candidate_to_holdout_ratio"] > 2.0
    assert decayed["quarantined"] is True

    later = make_snapshot(store, "candidate-later", range(8), 400)
    report = monitor.evaluate("report-later", "alice", request(later))["report"]
    assert report["alerts"] == []
    assert report["quarantined"] is True
    assert report["quarantine"]["report_id"] == "report-decay"
    assert monitor.quarantine("model-a")["report_id"] == "report-decay"


def test_replay_is_original_and_id_or_actor_changes_fail_closed(monitored):
    _, store, monitor = monitored
    _, candidate = healthy_pair(store)
    original = monitor.evaluate("report-a", "alice", request(candidate))["report"]
    replay = monitor.evaluate("report-a", "alice", request(candidate))
    assert replay == {"report": original, "replayed": True, "live_execution": False}
    with pytest.raises(ValueError, match="model_drift_report_id_conflict"):
        monitor.evaluate("report-a", "bob", request(candidate))
    with pytest.raises(ValueError, match="model_drift_report_id_conflict"):
        monitor.evaluate("report-a", "alice", request(candidate, error=1.0))
    assert len(monitor.db.list(REPORTS)) == 1


@pytest.mark.parametrize(
    "change,error",
    [
        ({"candidate_labels": []}, "invalid_drift_labels"),
        ({"evaluation_as_of_ms": True}, "invalid_drift_evaluation_time"),
        ({"evaluation_as_of_ms": -1}, "invalid_drift_evaluation_time"),
        ({"baseline_snapshot_id": "candidate-a"}, "distinct_drift_snapshots_required"),
        ({"candidate_snapshot_id": "missing"}, "drift_snapshot_not_found"),
        ({"model_id": "missing"}, "model_not_found"),
        ({"extra": True}, "invalid_drift_payload"),
    ],
)
def test_invalid_or_unavailable_monitoring_inputs_fail_closed(monitored, change, error):
    _, store, monitor = monitored
    _, candidate = healthy_pair(store)
    payload = {**request(candidate), **change}
    with pytest.raises(ValueError, match=error):
        monitor.evaluate("report-invalid", "alice", payload)
    assert monitor.get("report-invalid") is None


def test_label_count_time_and_snapshot_feature_order_are_enforced(monitored):
    db, store, monitor = monitored
    _, candidate = healthy_pair(store)
    with pytest.raises(ValueError, match="drift_label_count_mismatch"):
        monitor.evaluate(
            "report-count",
            "alice",
            {
                **request(candidate),
                "candidate_labels": request(candidate)["candidate_labels"]
                + [{"available_ms": 300, "value": 0.0}],
            },
        )
    payload = request(candidate)
    payload["candidate_labels"][0]["available_ms"] = 1001
    with pytest.raises(ValueError, match="drift_label_not_available_at_evaluation"):
        monitor.evaluate("report-label-time", "alice", payload)
    with pytest.raises(ValueError, match="drift_snapshot_not_available_at_evaluation"):
        monitor.evaluate("report-eval-time", "alice", request(candidate, evaluation_as_of_ms=100))

    ModelService(db).train(
        "model-b",
        [
            {
                "event_ms": index + 1,
                "available_ms": index + 1,
                "label_available_ms": index + 1,
                "features": [float(index), float(index)],
                "target": float(index),
            }
            for index in range(12)
        ],
        feature_names=["value", "other"],
    )
    with pytest.raises(ValueError, match="drift_snapshot_feature_order_mismatch"):
        monitor.evaluate("report-order", "alice", {**request(candidate), "model_id": "model-b"})


def test_report_and_quarantine_tampering_fail_closed(monitored):
    _, store, monitor = monitored
    healthy_pair(store)
    drifted = make_snapshot(store, "candidate-tamper", range(1000, 1008), 300)
    report = monitor.evaluate("report-tamper", "alice", request(drifted))["report"]
    tampered = dict(report)
    tampered["alerts"] = []
    monitor.db.put(REPORTS, "report-tamper", tampered, expected_version=1)
    with pytest.raises(ValueError, match="model_drift_report_digest_mismatch"):
        monitor.get("report-tamper")

    quarantine = dict(monitor.quarantine("model-a"))
    quarantine["active"] = False
    monitor.db.put(QUARANTINES, "model-a", quarantine, expected_version=1)
    with pytest.raises(ValueError, match="model_quarantine_digest_mismatch"):
        monitor.quarantine("model-a")


def test_concurrent_identical_reports_create_one_record(monitored):
    _, store, monitor = monitored
    _, candidate = healthy_pair(store)

    def evaluate(_):
        return monitor.evaluate("report-concurrent", "alice", request(candidate))["replayed"]

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(evaluate, range(8)))
    assert results.count(False) == 1
    assert results.count(True) == 7
    assert len(monitor.db.list(REPORTS)) == 1


def test_concurrent_conflicting_report_ids_have_one_winner(monitored):
    _, store, monitor = monitored
    _, candidate = healthy_pair(store)

    def evaluate(error):
        try:
            monitor.evaluate("report-race", "alice", request(candidate, error=error))
            return error
        except ValueError as exc:
            assert str(exc) == "model_drift_report_id_conflict"
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(evaluate, [0.0, 100.0]))
    assert sorted(results, key=str) in ([0.0, "conflict"], [100.0, "conflict"])
    assert monitor.get("report-race")["request"]["candidate_labels"][0]["value"] in {0.0, 100.0}


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


def test_drift_api_requires_mfa_and_exposes_verified_quarantine(api):
    db = Database(os.environ["OSIRIS_CONTROL_DB"])
    store = FeatureStore(db)
    rows = [
        {
            "event_ms": index + 1,
            "available_ms": index + 1,
            "label_available_ms": index + 1,
            "features": [float(index)],
            "target": float(2 * index),
        }
        for index in range(12)
    ]
    ModelService(db).train("model-a", rows, feature_names=["value"])
    healthy_pair(store)
    candidate = make_snapshot(store, "candidate-api", range(1000, 1008), 300)
    path = "/control-plane/model-drift/evaluate"
    envelope = {"id": "report-api", "payload": request(candidate)}
    analyst = {"Authorization": "Bearer fixture-analyst-token"}
    viewer = {"Authorization": "Bearer fixture-viewer-token"}
    assert api.post(path, json=envelope).status_code == 401
    assert api.post(path, json=envelope, headers=analyst).status_code == 403

    session = analyst_session(api)
    created = api.post(path, json=envelope, headers=session)
    assert created.status_code == 200, created.text
    report = created.json()["report"]
    assert report["quarantined"] is True
    assert report["live_execution"] is False
    report_path = "/control-plane/model-drift/reports/report-api"
    quarantine_path = "/control-plane/model-drift/quarantines/model-a"
    assert api.get(report_path, headers=viewer).status_code == 403
    assert api.get(report_path, headers=analyst).json() == report
    assert api.get(quarantine_path, headers=analyst).json() == report["quarantine"]
    assert api.get("/control-plane/model-drift/reports/missing", headers=analyst).status_code == 404
    assert api.post(path, json=envelope, headers=session).json()["replayed"] is True
    prediction = api.post(
        "/control-plane/models/model-a/predict",
        json={"features": [3.0]},
        headers=analyst,
    )
    assert prediction.status_code == 422
    assert prediction.json()["detail"] == "model_quarantined"
    assert "fixture-analyst-token" not in created.text
