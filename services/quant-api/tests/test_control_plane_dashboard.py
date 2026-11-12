"""Dashboard reads existing projections without bypassing identity or account integrity."""
import base64
import json
import sqlite3
import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from osiris_api import admin_centre, market_routes, paper_pipeline_status
from osiris_api.control_plane.api import router
from osiris_api.control_plane.database import Database
from osiris_api.control_plane.identity import totp
from osiris_api.market_gateway import MarketDataGateway
from osiris_api.paper_execution import initialize_paper_account, mark_to_market, submit_paper_order


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("OSIRIS_CONTROL_DB", str(tmp_path / "control.sqlite3"))
    monkeypatch.setenv("OSIRIS_CONTROL_MASTER_KEY", base64.b64encode(b"k" * 32).decode())
    monkeypatch.setenv("OSIRIS_ADMIN_TOKENS", json.dumps({
        "analyst": "fixture-analyst-token", "viewer": "fixture-viewer-token",
    }))
    monkeypatch.setenv("OSIRIS_PAPER_EXECUTION_DB", str(tmp_path / "paper.sqlite3"))
    monkeypatch.setenv("OSIRIS_PAPER_LEDGER_DB", str(tmp_path / "legacy.sqlite3"))
    monkeypatch.delenv("OSIRIS_ENABLE_LIVE_EXECUTION", raising=False)
    monkeypatch.delenv("OSIRIS_P2P_LOCAL_HEALTH_PORT", raising=False)
    monkeypatch.setattr(paper_pipeline_status, "_latest", None)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


ANALYST = {"Authorization": "Bearer fixture-analyst-token"}
VIEWER = {"Authorization": "Bearer fixture-viewer-token"}


def test_dashboard_requires_identity_and_configuration(api, monkeypatch):
    assert api.get("/control-plane/dashboard").status_code == 401
    assert api.get("/control-plane/dashboard", headers={"Authorization": "Bearer wrong"}).status_code == 401
    monkeypatch.delenv("OSIRIS_CONTROL_DB")
    assert api.get("/control-plane/dashboard", headers=ANALYST).status_code == 503


