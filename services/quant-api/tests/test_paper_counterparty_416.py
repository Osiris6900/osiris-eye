"""Paper counterparty exposure is the book's venue, not a caller list."""
from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from osiris_api.main import app
from osiris_api.paper_counterparty import paper_counterparty_exposure
from osiris_api.paper_execution import (
    LIVE_EXECUTION_ENABLED,
    initialize_paper_account,
    mark_to_market,
    snapshot,
    submit_paper_order,
)
from osiris_api.risk import RiskLimits

NOW = "2026-11-23T15:00:00+00:00"
FRESH = "2026-11-23T14:59:30+00:00"
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


def _buy(key, symbol, price, quantity="1"):
    assert submit_paper_order(
        client_order_id=key, symbol=symbol, side="BUY", quantity=quantity, price=price,
        fee="0", observed_at=FRESH, now=NOW, risk_service_available=True,
        kill_switch_active=False,
    )["status"] == "filled"


def test_live_execution_stays_disabled():
    assert LIVE_EXECUTION_ENABLED is False


def test_two_paper_venues_sum_and_the_book_is_unchanged(book):
    _buy("btc", "BTC", "100")
    mark_to_market({"BTC": "110"}, observed_at=FRESH, now=NOW)
    _buy("eth", "ETH", "50", quantity="2")
    mark_to_market({"ETH": "40"}, observed_at=FRESH, now=NOW)
    before = snapshot()
    report = paper_counterparty_exposure(NOW)
    assert report["state"] == "ready"
    assert report["creditworthiness_assessed"] is False
    assert report["book_mutated"] is False
    assert report["live_execution"] is False
    assert report["source"] == "paper_book"
    assert report["counterparties"] == [{"counterparty": "PAPER", "exposure": "190"}]
    assert report["gross_exposure"] == "190"
    assert snapshot() == before


def test_missing_mark_does_not_invent_exposure(book):
    _buy("btc", "BTC", "100")
    report = paper_counterparty_exposure(NOW)
    assert report["state"] == "incomplete"
    assert report["reason"] == "mark_required"
    assert report["gross_exposure"] is None
    assert report["counterparties"] == []


def test_corrupt_book_is_unavailable(book):
    _buy("btc", "BTC", "100")
    with sqlite3.connect(book) as conn:
        conn.execute("UPDATE paper_exec_audit SET payload='{}' WHERE seq=1")
    report = paper_counterparty_exposure(NOW)
    assert report["state"] == "unavailable"
    assert report["reason"] == "audit_chain_corrupt"
    assert report["gross_exposure"] is None


def test_http_ignores_caller_exposures(book):
    _buy("btc", "BTC", "100")
    mark_to_market({"BTC": "110"}, observed_at=FRESH, now=NOW)
    response = TestClient(app).get("/paper/execution/counterparties", params={
        "as_of": NOW, "exposures": "999999", "counterparty": "BANK",
    })
    assert response.status_code == 200
    body = response.json()
    assert body["gross_exposure"] == "110"
    assert body["counterparties"] == [{"counterparty": "PAPER", "exposure": "110"}]
    assert body["creditworthiness_assessed"] is False
