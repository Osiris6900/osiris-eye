"""Immutable dataset lineage and shared training/serving vector evidence."""

import base64
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from osiris_api.control_plane.api import router
from osiris_api.control_plane.database import Database, digest
from osiris_api.control_plane.features import SNAPSHOTS, FeatureStore
from osiris_api.control_plane.identity import totp
from osiris_api.control_plane.models import ModelService


@pytest.fixture
def store(tmp_path):
    return FeatureStore(Database(str(tmp_path / "features.db")))


def put(store, entity, feature, event_ms, available_ms, value, source="fixture"):
    return store.put(
        feature,
        entity,
        event_ms,
        available_ms,
        value,
        hashlib.sha256(
            f"{source}:{entity}:{feature}:{event_ms}:{available_ms}".encode()
        ).hexdigest(),
    )


def definition(joins=None, names=None):
    return {
        "feature_names": ["price", "volume"] if names is None else names,
        "joins": [{"entity": "BTC", "as_of_ms": 20}] if joins is None else joins,
    }


def test_snapshot_binds_event_time_availability_values_and_sources(store):
    put(store, "BTC", "price", 10, 10, 100.0, "initial")
    put(store, "BTC", "price", 15, 30, 999.0, "future")
    put(store, "BTC", "volume", 12, 12, 2.0)
    put(store, "ETH", "price", 11, 19, 200.0)
    put(store, "ETH", "volume", 13, 20, 3.0)
    joins = [{"entity": "ETH", "as_of_ms": 20}, {"entity": "BTC", "as_of_ms": 20}]

    result = store.create_snapshot("dataset-a", "alice", definition(joins))
    snapshot = result["snapshot"]
    assert result["replayed"] is False
    assert snapshot["joins"] == list(reversed(joins))
    assert [row["entity"] for row in snapshot["rows"]] == ["BTC", "ETH"]
    assert snapshot["rows"][0]["values"] == [100.0, 2.0]
    assert snapshot["rows"][0]["features"]["price"]["event_ms"] == 10
    assert snapshot["rows"][1]["values"] == [200.0, 3.0]
    assert snapshot["dataset_sha256"] == digest(
        {"feature_names": ["price", "volume"], "rows": snapshot["rows"]}
    )
    for row in snapshot["rows"]:
        assert row["feature_vector_sha256"] == digest(
            {
                "feature_names": row["feature_names"],
                "values": row["values"],
                "features": row["features"],
            }
        )
        for feature in row["features"].values():
            assert feature["event_ms"] <= feature["available_ms"] <= row["as_of_ms"]
            assert len(feature["source_hash"]) == 64
    assert snapshot["live_execution"] is snapshot["independent_model_approval"] is False
    assert snapshot["time_provenance_independently_verified"] is False
    replay = store.create_snapshot("dataset-a", "alice", definition(list(reversed(joins))))
    assert replay["replayed"] is True
    assert replay["snapshot"] == snapshot


def test_snapshot_is_stable_after_a_later_backfill_and_replay_is_original(store):
    put(store, "BTC", "price", 10, 10, 100.0, "initial")
    put(store, "BTC", "volume", 10, 10, 2.0)
    request = definition()
    original = store.create_snapshot("dataset-original", "alice", request)["snapshot"]

    put(store, "BTC", "price", 15, 20, 150.0, "backfill")
    assert store.vector("BTC", request["feature_names"], 20)["values"] == [150.0, 2.0]
    replay = store.create_snapshot(
        "dataset-original",
        "alice",
        {"feature_names": request["feature_names"], "joins": request["joins"]},
    )
    assert replay["replayed"] is True
    assert replay["snapshot"] == original
    assert replay["snapshot"]["rows"][0]["values"] == [100.0, 2.0]
    revised = store.create_snapshot("dataset-revised", "alice", request)["snapshot"]
    assert revised["rows"][0]["values"] == [150.0, 2.0]
    assert revised["dataset_sha256"] != original["dataset_sha256"]
    assert store.get_snapshot("dataset-original") == original


def test_missing_or_unavailable_feature_aborts_without_a_snapshot(store):
    put(store, "BTC", "price", 10, 30, 100.0)
    with pytest.raises(ValueError, match="point_in_time_feature_unavailable"):
        store.create_snapshot("dataset-missing", "alice", definition())
    assert store.get_snapshot("dataset-missing") is None
    assert store.db.list(SNAPSHOTS) == []


def test_snapshot_id_conflicts_include_actor_and_normalized_definition(store):
    put(store, "BTC", "price", 10, 10, 100.0)
    put(store, "BTC", "volume", 10, 10, 2.0)
    store.create_snapshot("dataset-a", "alice", definition())
    with pytest.raises(ValueError, match="feature_snapshot_id_conflict"):
        store.create_snapshot("dataset-a", "bob", definition())
    with pytest.raises(ValueError, match="feature_snapshot_id_conflict"):
        store.create_snapshot("dataset-a", "alice", definition(names=["volume", "price"]))
    with pytest.raises(ValueError, match="feature_snapshot_id_conflict"):
        store.create_snapshot("dataset-a", "alice", definition([{"entity": "BTC", "as_of_ms": 21}]))