def test_viewer_gets_health_but_never_reads_research_or_account(api, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("research projection accessed for viewer")
    monkeypatch.setattr(admin_centre, "paper_status", forbidden)
    monkeypatch.setattr(admin_centre, "risk_status", forbidden)
    monkeypatch.setattr(paper_pipeline_status, "get_paper_pipeline_status", forbidden)
    response = api.get("/control-plane/dashboard", headers=VIEWER)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["research_access"] == "denied"
    assert data["paper"] is data["pipeline"] is data["risk"] is None
    assert data["live_trading_authorized"] is False
    assert response.headers["cache-control"] == "no-store"
    assert not admin_centre.scan_payload(data, ["fixture-viewer-token"])


def test_missing_account_is_not_created_or_replaced_with_demo_values(api, tmp_path):
    response = api.get("/control-plane/dashboard", headers=ANALYST)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["paper"]["source_ledger"] == "canonical_execution"
    assert data["paper"]["cash"] is None
    assert data["paper"]["initialized"] is False
    assert data["pipeline"]["proposal"] == "none"
    assert not (tmp_path / "paper.sqlite3").exists()
    assert not (tmp_path / "legacy.sqlite3").exists()


def test_canonical_values_match_and_corrupt_refresh_withholds_finances(api, tmp_path):
    initialize_paper_account("1000")
    now = datetime.now(UTC).isoformat()
    submit_paper_order(client_order_id="dashboard-fill", symbol="BTC-USD", side="BUY",
                       quantity="2", price="100", fee="2", observed_at=now, now=now)
    mark_to_market({"BTC-USD": "110"}, observed_at=now, now=now)
    data = api.get("/control-plane/dashboard", headers=ANALYST).json()
    assert data["paper"]["cash"] == "798"
    assert data["paper"]["pnl"] == {"realized": "0", "unrealized": "18", "equity": "1018"}
    assert data["paper"]["reconciliation"]["passed"] is True
    with sqlite3.connect(tmp_path / "paper.sqlite3") as db:
        db.execute("UPDATE paper_exec_meta SET cash='999999'")
    data = api.get("/control-plane/dashboard", headers=ANALYST).json()
    assert data["paper"]["cash"] is data["paper"]["pnl"] is data["paper"]["positions"] is None
    assert data["paper"]["reconciliation"]["passed"] is False
    assert any(flag["kind"] == "paper_reconciliation_failed" for flag in data["risk"]["flags"])


def test_mfa_session_reads_dashboard_and_logout_revokes_it(api):
    enrollment = api.post("/control-plane/auth/enroll", headers=ANALYST).json()
    code = totp(enrollment["secret"], int(time.time() // 30))
    session = api.post("/control-plane/auth/login", headers=ANALYST, json={"code": code}).json()
    header = {"Authorization": "Session " + session["session_token"]}
    response = api.get("/control-plane/dashboard", headers=header)
    assert response.status_code == 200, response.text
    assert header["Authorization"] not in response.text
    assert api.post("/control-plane/auth/logout", headers=header).status_code == 200
    assert api.get("/control-plane/dashboard", headers=header).status_code == 401


def test_secret_shaped_projection_is_never_returned(api, monkeypatch):
    monkeypatch.setattr(admin_centre, "feeds_status", lambda: {"secret": "fixture-sensitive-value"})
    response = api.get("/control-plane/dashboard", headers=VIEWER)
    assert response.status_code == 500
    assert "fixture-sensitive-value" not in response.text


def test_dashboard_reads_stored_peer_metrics_without_running_a_collection(api, tmp_path):
    report = {"captured_ms": 1, "report": {"schema": "osiris.peer-metrics.v1", "peers": []},
              "independent_host_provenance_verified": False}
    Database(str(tmp_path / "control.sqlite3")).put("peer-telemetry", "latest", report)
    response = api.get("/control-plane/dashboard", headers=VIEWER)
    assert response.status_code == 200, response.text
    assert response.json()["peer_metrics"] == report


class FixtureBars:
    name, mode = "fixture", "synthetic"

    def __init__(self):
        self.calls = 0

    def fetch_bars(self, symbol, start, end):
        self.calls += 1
        return [{"symbol": symbol, "event_time": start, "available_at": start,
                 "open": 100, "high": 102, "low": 99, "close": 101, "volume": 5}]


@pytest.fixture
def market(monkeypatch):
    adapter = FixtureBars()
    gateway = MarketDataGateway()
    gateway.register_adapter("fixture", adapter, supported_asset_classes=["crypto"],
                             entitlement_reference="offline-fixture-only")
    monkeypatch.setattr(market_routes, "market_data_gateway", gateway)
    end = datetime.now(UTC)
    params = {"provider": "fixture", "symbol": "BTC-USD", "asset_class": "crypto",
              "start": (end - timedelta(hours=1)).isoformat(), "end": end.isoformat()}
    return adapter, params


def test_market_read_reuses_gateway_preserves_synthetic_provenance_and_is_no_store(api, market):
    adapter, params = market
    response = api.get("/control-plane/market/bars", params=params, headers=ANALYST)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["mode"] == "synthetic"
    assert data["window_start"] == params["start"]
    assert data["bars"][0]["close"] == 101
    assert data["live_execution"] is False
    assert response.headers["cache-control"] == "no-store"
    assert adapter.calls == 1


def test_market_denies_viewer_before_upstream_and_validates_bounded_window(api, market):
    adapter, params = market
    assert api.get("/control-plane/market/bars", params=params, headers=VIEWER).status_code == 403
    for start in ["bad-time-" * 4, params["end"], "2020-01-01T00:00:00+00:00"]:
        response = api.get("/control-plane/market/bars", params={**params, "start": start}, headers=ANALYST)
        assert response.status_code == 422
    assert adapter.calls == 0


def test_unavailable_provider_never_falls_back_to_a_different_symbol_source(api, market):
    adapter, params = market
    response = api.get("/control-plane/market/bars", params={**params, "provider": "absent"}, headers=ANALYST)
    assert response.status_code == 503
    assert response.json() == {"detail": "market_data_unavailable"}
    assert adapter.calls == 0
