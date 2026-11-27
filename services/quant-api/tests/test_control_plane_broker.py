"""An upstream client ID cannot override the risk-approved order contract."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Event

import pytest

from osiris_api.control_plane.broker import AlpacaPaperBroker
from osiris_api.control_plane.database import Database, digest
from osiris_api.paper_execution import initialize_paper_account, mark_to_market, submit_paper_order
from osiris_api.risk import RiskLimits

ORDER = {"client_order_id": "order-1", "symbol": "TEST", "side": "BUY",
         "quantity": "3", "limit_price": "100"}
CONTEXT = {"limits": RiskLimits(), "position": 0, "gross": 0, "daily_pnl": 0,
           "kill_switch": False, "fresh": True}


def snapshot(**changes):
    return {"id": "broker-1", "client_order_id": "order-1", "status": "accepted",
            "symbol": "TEST", "side": "buy", "qty": "3", "limit_price": "100",
            "type": "limit", "time_in_force": "day", "filled_qty": "0",
            "filled_avg_price": None, **changes}


class Transport:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def broker(db, transport):
    return AlpacaPaperBroker(db, {"key": "FIXTURE", "secret": "FIXTURE"},
                             enabled=True, transport=transport)


def open_book(monkeypatch, tmp_path, *, limits=None, shares="0", price="100"):
    """Server-side book the broker gate reads. Caller floats are not this."""
    monkeypatch.setenv("OSIRIS_PAPER_EXECUTION_DB", str(tmp_path / "paper.sqlite3"))
    monkeypatch.delenv("OSIRIS_ENABLE_LIVE_EXECUTION", raising=False)
    initialize_paper_account("1000000", limits or RiskLimits(), "86400", "120")
    stamp = datetime.now(timezone.utc).isoformat()
    if shares not in {"0", "0.0"}:
        filled = submit_paper_order(
            client_order_id="seed-buy", symbol="TEST", side="BUY",
            quantity=shares, price=price, fee="0", observed_at=stamp, now=stamp,
        )
        assert filled["status"] == "filled", filled
    mark_to_market({"TEST": price}, observed_at=stamp, now=stamp)


@pytest.mark.parametrize("changes", [
    {"client_order_id": "another-order"}, {"id": None}, {"id": ["bad"]},
    {"symbol": "OTHER"}, {"side": "sell"}, {"qty": "4"}, {"qty": True},
    {"limit_price": "101"}, {"type": "market"}, {"time_in_force": "gtc"},
    {"status": "unrecognized"}, {"status": ["accepted"]},
    {"filled_qty": "-1"}, {"filled_qty": "4"}, {"filled_qty": "NaN"},
    {"filled_qty": None}, {"filled_qty": True}, {"filled_avg_price": "1"},
    {"status": "filled"}, {"status": "partially_filled"},
    {"status": "partially_filled", "filled_qty": "3", "filled_avg_price": "99"},
    {"status": "partially_filled", "filled_qty": "1", "filled_avg_price": None},
    {"status": "partially_filled", "filled_qty": "1", "filled_avg_price": "101"},
    {"status": "rejected", "filled_qty": "1", "filled_avg_price": "99"},
    {"qty": "1e999999999"}, {"filled_qty": "1e999999999"},
])
def test_unbound_acknowledgement_stays_uncertain_until_valid_reconciliation(tmp_path, monkeypatch, changes):
    open_book(monkeypatch, tmp_path)
    db = Database(str(tmp_path / "broker.db"))
    good = snapshot()
    transport = Transport(snapshot(**changes), good)
    service = broker(db, transport)
    with pytest.raises(ValueError, match="order_outcome_unknown_reconcile_first"):
        service.submit(ORDER, **CONTEXT)
    persisted = db.get("broker-orders", "order-1")["value"]
    assert persisted["status"] == "submitting"
    assert persisted["order"] == ORDER
    assert "response" not in persisted
    with pytest.raises(ValueError, match="reconcile_first"):
        service.submit(ORDER, **CONTEXT)
    assert service.reconcile("order-1") == good
    assert service.submit(ORDER, **CONTEXT) == good
    assert [call[0] for call in transport.calls] == ["POST", "GET"]
    assert all(call[1].startswith("https://paper-api.alpaca.markets/") for call in transport.calls)


def test_partial_full_fill_progress_and_exact_decimal_equivalence_survive_restart(tmp_path, monkeypatch):
    open_book(monkeypatch, tmp_path)
    path = str(tmp_path / "broker.db")
    part = snapshot(status="partially_filled", filled_qty="1.00", filled_avg_price="99")
    full = snapshot(status="filled", filled_qty="3", filled_avg_price="99.5",
                    qty="3.000", limit_price="100.00")
    transport = Transport(snapshot(), part, full)
    assert broker(Database(path), transport).submit(ORDER, **CONTEXT)["status"] == "accepted"
    restarted = broker(Database(path), transport)
    assert restarted.reconcile("order-1") == part
    assert restarted.reconcile("order-1") == full
    assert restarted.submit(ORDER, **CONTEXT) == full
    assert [call[0] for call in transport.calls] == ["POST", "GET", "GET"]


@pytest.mark.parametrize("status", ["new", "pending_new", "accepted", "accepted_for_bidding"])
def test_pre_execution_state_cannot_hide_already_reported_fills(tmp_path, monkeypatch, status):
    open_book(monkeypatch, tmp_path)
    db = Database(str(tmp_path / "broker.db"))
    partial = snapshot(status="partially_filled", filled_qty="1", filled_avg_price="99")
    transport = Transport(partial, {**partial, "status": status})
    service = broker(db, transport)
    service.submit(ORDER, **CONTEXT)
    before = db.get("broker-orders", "order-1")
    with pytest.raises(ValueError, match="broker_snapshot_mismatch"):
        service.reconcile("order-1")
    assert db.get("broker-orders", "order-1") == before


@pytest.mark.parametrize("changes", [
    {"id": "different-broker-order"}, {"filled_qty": "0", "filled_avg_price": None},
    {"filled_avg_price": "98"}, {"symbol": "OTHER"},
])
def test_regressing_or_conflicting_snapshot_preserves_last_verified_state(tmp_path, monkeypatch, changes):
    open_book(monkeypatch, tmp_path)
    db = Database(str(tmp_path / "broker.db"))
    part = snapshot(status="partially_filled", filled_qty="1", filled_avg_price="99")
    transport = Transport(part, {**part, **changes})
    service = broker(db, transport)
    service.submit(ORDER, **CONTEXT)
    before = db.get("broker-orders", "order-1")
    with pytest.raises(ValueError, match="broker_snapshot_mismatch"):
        service.reconcile("order-1")
    assert db.get("broker-orders", "order-1") == before


@pytest.mark.parametrize("terminal,filled,average", [
    ("filled", "3", "99"), ("canceled", "1", "99"),
    ("expired", "0", None), ("rejected", "0", None),
])
def test_terminal_orders_cannot_reopen_or_change_execution_progress(tmp_path, monkeypatch, terminal, filled, average):
    open_book(monkeypatch, tmp_path)
    db = Database(str(tmp_path / "broker.db"))
    final = snapshot(status=terminal, filled_qty=filled, filled_avg_price=average)
    transport = Transport(final, snapshot())
    service = broker(db, transport)
    service.submit(ORDER, **CONTEXT)
    with pytest.raises(ValueError, match="broker_snapshot_mismatch"):
        service.reconcile("order-1")
    assert db.get("broker-orders", "order-1")["value"]["response"] == final


def test_sell_acknowledgement_cannot_claim_a_fill_below_the_sell_limit(tmp_path, monkeypatch):
    open_book(monkeypatch, tmp_path, shares="3")
    db = Database(str(tmp_path / "broker.db"))
    service = broker(db, Transport(snapshot(side="sell", status="filled", filled_qty="3",
                                           filled_avg_price="99")))
    with pytest.raises(ValueError, match="order_outcome_unknown"):
        service.submit({**ORDER, "side": "SELL"}, **{**CONTEXT, "position": 300, "gross": 300})


def test_legacy_client_id_only_record_requires_trusted_intent_before_reconciliation(tmp_path, monkeypatch):
    open_book(monkeypatch, tmp_path)
    db = Database(str(tmp_path / "broker.db"))
    db.put("broker-orders", "order-1", {"fingerprint": digest(ORDER), "status": "submitting"})
    transport = Transport(snapshot())
    with pytest.raises(ValueError, match="broker_order_contract_required"):
        broker(db, transport).reconcile("order-1")
    with pytest.raises(ValueError, match="broker_order_contract_required"):
        broker(db, transport).submit(ORDER, **CONTEXT)
    assert not transport.calls


def test_concurrent_retry_and_changed_intent_cannot_submit_a_second_order(tmp_path, monkeypatch):
    open_book(monkeypatch, tmp_path)
    db = Database(str(tmp_path / "broker.db"))
    started, release = Event(), Event()

    class BlockingTransport(Transport):
        def request(self, method, url, **kwargs):
            started.set()
            assert release.wait(5)
            return super().request(method, url, **kwargs)

    transport = BlockingTransport(snapshot())
    service = broker(db, transport)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(service.submit, ORDER, **CONTEXT)
        try:
            assert started.wait(5)
            with pytest.raises(ValueError, match="reconcile_first"):
                service.submit(ORDER, **CONTEXT)
            with pytest.raises(ValueError, match="order_id_conflict"):
                service.submit({**ORDER, "quantity": "2"}, **CONTEXT)
        finally:
            release.set()
        assert pending.result(timeout=5) == snapshot()
    assert len(transport.calls) == 1


@pytest.mark.parametrize("order,limits,shares,price,reason", [
    ({**ORDER, "quantity": "1", "limit_price": "100.000000000000000001"},
     RiskLimits(max_order_notional=100), "0", "100", "max_order_notional_exceeded"),
    ({**ORDER, "quantity": "1e-200", "limit_price": "1"},
     RiskLimits(max_position_notional=100), "100", "1", "max_position_notional_exceeded"),
    ({**ORDER, "quantity": "1e-200", "limit_price": "1"},
     RiskLimits(max_gross_exposure=100), "100", "1", "exposure_budget_exceeded"),
    # One ULP above the held shares. Float quantity collapses to the position.
    ({**ORDER, "side": "SELL", "quantity": "1.000000000000000001", "limit_price": "100"},
     RiskLimits(), "1", "100", "invalid_order_or_position"),
])
def test_float_rounding_cannot_authorize_an_order_above_exact_risk_budgets(
    tmp_path, monkeypatch, order, limits, shares, price, reason,
):
    open_book(monkeypatch, tmp_path, limits=limits, shares=shares, price=price)
    # Favorable caller floats would clear the old gate. The ledger budget does not.
    context = {**CONTEXT, "limits": RiskLimits(), "position": 1_000_000, "gross": 1_000_000}
    db = Database(str(tmp_path / "broker.db"))
    transport = Transport()
    with pytest.raises(ValueError, match=reason):
        broker(db, transport).submit(order, **context)
    assert transport.calls == []
    assert db.get("broker-orders", "order-1") is None


@pytest.mark.parametrize("side,prior_average,next_average", [
    ("BUY", "50", "100"),   # Last unit implicitly costs 150, above the 100 limit.
    ("BUY", "99", "49"),    # Positive cumulative average conceals a negative fill price.
    ("BUY", "100", "50"),   # An incremental execution cannot have zero notional.
    ("SELL", "150", "100"), # Last unit implicitly sells at 50, below the 100 limit.
    ("BUY", "99.9999999999999999999999999999", "100"),
    ("SELL", "100.000000000000000000000000001", "100"),
])
def test_cumulative_average_cannot_hide_invalid_incremental_fill(
    tmp_path, monkeypatch, side, prior_average, next_average
):
    open_book(monkeypatch, tmp_path, shares="3" if side == "SELL" else "0")
    db = Database(str(tmp_path / "broker.db"))
    part = snapshot(side=side.lower(), status="partially_filled", filled_qty="1",
                    filled_avg_price=prior_average)
    next_part = {**part, "filled_qty": "2", "filled_avg_price": next_average}
    transport = Transport(part, next_part)
    context = {**CONTEXT, "position": 300, "gross": 300} if side == "SELL" else CONTEXT
    service = broker(db, transport)
    service.submit({**ORDER, "side": side}, **context)
    before = db.get("broker-orders", "order-1")
    with pytest.raises(ValueError, match="broker_snapshot_mismatch"):
        service.reconcile("order-1")
    assert db.get("broker-orders", "order-1") == before
    assert [call[0] for call in transport.calls] == ["POST", "GET"]


@pytest.mark.parametrize("later", [
    {"status": "accepted", "filled_qty": "0", "filled_avg_price": None},
    {"status": "new", "filled_qty": "0", "filled_avg_price": None},
    {"status": "partially_filled", "filled_qty": "1", "filled_avg_price": "99"},
])
def test_done_for_day_with_no_fill_cannot_resume(tmp_path, monkeypatch, later):
    open_book(monkeypatch, tmp_path)
    db = Database(str(tmp_path / "broker.db"))
    finished = snapshot(status="done_for_day", filled_qty="0", filled_avg_price=None)
    transport = Transport(finished, snapshot(**later))
    service = broker(db, transport)
    assert service.submit(ORDER, **CONTEXT) == finished
    before = db.get("broker-orders", "order-1")
    with pytest.raises(ValueError, match="broker_snapshot_mismatch"):
        service.reconcile("order-1")
    assert db.get("broker-orders", "order-1") == before
    assert before["value"]["response"]["status"] == "done_for_day"


@pytest.mark.parametrize("later", [
    {"status": "partially_filled", "filled_qty": "1", "filled_avg_price": "99"},
    {"status": "pending_cancel", "filled_qty": "1", "filled_avg_price": "99"},
    # An in-limit extra unit would pass the incremental-price check alone.
    {"status": "partially_filled", "filled_qty": "2", "filled_avg_price": "99.5"},
    {"status": "filled", "filled_qty": "3", "filled_avg_price": "99.5"},
])
def test_done_for_day_partial_cannot_reopen_or_gain_fills(tmp_path, monkeypatch, later):
    open_book(monkeypatch, tmp_path)
    db = Database(str(tmp_path / "broker.db"))
    finished = snapshot(status="done_for_day", filled_qty="1", filled_avg_price="99")
    transport = Transport(finished, snapshot(**later))
    service = broker(db, transport)
    assert service.submit(ORDER, **CONTEXT) == finished
    before = db.get("broker-orders", "order-1")
    with pytest.raises(ValueError, match="broker_snapshot_mismatch"):
        service.reconcile("order-1")
    assert db.get("broker-orders", "order-1") == before


def test_partial_day_order_can_finish_and_replay_without_further_fills(tmp_path, monkeypatch):
    open_book(monkeypatch, tmp_path)
    db = Database(str(tmp_path / "broker.db"))
    part = snapshot(status="partially_filled", filled_qty="1", filled_avg_price="99")
    done = snapshot(status="done_for_day", filled_qty="1", filled_avg_price="99")
    transport = Transport(part, done, done)
    service = broker(db, transport)
    assert service.submit(ORDER, **CONTEXT)["status"] == "partially_filled"
    assert service.reconcile("order-1") == done
    assert service.reconcile("order-1") == done
    assert [call[0] for call in transport.calls] == ["POST", "GET", "GET"]


@pytest.mark.parametrize("side,prior_average,next_average", [
    ("BUY", "50", "75"), ("SELL", "150", "125"),
    ("BUY", "100", "100"), ("SELL", "100", "100"),
])
def test_valid_incremental_limit_fills_still_reconcile(tmp_path, monkeypatch, side, prior_average, next_average):
    open_book(monkeypatch, tmp_path, shares="3" if side == "SELL" else "0")
    db = Database(str(tmp_path / "broker.db"))
    first = snapshot(side=side.lower(), status="partially_filled", filled_qty="1",
                     filled_avg_price=prior_average)
    following = {**first, "filled_qty": "2", "filled_avg_price": next_average}
    service = broker(db, Transport(first, following))
    context = {**CONTEXT, "position": 300, "gross": 300} if side == "SELL" else CONTEXT
    service.submit({**ORDER, "side": side}, **context)
    assert service.reconcile("order-1") == following