@pytest.mark.parametrize(
    "payload,error",
    [
        ({}, "invalid_snapshot_payload"),
        ({"feature_names": ["price"], "joins": [], "actor": "forged"}, "invalid_snapshot_payload"),
        (definition([]), "invalid_snapshot_joins"),
        (definition([{"entity": "BTC", "as_of_ms": True}]), "invalid_snapshot_joins"),
        (definition([{"entity": "BTC", "as_of_ms": 20, "extra": 1}]), "invalid_snapshot_joins"),
        (definition(names=[]), "invalid_feature_query"),
        (definition(names=["price", "price"]), "invalid_feature_query"),
        (
            definition([{"entity": "BTC", "as_of_ms": 20}, {"entity": "BTC", "as_of_ms": 20}]),
            "duplicate_snapshot_join",
        ),
    ],
)
def test_invalid_snapshot_definitions_fail_closed(store, payload, error):
    with pytest.raises(ValueError, match=error):
        store.create_snapshot("dataset-invalid", "alice", payload)
    assert store.db.list(SNAPSHOTS) == []


def test_snapshot_survives_restart_and_detects_tampering(store):
    put(store, "BTC", "price", 10, 10, 100.0)
    put(store, "BTC", "volume", 10, 10, 2.0)
    snapshot = store.create_snapshot("dataset-restart", "alice", definition())["snapshot"]
    restarted = FeatureStore(Database(store.db.path))
    assert restarted.get_snapshot("dataset-restart") == snapshot

    tampered = dict(snapshot)
    tampered["rows"] = [dict(row) for row in snapshot["rows"]]
    tampered["rows"][0]["values"] = [999.0, 2.0]
    restarted.db.put(SNAPSHOTS, "dataset-restart", tampered, expected_version=1)
    with pytest.raises(ValueError, match="feature_snapshot_digest_mismatch"):
        restarted.get_snapshot("dataset-restart")


def test_concurrent_identical_snapshots_create_one_immutable_record(store):
    put(store, "BTC", "price", 10, 10, 100.0)
    put(store, "BTC", "volume", 10, 10, 2.0)

    def create(_):
        return store.create_snapshot("dataset-concurrent", "alice", definition())["replayed"]

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(create, range(8)))
    assert results.count(False) == 1
    assert results.count(True) == 7
    assert len(store.db.list(SNAPSHOTS)) == 1


def test_concurrent_conflicting_snapshot_ids_have_one_winner(store):
    put(store, "BTC", "price", 10, 10, 100.0)
    put(store, "BTC", "volume", 10, 10, 2.0)

    def create(as_of_ms):
        try:
            store.create_snapshot(
                "dataset-race",
                "alice",
                definition([{"entity": "BTC", "as_of_ms": as_of_ms}]),
            )
            return as_of_ms
        except ValueError as exc:
            assert str(exc) == "feature_snapshot_id_conflict"
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(create, [20, 21]))
    assert sorted(results, key=str) in ([20, "conflict"], [21, "conflict"])
    assert store.get_snapshot("dataset-race")["joins"][0]["as_of_ms"] in {20, 21}


def test_model_serving_and_snapshot_use_the_same_ordered_vector(store):
    rows = [
        {
            "event_ms": index + 1,
            "available_ms": index + 1,
            "label_available_ms": index + 1,
            "features": [float(index), float(2 * index)],
            "target": float(3 * index),
        }
        for index in range(12)
    ]
    models = ModelService(store.db)
    models.train("model-ordered", rows, feature_names=["second", "first"])
    put(store, "BTC", "first", 20, 20, 50.0)
    put(store, "BTC", "second", 20, 20, 100.0)

    vector = store.vector("BTC", ["second", "first"], 20)
    assert vector["values"] == [100.0, 50.0]
    served = models.predict_from_store("model-ordered", "BTC", 20)
    assert served["prediction"] == models.predict("model-ordered", [100.0, 50.0])
    assert served["feature_vector_sha256"] == vector["feature_vector_sha256"]
    snapshot = store.create_snapshot(
        "dataset-model", "alice", definition(names=["second", "first"])
    )["snapshot"]
    assert snapshot["rows"][0]["values"] == vector["values"]
    assert snapshot["rows"][0]["feature_vector_sha256"] == served["feature_vector_sha256"]
    assert models.predict("model-ordered", snapshot["rows"][0]["values"]) == served["prediction"]


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


def test_snapshot_api_requires_mfa_and_research_read_scope(api):
    import os

    store = FeatureStore(Database(os.environ["OSIRIS_CONTROL_DB"]))
    put(store, "BTC", "price", 10, 10, 100.0)
    put(store, "BTC", "volume", 10, 10, 2.0)
    path = "/control-plane/features/snapshots"
    envelope = {"id": "dataset-api", "payload": definition()}
    analyst = {"Authorization": "Bearer fixture-analyst-token"}
    viewer = {"Authorization": "Bearer fixture-viewer-token"}
    assert api.post(path, json=envelope).status_code == 401
    assert api.post(path, json=envelope, headers=analyst).status_code == 403

    session = analyst_session(api)
    created = api.post(path, json=envelope, headers=session)
    assert created.status_code == 200, created.text
    snapshot = created.json()["snapshot"]
    assert created.json()["replayed"] is False
    assert snapshot["rows"][0]["values"] == [100.0, 2.0]
    assert snapshot["live_execution"] is False

    read_path = path + "/dataset-api"
    assert api.get(read_path).status_code == 401
    assert api.get(read_path, headers=viewer).status_code == 403
    exported = api.get(read_path, headers=analyst)
    assert exported.status_code == 200
    assert exported.json() == snapshot
    assert api.get(path + "/missing", headers=analyst).status_code == 404
    assert api.post(path, json=envelope, headers=session).json()["replayed"] is True
    forged = {**envelope, "actor": "forged"}
    assert api.post(path, json=forged, headers=session).status_code == 422
    assert "fixture-analyst-token" not in created.text + exported.text
