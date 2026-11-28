"""A stored symbol change renames the paper position and keeps cost and cash."""
from __future__ import annotations

import pytest

from osiris_api.instrument_master import InstrumentMasterStore, instrument_db_path
from osiris_api.paper_corporate_actions import apply_paper_corporate_actions
from osiris_api.paper_execution import (
    LIVE_EXECUTION_ENABLED,
    initialize_paper_account,
    mark_to_market,
    reconcile,
    snapshot,
    submit_paper_order,
)
from osiris_api.risk import RiskLimits

WHEN = "2024-05-01T04:30:00+00:00"
LIMITS = RiskLimits(
    max_order_notional=100_000, max_position_notional=100_000,
    max_daily_loss=100_000, max_gross_exposure=100_000,
)


@pytest.fixture
def book(tmp_path, monkeypatch):
    monkeypatch.setenv("OSIRIS_PAPER_EXECUTION_DB", str(tmp_path / "paper.sqlite3"))
    monkeypatch.setenv("OSIRIS_INSTRUMENT_DB_PATH", str(tmp_path / "instruments.sqlite3"))
    monkeypatch.delenv("OSIRIS_ENABLE_LIVE_EXECUTION", raising=False)
    monkeypatch.delenv("OSIRIS_INSTRUMENT_FIXTURE", raising=False)
    initialize_paper_account("100000", LIMITS)
    store = InstrumentMasterStore(instrument_db_path())
    store.ensure_seeded()
    store.put({
        "asset_class": "equity", "venue": "NSE", "symbol": "RIL",
        "quote_currency": "INR", "timezone": "Asia/Kolkata",
        "session_type": "exchange", "tick_size": "0.05", "lot_size": "1",
    }, effective_at="2020-01-01T00:00:00Z")
    store.put_corporate_action({
        "instrument_id": "equity:NSE:RELIANCE",
        "action_type": "symbol_change",
        "effective_at": "2024-05-15T00:00:00Z",
        "new_symbol": "RIL",
    })
    return store


def _buy(symbol, key):
    return submit_paper_order(
        client_order_id=key, symbol=symbol, side="BUY", quantity="2", price="100.05",
        fee="0", observed_at=WHEN, now=WHEN, risk_service_available=True,
        kill_switch_active=False,
    )


def test_live_execution_stays_disabled():
    assert LIVE_EXECUTION_ENABLED is False


def test_symbol_change_renames_the_position(book):
    bought = _buy("RELIANCE", "old")
    assert bought["status"] == "filled"
    cash = snapshot()["cash"]
    result = apply_paper_corporate_actions("2024-05-16T00:00:00+00:00")
    renamed = [row for row in result["applied"] if row["action_type"] == "symbol_change"]
    assert len(renamed) == 1
    assert snapshot()["positions"] == [{"symbol": "RIL", "quantity": "2", "average_cost": "100.05"}]
    assert snapshot()["cash"] == cash
    assert reconcile()["passed"] is True
    again = apply_paper_corporate_actions("2024-05-16T00:00:00+00:00")
    assert again["applied"] == []
    assert renamed[0]["content_hash"] in again["unchanged"]


def test_occupied_target_is_not_overwritten(book):
    assert _buy("RIL", "target")["status"] == "filled"
    mark_to_market({"RIL": "100.05"}, observed_at=WHEN, now=WHEN)
    assert _buy("RELIANCE", "source")["status"] == "filled"
    result = apply_paper_corporate_actions("2024-05-16T00:00:00+00:00")
    assert any(row["reason"] == "symbol_change_target_occupied" for row in result["skipped"])
    symbols = {row["symbol"] for row in snapshot()["positions"]}
    assert symbols == {"RIL", "RELIANCE"}
    assert reconcile()["passed"] is True
