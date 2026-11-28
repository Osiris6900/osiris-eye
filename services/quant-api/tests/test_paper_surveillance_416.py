"""Paper surveillance reads the journal. It does not trust a caller event list."""
from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from osiris_api.main import app
from osiris_api.paper_execution import (
    LIVE_EXECUTION_ENABLED,
    cancel_paper_order,
    initialize_paper_account,
    submit_paper_order,
)
from osiris_api.paper_surveillance import review_paper_journal
from osiris_api.risk import RiskLimits

NOW = "2026-11-22T15:00:00+00:00"
FRESH = "2026-11-22T14:59:30+00:00"
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


def _order(key, *, working=False, side="BUY", price="100"):
    return submit_paper_order(
        client_order_id=key, symbol="BTC", side=side, quantity="1", price=price,
        fee="0", observed_at=FRESH, now=NOW, risk_service_available=True,
        kill_switch_active=False, working=working,
    )


def test_live_execution_stays_disabled():
    assert LIVE_EXECUTION_ENABLED is False


def test_empty_book_is_reviewed_and_not_configured_is_unavailable(book, monkeypatch):
    empty = review_paper_journal()
    assert empty["state"] == "reviewed"
    assert empty["alerts"] == []
    assert empty["human_review_required"] is True
    assert empty["enforcement_action_taken"] is False
    assert empty["live_execution"] is False
    assert empty["source"] == "paper_journal"
    monkeypatch.delenv("OSIRIS_PAPER_EXECUTION_DB")
    missing = review_paper_journal()
    assert missing["state"] == "unavailable"
    assert missing["reason"] == "not_configured"
    assert missing["alerts"] == []


def test_repeated_cancels_and_a_same_second_burst_are_flagged(book):
    for name in ("a", "b", "c"):
        assert _order(name, working=True)["state"] == "accepted"
        assert cancel_paper_order(client_order_id=name)["state"] == "cancelled"
    report = review_paper_journal()
    indicators = {row["indicator"] for row in report["alerts"]}
    assert report["state"] == "reviewed"
    assert "high_cancel_to_submit_ratio" in indicators
    assert "rapid_submit_burst" in indicators
    assert report["orders_reviewed"] == 3
    cancel = next(row for row in report["alerts"] if row["indicator"] == "high_cancel_to_submit_ratio")
    assert cancel["symbol"] == "BTC"
    assert cancel["cancels"] == 3
    assert report["enforcement_action_taken"] is False


def test_same_price_round_trip_comes_from_fills(book):
    assert _order("buy")["status"] == "filled"
    assert _order("sell", side="SELL")["status"] == "filled"
    report = review_paper_journal()
    assert any(row["indicator"] == "same_price_round_trip" for row in report["alerts"])
    assert report["fills_reviewed"] == 2


def test_two_orders_are_not_a_cancel_pattern(book):
    _order("one", working=True)
    _order("two", working=True)
    cancel_paper_order(client_order_id="one")
    report = review_paper_journal()
    assert report["state"] == "reviewed"
    assert all(row["indicator"] != "high_cancel_to_submit_ratio" for row in report["alerts"])


def test_corrupt_journal_is_not_a_clear_review(book):
    _order("one")
    with sqlite3.connect(book) as conn:
        conn.execute("UPDATE paper_exec_audit SET payload='{}' WHERE seq=1")
    report = review_paper_journal()
    assert report["state"] == "unavailable"
    assert report["reason"] == "audit_chain_corrupt"
    assert report["alerts"] == []


def test_http_ignores_caller_events(book):
    for name in ("a", "b", "c"):
        _order(name, working=True)
        cancel_paper_order(client_order_id=name)
    client = TestClient(app)
    response = client.get("/paper/execution/surveillance", params={
        "events": "[]", "cancel_ratio_threshold": "1",
    })
    assert response.status_code == 200
    body = response.json()
    assert any(row["indicator"] == "high_cancel_to_submit_ratio" for row in body["alerts"])
    assert body["live_execution"] is False
    posted = client.post("/paper/execution/surveillance", json={"events": []})
    assert posted.status_code == 405
