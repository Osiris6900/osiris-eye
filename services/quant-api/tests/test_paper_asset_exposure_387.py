"""Paper holdings are classified by the instrument master, not by a caller portfolio."""
from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from osiris_api.main import app
from osiris_api.paper_asset_exposure import paper_asset_exposure
from osiris_api.paper_execution import (
    LIVE_EXECUTION_ENABLED,
    initialize_paper_account,
    mark_to_market,
    snapshot,
    submit_paper_order,
)
from osiris_api.risk import RiskLimits

NOW = "2026-11-23T04:30:00+00:00"
LIMITS = RiskLimits(
    max_order_notional=100_000, max_position_notional=100_000,
    max_daily_loss=100_000, max_gross_exposure=100_000,
)


@pytest.fixture
def book(tmp_path, monkeypatch):
    path = tmp_path / "paper.sqlite3"
    monkeypatch.setenv("OSIRIS_PAPER_EXECUTION_DB", str(path))
    monkeypatch.setenv("OSIRIS_INSTRUMENT_DB_PATH", str(tmp_path / "instruments.sqlite3"))
    monkeypatch.delenv("OSIRIS_ENABLE_LIVE_EXECUTION", raising=False)
    monkeypatch.delenv("OSIRIS_INSTRUMENT_FIXTURE", raising=False)
    initialize_paper_account("100000", LIMITS)
    return path


def _buy(key, symbol, price, when, quantity="1"):
    assert submit_paper_order(
        client_order_id=key, symbol=symbol, side="BUY", quantity=quantity, price=price,
        fee="0", observed_at=when, now=when, risk_service_available=True,
        kill_switch_active=False,
    )["status"] == "filled"


def test_live_execution_stays_disabled():
    assert LIVE_EXECUTION_ENABLED is False


def test_equity_and_crypto_are_separate_classes(book):
    _buy("reliance", "RELIANCE", "100.05", NOW)
    mark_to_market({"RELIANCE": "100.10"}, observed_at=NOW, now=NOW)
    _buy("btc", "BTC", "100", NOW)
    mark_to_market({"BTC": "110", "RELIANCE": "100.10"}, observed_at=NOW, now=NOW)
    before = snapshot()
    report = paper_asset_exposure(NOW)
    assert report["state"] == "ready"
    assert report["correlation_estimated"] is False
    assert report["book_mutated"] is False
    assert report["asset_classes"] == [
        {"asset_class": "crypto", "exposure": "110"},
        {"asset_class": "equity", "exposure": "100.1"},
    ]
    assert report["gross_exposure"] == "210.1"
    assert {"from": "BTC", "to": "crypto", "relation": "classified_as"} in report["edges"]
    assert {"from": "RELIANCE", "to": "equity", "relation": "classified_as"} in report["edges"]
    assert snapshot() == before


def test_missing_mark_does_not_classify_a_partial_book(book):
    _buy("btc", "BTC", "100", NOW)
    report = paper_asset_exposure(NOW)
    assert report["state"] == "incomplete"
    assert report["reason"] == "mark_required"
    assert report["gross_exposure"] is None
    assert report["edges"] == []


def test_corrupt_book_is_unavailable(book):
    _buy("btc", "BTC", "100", NOW)
    with sqlite3.connect(book) as conn:
        conn.execute("UPDATE paper_exec_audit SET payload='{}' WHERE seq=1")
    report = paper_asset_exposure(NOW)
    assert report["state"] == "unavailable"
    assert report["reason"] == "audit_chain_corrupt"
    assert report["nodes"] == []


def test_http_ignores_caller_weights(book):
    _buy("btc", "BTC", "100", NOW)
    mark_to_market({"BTC": "110"}, observed_at=NOW, now=NOW)
    response = TestClient(app).get("/paper/execution/asset-exposure", params={
        "as_of": NOW, "weights": "1", "asset_class": "equity",
    })
    assert response.status_code == 200
    body = response.json()
    assert body["asset_classes"] == [{"asset_class": "crypto", "exposure": "110"}]
    assert body["correlation_estimated"] is False
