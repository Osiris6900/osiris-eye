"""The offline browser fixture must seed the canonical account it displays."""

import json
import os
import runpy
from pathlib import Path
from unittest.mock import patch

import uvicorn

from osiris_api import market_routes
from osiris_api.market_gateway import MarketDataGateway
from osiris_api.paper_execution import reconcile, snapshot


def test_browser_fixture_seeds_a_verified_account_before_starting_tls(tmp_path, monkeypatch):
    fixture = Path(__file__).resolve().parents[3] / "scripts/browser_platform20_fixture.py"
    namespace = runpy.run_path(str(fixture))
    started = []
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: started.append((args, kwargs)))
    monkeypatch.setattr(market_routes, "market_data_gateway", MarketDataGateway())
    with patch.dict(os.environ, {"OSIRIS_BROWSER_FIXTURE_DIR": str(tmp_path)}):
        namespace["main"]()
        account = snapshot()
        assert account["cash"] == "798"
        assert account["fill_count"] == 1
        assert account["unrealized_pnl"] == "18"
        assert account["positions"][0]["quantity"] == "2"
        assert reconcile()["passed"] is True
        evidence = json.loads((tmp_path / "fixture.json").read_text())
        assert evidence["clock_health_source"] == "synthetic-browser-fixture"
        assert json.loads(os.environ["OSIRIS_CLOCK_HEALTH_JSON"])["live_trading_authorized"] is False
        assert len(started) == 1
        assert started[0][1]["ssl_certfile"] == str(tmp_path / "ca.pem")
