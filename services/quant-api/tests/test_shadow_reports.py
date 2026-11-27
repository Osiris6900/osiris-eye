"""Report listings verify the same sealed shadow snapshots as individual reads."""

import copy
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from osiris_api.control_plane.api import router
from osiris_api.control_plane.database import Database, digest
from osiris_api.control_plane.shadow import RUNS, ShadowService


@pytest.fixture
def reports(tmp_path, monkeypatch):
    path = str(tmp_path / "reports.db")
    monkeypatch.setenv("OSIRIS_CONTROL_DB", path)
    monkeypatch.setenv("OSIRIS_ADMIN_TOKENS", json.dumps({"analyst": "fixture-analyst-token"}))
    database = Database(path)
    service = ShadowService(database)
    legacy = json.loads((Path(__file__).parent / "fixtures" / "shadow_v1_394.json").read_text())
    database.put(RUNS, "legacy-run", legacy["run"])
    current = service.run(
        "current-run",
        [
            {"event_ms": 10, "price": "3", "target": 1},
            {"event_ms": 20, "price": "3", "target": 0},
            {"event_ms": 30, "price": "4", "target": 0},
        ],
    )
    # Listing metadata must remain attached to the exact body being verified.
    database.put(RUNS, "current-run", current, expected_version=1)
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        yield database, service, client


AUTH = {"Authorization": "Bearer fixture-analyst-token"}


def test_reports_preserve_verified_legacy_and_current_run_snapshots(reports):
    database, service, client = reports
    expected = database.list(RUNS, limit=20)
    response = client.get("/control-plane/reports", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["shadow_runs"] == expected
    assert [(row["id"], row["version"]) for row in body["shadow_runs"]] == [
        ("current-run", 2),
        ("legacy-run", 1),
    ]
    for row in body["shadow_runs"]:
        assert set(row) == {"id", "version", "value"}
        assert row["value"] == service.get_run(row["id"])
    assert body["role"] == "analyst"
    assert body["live_execution"] is False
    assert client.get("/control-plane/reports").status_code == 401


@pytest.mark.parametrize("run_id", ["legacy-run", "current-run"])
@pytest.mark.parametrize("reseal", [False, True])
def test_reports_fail_closed_on_corrupt_shadow_evidence(reports, run_id, reseal):
    database, _, client = reports
    stored = database.get(RUNS, run_id)
    corrupted = copy.deepcopy(stored["value"])
    corrupted["pnl"] = "999999999"
    if reseal:
        corrupted["shadow_run_sha256"] = digest(
            {key: value for key, value in corrupted.items() if key != "shadow_run_sha256"}
        )
    database.put(RUNS, run_id, corrupted, expected_version=stored["version"])
    error = "invalid_shadow_run" if reseal else "shadow_run_digest_mismatch"
    detail = client.get(f"/control-plane/shadow/runs/{run_id}", headers=AUTH)
    assert detail.status_code == 422
    assert detail.json() == {"detail": error}
    response = client.get("/control-plane/reports", headers=AUTH)
    assert response.status_code == 422
    assert response.json() == {"detail": error}


def test_verified_listing_keeps_body_and_version_from_one_snapshot(reports, monkeypatch):
    database, service, _ = reports
    original_list = database.list
    expected = original_list(RUNS, limit=20)

    def update_after_listing(collection, *, limit):
        listed = original_list(collection, limit=limit)
        changed = copy.deepcopy(listed[0]["value"])
        changed["pnl"] = "999999999"
        database.put(collection, listed[0]["id"], changed, expected_version=listed[0]["version"])
        return listed

    monkeypatch.setattr(database, "list", update_after_listing)
    assert service.list_runs(limit=20) == expected
    assert database.get(RUNS, "current-run")["version"] == 3
    with pytest.raises(ValueError, match="^shadow_run_digest_mismatch$"):
        service.get_run("current-run")
