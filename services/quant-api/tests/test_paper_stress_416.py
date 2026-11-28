"""Canonical paper stress uses the book's positions and does not write them."""
from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from osiris_api.main import app
from osiris_api.paper_execution import (
    LIVE_EXECUTION_ENABLED,
    initialize_paper_account,
    mark_to_market,
    snapshot,
    submit_paper_order,
)
from osiris_api.paper_stress import stress_paper_book
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


def _hold():
    assert submit_paper_order(
        client_order_id="buy", symbol="BTC", side="BUY", quantity="1", price="100",
        fee="0", observed_at=FRESH, now=NOW, risk_service_available=True,
        kill_switch_active=False,
    )["status"] == "filled"


def test_live_execution_stays_disabled():
    assert LIVE_EXECUTION_ENABLED is False


def test_mark_shock_changes_pnl_and_not_the_book(book):
    _hold()
    mark_to_market({"BTC": "110"}, observed_at=FRESH, now=NOW)
    before = snapshot()
    report = stress_paper_book(NOW, {"BTC": "-0.10"})
    assert report["state"] == "ready"
    assert report["book_mutated"] is False
    assert report["live_execution"] is False
    assert report["current_unrealized_pnl"] == "10"
    assert report["stressed_unrealized_pnl"] == "-1"
    assert report["pnl_change"] == "-11"
    assert report["positions"][0]["shocked_mark"] == "99"
    assert snapshot() == before


def test_missing_mark_does_not_invent_a_total(book):
    _hold()
    report = stress_paper_book(NOW, {"BTC": "-0.10"})
    assert report["state"] == "incomplete"
    assert report["reason"] == "mark_required"
    assert report["stressed_unrealized_pnl"] is None
    assert report["positions"] == []


def test_unheld_symbol_and_full_loss_are_rejected(book):
    _hold()
    mark_to_market({"BTC": "110"}, observed_at=FRESH, now=NOW)
    with pytest.raises(ValueError, match="shock_for_unheld_symbol"):
        stress_paper_book(NOW, {"ETH": "-0.10"})
    with pytest.raises(ValueError, match="invalid_stress_shock"):
        stress_paper_book(NOW, {"BTC": "-1"})
    assert snapshot()["positions"][0]["quantity"] == "1"


def test_corrupt_book_is_unavailable(book):
    _hold()
    with sqlite3.connect(book) as conn:
        conn.execute("UPDATE paper_exec_audit SET payload='{}' WHERE seq=1")
    report = stress_paper_book(NOW, {"BTC": "-0.10"})
    assert report["state"] == "unavailable"
    assert report["reason"] == "audit_chain_corrupt"
    assert report["stressed_unrealized_pnl"] is None


def test_http_rejects_replacement_positions(book):
    _hold()
    mark_to_market({"BTC": "110"}, observed_at=FRESH, now=NOW)
    response = TestClient(app).post("/paper/execution/stress", json={
        "as_of": NOW, "shocks": {"BTC": "-0.10"}, "positions": [{"symbol": "BTC", "quantity": "100"}],
    })
    assert response.status_code == 422
    assert response.json()["detail"] == "stress_positions_not_accepted"
    assert snapshot()["positions"][0]["quantity"] == "1"
