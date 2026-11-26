"""Paper marks and fill references must pass the existing data-quality gates.

A rejected observation leaves the last good mark in place. Freshness is the age
of the exchange and receive timestamps at the injected clock. Live trading stays
disabled.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from osiris_api.authorized_feeds import suspend_adapter
from osiris_api.market_feed_gate import (
    DEFAULT_MAX_MARK_JUMP_MULTIPLE,
    MarkCursor,
    admit_market_observation,
)
from osiris_api.paper_execution import (
    LIVE_EXECUTION_ENABLED,
    apply_paper_fill,
    execution_context,
    initialize_paper_account,
    market_data_quality,
    mark_to_market,
    reconcile,
    snapshot,
    submit_paper_order,
)
from osiris_api.risk import RiskLimits


NOW = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)
NOW_S = NOW.isoformat()
EARLIER = (NOW - timedelta(seconds=30)).isoformat()
STALE = (NOW - timedelta(seconds=400)).isoformat()


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("OSIRIS_PAPER_EXECUTION_DB", str(tmp_path / "quality.sqlite3"))
    monkeypatch.delenv("OSIRIS_ENABLE_LIVE_EXECUTION", raising=False)
    initialize_paper_account("100000", RiskLimits(
        max_order_notional=100_000, max_position_notional=100_000,
        max_daily_loss=100_000, max_gross_exposure=100_000,
    ))
    return tmp_path


def _obs(price, **extra):
    body = {"price": price, "source": "paper-ledger"}
    body.update(extra)
    return body


def _mark(price="100", **extra):
    return mark_to_market(
        {"BTC": _obs(price, **extra)}, observed_at=NOW_S, now=NOW_S,
    )


def test_live_trading_stays_disabled():
    assert LIVE_EXECUTION_ENABLED is False
    assert DEFAULT_MAX_MARK_JUMP_MULTIPLE == Decimal("10")


def test_scalar_mark_still_passes_the_gates_and_is_counted(db):
    marked = mark_to_market({"BTC": "140"}, observed_at=NOW_S, now=NOW_S)
    assert marked["unrealized_pnl"] == "0"
    assert marked["live_execution"] is False
    state = snapshot()
    assert state["marks"] == [{"symbol": "BTC", "price": "140", "observed_at": NOW_S}]
    quality = state["market_data_quality"]
    assert quality[0]["last_good_at"] == NOW_S
    assert quality[0]["counts"]["accepted"] == 1
    assert quality[0]["live_trading_authorized"] is False
    assert reconcile()["passed"] is True


def test_same_timestamp_replacement_within_the_jump_band_stays_allowed(db):
    mark_to_market({"BTC": "100"}, observed_at=NOW_S, now=NOW_S)
    mark_to_market({"BTC": "180"}, observed_at=NOW_S, now=NOW_S)
    assert snapshot()["marks"][0]["price"] == "180"


def test_out_of_order_tick_does_not_replace_the_last_good_mark(db):
    mark_to_market({"BTC": "100"}, observed_at=NOW_S, now=NOW_S)
    before = snapshot()["marks"]
    with pytest.raises(ValueError, match="out_of_order_observation"):
        mark_to_market({"BTC": "50"}, observed_at=EARLIER, now=NOW_S)
    assert snapshot()["marks"] == before
    rejected = [
        row for row in snapshot()["audit"] if row["event"] == "market_observation_rejected"
    ]
    assert "out_of_order_observation" in rejected[-1]["payload"]
    assert reconcile()["passed"] is True


def test_unauthorized_source_and_caller_freshness_flag_do_not_admit_a_price(db):
    mark_to_market({"BTC": "100"}, observed_at=NOW_S, now=NOW_S)
    with pytest.raises(ValueError, match="source_unauthenticated"):
        _mark("250", source="evil-vendor", authenticated=True, entitled=True, fresh=True)
    assert snapshot()["marks"][0]["price"] == "100"
    with pytest.raises(ValueError, match="stale_market_data"):
        _mark(
            "110",
            event_time=STALE,
            received_time=STALE,
            fresh=True,
            market_data_fresh=True,
        )
    assert snapshot()["marks"] == [{"symbol": "BTC", "price": "100", "observed_at": NOW_S}]
    reasons = market_data_quality()["symbols"][0]["counts"]
    assert reasons["source_unauthenticated"] == 1
    assert reasons["stale_observation"] == 1
    assert market_data_quality()["live_trading_authorized"] is False


def test_outlier_duplicate_gap_and_crossed_quotes_leave_the_mark_unchanged(db):
    _mark("100", sequence=1)
    with pytest.raises(ValueError, match="outlier_price"):
        mark_to_market(
            {"BTC": _obs("130", sequence=2)},
            observed_at=NOW_S, now=NOW_S, max_jump_multiple="1.2",
        )
    with pytest.raises(ValueError, match="duplicate_tick"):
        _mark("100", sequence=1)
    with pytest.raises(ValueError, match="sequence_gap"):
        _mark("101", sequence=4)
    with pytest.raises(ValueError, match="sequence_gap"):
        _mark("101", sequence=3)
    with pytest.raises(ValueError, match="crossed_quote"):
        _mark("100", sequence=2, bid="101", ask="100")
    with pytest.raises(ValueError, match="locked_quote"):
        _mark("100", sequence=2, bid="100", ask="100")
    assert snapshot()["marks"][0]["price"] == "100"
    assert snapshot()["market_data_quality"][0]["gap_open"] is True
    assert snapshot()["market_data_quality"][0]["sequence"] == 1
    rescued = _mark("104", sequence=4, resync=True)
    assert rescued["live_execution"] is False
    assert snapshot()["marks"][0]["price"] == "104"
    assert snapshot()["market_data_quality"][0]["gap_open"] is False
    assert snapshot()["market_data_quality"][0]["sequence"] == 4
    assert reconcile()["passed"] is True


def test_rejected_price_does_not_refresh_the_mark_the_risk_gate_reads(db):
    mark_to_market({"BTC": "140"}, observed_at=EARLIER, now=EARLIER)
    submit_paper_order(
        client_order_id="open", symbol="BTC", side="BUY", quantity="1", price="100",
        fee="0", observed_at=EARLIER, now=EARLIER,
    )
    with pytest.raises(ValueError, match="outlier_price"):
        mark_to_market({"BTC": "5000"}, observed_at=NOW_S, now=NOW_S)
    context = execution_context(symbol="BTC", price="100", now=NOW_S)
    assert context["buy_exposure_price"] == "140"
    aged = (NOW + timedelta(seconds=400)).isoformat()
    later = execution_context(symbol="BTC", price="100", now=aged)
    assert later["buy_exposure_price"] == "100"
    assert snapshot()["marks"][0]["observed_at"] == EARLIER


def test_fill_reference_far_from_the_last_good_mark_is_rejected(db):
    mark_to_market({"BTC": "100"}, observed_at=NOW_S, now=NOW_S)
    opened = submit_paper_order(
        client_order_id="wide", symbol="BTC", side="BUY", quantity="1", price="5000",
        fee="0", observed_at=NOW_S, now=NOW_S, working=True,
    )
    assert opened["status"] == "risk_rejected"
    assert opened["reason"] == "outlier_price"
    assert opened["fill"] is None
    assert snapshot()["fill_count"] == 0
    assert snapshot()["cash"] == "100000"
    accepted = submit_paper_order(
        client_order_id="near", symbol="BTC", side="BUY", quantity="1", price="900",
        fee="0", observed_at=NOW_S, now=NOW_S, working=True,
    )
    assert accepted["state"] == "accepted"
    mark_to_market({"BTC": "80"}, observed_at=NOW_S, now=NOW_S)
    with pytest.raises(ValueError, match="outlier_price"):
        apply_paper_fill(
            client_order_id="near", client_fill_id="bad", quantity="1", price="900",
            observed_at=NOW_S, now=NOW_S,
        )
    assert snapshot()["fill_count"] == 0
    assert snapshot()["marks"][0]["price"] == "80"
    assert reconcile()["passed"] is True


def test_bad_prices_and_suspended_sources_fail_closed_in_the_shared_gate():
    cursor = MarkCursor(price=Decimal("100"), event_time=NOW, sequence=1, source="paper-ledger")
    common = dict(
        symbol="BTC", source="paper-ledger", event_time=NOW, received_time=NOW, now=NOW,
        max_age_seconds=300, max_future_skew_seconds=60, previous=cursor,
    )
    assert admit_market_observation(price=Decimal("nan"), **common)[1] == "invalid_price"
    assert admit_market_observation(price=Decimal("Infinity"), **common)[1] == "invalid_price"
    assert admit_market_observation(price=Decimal("0"), **common)[1] == "invalid_price"
    assert admit_market_observation(price=Decimal("-5"), **common)[1] == "invalid_price"
    suspend_adapter("synthetic-authorized")
    try:
        suspended = admit_market_observation(
            price=Decimal("100"), symbol="BTC", source="synthetic-authorized",
            event_time=NOW, received_time=NOW, now=NOW,
            max_age_seconds=300, max_future_skew_seconds=60,
        )
    finally:
        from osiris_api.authorized_feeds import restore_adapter
        restore_adapter("synthetic-authorized")
    assert suspended == (False, "source_unentitled")
