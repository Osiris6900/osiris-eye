"""Regression checks across clock, feed admission, instruments and broker risk."""
import json
from datetime import datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from osiris_api import paper_execution as ledger
from osiris_api.control_plane.clock import (
    TrustedClock,
    install_clock_health,
    install_runtime_clock,
)
from osiris_api.main import app

NOW = "2026-11-26T12:00:00+00:00"


@pytest.fixture
def book(tmp_path, monkeypatch):
    monkeypatch.setenv("OSIRIS_PAPER_EXECUTION_DB", str(tmp_path / "paper.db"))
    monkeypatch.setenv("OSIRIS_INSTRUMENT_DB_PATH", str(tmp_path / "instruments.db"))
    install_runtime_clock(TrustedClock(lambda: datetime.fromisoformat(NOW), source_id="test.server"))
    ledger.initialize_paper_account("10000")
    ledger.mark_to_market({"BTC": "100"}, observed_at=NOW)


def _authorize(symbol="BTC", price=Decimal(100)):
    return ledger.authorize_paper_submission(
        client_order_id="broker-candidate", symbol=symbol, side="BUY",
        quantity=Decimal(1), price=price,
    )


def test_broker_risk_uses_trusted_clock_and_fails_closed_on_degraded_host(book):
    approved = _authorize()
    assert approved["approved"] is True
    event = next(row for row in ledger.snapshot()["audit"] if row["event"] == ledger.PRETRADE_RISK_EVENT)
    provenance = json.loads(event["payload"])["timestamp_provenance"]
    assert provenance["clock_source_id"] == "test.server"
    assert provenance["server_receive_time"] == NOW
    assert provenance["trusted"] is True
    install_clock_health({"status": "degraded", "orders_fail_closed": True})
    rejected = _authorize()
    assert rejected["approved"] is False
    assert rejected["reason"] == "clock_degraded"
    assert ledger.reconcile()["passed"] is True
    delivered = ledger.pull_bus_events("paper.risk", "risk-review")["events"]
    assert [row["event_type"] for row in delivered] == [ledger.PRETRADE_RISK_EVENT] * 2
    assert delivered[-1]["payload"]["reason"] == "clock_degraded"
    assert delivered[0]["payload"]["timestamp_provenance"] == provenance


def test_broker_risk_preserves_exact_instrument_validation_and_audit(book):
    result = _authorize(symbol="AAPL", price=Decimal("100.001"))
    assert result["approved"] is False
    assert result["reason"] == "invalid_tick_size"
    assert result["inputs"]["instrument_master_version"]
    result = _authorize(symbol="NOT-IN-MASTER")
    assert result["approved"] is False
    assert result["reason"] == "unknown_instrument"


def test_http_nested_receive_stamp_cannot_override_server_time(book):
    before = ledger.snapshot()["marks"]
    response = TestClient(app).post("/paper/execution/marks", json={
        "observed_at": NOW,
        "marks": {"BTC": {"price": "101", "received_time": NOW}},
    })
    assert response.status_code == 422
    assert response.json()["detail"] == "caller_clock_rejected"
    assert ledger.snapshot()["marks"] == before


def test_quality_rejection_retains_trusted_receive_and_instrument_metadata(book):
    with pytest.raises(ValueError, match="outlier_price"):
        ledger.mark_to_market({"BTC": {"price": "2000", "source": "paper-ledger"}}, observed_at=NOW)
    state = ledger.snapshot()
    assert state["marks"][0]["price"] == "100"
    payload = json.loads(state["audit"][-1]["payload"])
    assert payload["reason"] == "outlier_price"
    assert payload["timestamp_provenance"]["server_receive_time"] == NOW
    assert ledger.reconcile()["passed"] is True
    delivered = ledger.pull_bus_events("paper.marks", "quality-review")["events"]
    assert delivered[-1]["event_type"] == "market_observation_rejected"
    assert delivered[-1]["payload"] == payload


def test_http_fill_retains_server_clock_identity_in_journal_and_bus(book):
    opened = ledger.submit_paper_order(
        client_order_id="http-fill", symbol="BTC", side="BUY", quantity="1",
        price="100", observed_at=NOW, working=True,
    )
    assert opened["state"] == "accepted"
    response = TestClient(app).post("/paper/execution/orders/http-fill/fills", json={
        "client_fill_id": "server-fill", "quantity": "1", "observed_at": NOW,
    })
    assert response.status_code == 200
    assert response.json()["state"] == "filled"
    event = next(row for row in ledger.snapshot()["audit"] if row["event"] == "fill_recorded")
    payload = json.loads(event["payload"])
    assert payload["timestamp_provenance"]["clock_source_id"] == "test.server"
    assert payload["timestamp_provenance"]["server_receive_time"] == NOW
    delivered = ledger.pull_bus_events("paper.fills", "fill-review")["events"]
    assert delivered[-1]["payload"] == payload


@pytest.mark.parametrize("via_http", [False, True])
def test_resting_fill_rechecks_host_clock_and_can_retry_after_recovery(book, via_http):
    opened = ledger.submit_paper_order(
        client_order_id="clock-fill", symbol="BTC", side="BUY", quantity="2",
        price="100", observed_at=NOW, working=True,
    )
    assert opened["state"] == "accepted"
    fill = {"client_fill_id": "clock-fill-1", "quantity": "1", "observed_at": NOW}
    install_clock_health({"status": "degraded", "orders_fail_closed": True})
    if via_http:
        response = TestClient(app).post("/paper/execution/orders/clock-fill/fills", json=fill)
        assert response.status_code == 422
        assert response.json()["detail"] == "clock_degraded"
    else:
        with pytest.raises(ValueError, match="^clock_degraded$"):
            ledger.apply_paper_fill(client_order_id="clock-fill", **fill)

    state = ledger.snapshot()
    assert state["fill_count"] == 0
    assert state["cash"] == "10000"
    assert state["positions"] == []
    assert state["orders"][0]["state"] == "accepted"
    assert state["orders"][0]["leaves_quantity"] == "2"
    rejection = json.loads(state["audit"][-1]["payload"])
    assert rejection["reason"] == "clock_degraded"
    assert rejection["timestamp_provenance"]["clock_source_id"] == "test.server"
    assert ledger.pull_bus_events("paper.fills", "clock-review")["events"][-1]["payload"] == rejection
    assert ledger.reconcile()["passed"] is True

    install_clock_health({"status": "healthy", "orders_fail_closed": False})
    recovered = ledger.apply_paper_fill(client_order_id="clock-fill", **fill)
    assert recovered["state"] == "partially_filled"
    assert recovered["leaves_quantity"] == "1"
    install_clock_health({"status": "degraded", "orders_fail_closed": True})
    assert ledger.apply_paper_fill(client_order_id="clock-fill", **fill)["status"] == "idempotent_replay"
    assert ledger.cancel_paper_order(client_order_id="clock-fill", now=NOW)["state"] == "cancelled"
    assert ledger.snapshot()["fill_count"] == 1
    assert ledger.reconcile()["passed"] is True
