"""An expired authoritative catalog version must not revive an older contract."""
from __future__ import annotations

import pytest

from osiris_api.instrument_master import InstrumentMasterStore

OLD = "2020-01-01T00:00:00Z"
NEW = "2026-01-01T00:00:00Z"
END = "2026-02-01T00:00:00Z"
NOW = "2026-11-22T15:00:00Z"


def _instrument(*, venue="NASDAQ", **changes):
    record = {
        "asset_class": "equity", "venue": venue, "symbol": "AAPL",
        "quote_currency": "USD", "timezone": "America/New_York",
        "session_type": "exchange", "tick_size": "0.01", "lot_size": "1",
    }
    return {**record, **changes}


@pytest.fixture
def master(tmp_path):
    return InstrumentMasterStore(str(tmp_path / "instruments.sqlite3"))


def test_expired_latest_version_does_not_resurrect_older_open_ended_contract(master):
    old = master.put(_instrument(), effective_at=OLD)
    latest = master.put(_instrument(effective_to=END, tick_size="0.05"), effective_at=NEW)

    before = master.assess("AAPL", price="100.01", quantity="1", as_of="2025-12-31T23:59:59Z")
    assert before["ok"] is True
    assert before["instrument_version"] == old["instrument"]["content_hash"]
    during = master.assess("AAPL", price="100.05", quantity="1", as_of=NEW)
    assert during["ok"] is True
    assert during["instrument_version"] == latest["instrument"]["content_hash"]
    for as_of in (END, NOW):
        expired = master.assess("AAPL", price="100.05", quantity="1", as_of=as_of)
        assert expired["ok"] is False
        assert expired["reason"] == "instrument_inactive"


def test_future_version_does_not_replace_current_effective_version(master):
    current = master.put(_instrument(), effective_at=OLD)
    master.put(_instrument(status="halted"), effective_at="2027-01-01T00:00:00Z")
    decision = master.assess("AAPL", price="100.01", quantity="1", as_of=NOW)
    assert decision["ok"] is True
    assert decision["instrument_version"] == current["instrument"]["content_hash"]


def test_future_version_does_not_bridge_gap_after_expired_version(master):
    master.put(_instrument(), effective_at=OLD)
    master.put(_instrument(effective_to=END), effective_at=NEW)
    master.put(_instrument(), effective_at="2027-01-01T00:00:00Z")
    assert master.assess("AAPL", as_of=NOW)["reason"] == "instrument_inactive"


def test_unknown_and_future_only_catalogs_remain_fail_closed(master):
    assert master.assess("AAPL", as_of=NOW)["reason"] == "unknown_instrument"
    master.put(_instrument(), effective_at="2027-01-01T00:00:00Z")
    assert master.assess("AAPL", as_of=NOW)["reason"] == "instrument_not_yet_effective"


def test_expired_venue_history_does_not_hide_single_current_venue(master):
    master.put(_instrument(), effective_at=OLD)
    master.put(_instrument(effective_to=END), effective_at=NEW)
    other = master.put(_instrument(venue="NYSE"), effective_at=NEW)
    decision = master.assess("AAPL", price="100", quantity="1", as_of=NOW)
    assert decision["ok"] is True
    assert decision["instrument_id"] == "equity:NYSE:AAPL"
    assert decision["instrument_version"] == other["instrument"]["content_hash"]


def test_two_current_venues_remain_ambiguous(master):
    master.put(_instrument(), effective_at=OLD)
    master.put(_instrument(venue="NYSE"), effective_at=NEW)
    decision = master.assess("AAPL", price="100", quantity="1", as_of=NOW)
    assert decision["ok"] is False
    assert decision["reason"] == "ambiguous_instrument"


@pytest.mark.parametrize("status", ["halted", "delisted"])
def test_latest_inactive_status_cannot_fall_back_to_active_history(master, status):
    master.put(_instrument(), effective_at=OLD)
    master.put(_instrument(status=status), effective_at=NEW)
    assert master.assess("AAPL", as_of=NOW)["reason"] == "instrument_inactive"


def test_paper_submission_rejects_expired_latest_version_without_changing_book(master, monkeypatch):
    from osiris_api.paper_execution import initialize_paper_account, snapshot, submit_paper_order
    from osiris_api.risk import RiskLimits

    monkeypatch.setenv("OSIRIS_INSTRUMENT_DB_PATH", master.database_path)
    monkeypatch.setenv("OSIRIS_PAPER_EXECUTION_DB", master.database_path + ".paper")
    master.put(_instrument(), effective_at=OLD)
    master.put(_instrument(effective_to=END), effective_at=NEW)
    initialize_paper_account("100000", RiskLimits())
    before = snapshot()
    order = submit_paper_order(
        client_order_id="expired-current-contract", symbol="AAPL", side="BUY",
        quantity="1", price="100", fee="0", observed_at=NOW, now=NOW,
        kill_switch_active=False, risk_service_available=True,
    )
    after = snapshot()
    assert order["status"] == "instrument_rejected"
    assert order["reason"] == "instrument_inactive"
    assert order["fill"] is None
    assert order["instrument_master_version"] == master.catalog_version()
    assert after["cash"] == before["cash"]
    assert after["positions"] == before["positions"] == []
    assert after["fill_count"] == before["fill_count"] == 0
