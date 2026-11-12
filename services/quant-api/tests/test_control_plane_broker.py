"""An upstream client ID cannot override the risk-approved order contract."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from osiris_api.control_plane.broker import AlpacaPaperBroker
from osiris_api.control_plane.database import Database, digest
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
def test_unbound_acknowledgement_stays_uncertain_until_valid_reconciliation(tmp_path, changes):
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


def test_partial_full_fill_progress_and_exact_decimal_equivalence_survive_restart(tmp_path):
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


@pytest.mark.parametrize("changes", [
    {"id": "different-broker-order"}, {"filled_qty": "0", "filled_avg_price": None},
    {"filled_avg_price": "98"}, {"symbol": "OTHER"},
])
def test_regressing_or_conflicting_snapshot_preserves_last_verified_state(tmp_path, changes):
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
def test_terminal_orders_cannot_reopen_or_change_execution_progress(tmp_path, terminal, filled, average):
    db = Database(str(tmp_path / "broker.db"))
    final = snapshot(status=terminal, filled_qty=filled, filled_avg_price=average)
    transport = Transport(final, snapshot())
    service = broker(db, transport)
    service.submit(ORDER, **CONTEXT)
    with pytest.raises(ValueError, match="broker_snapshot_mismatch"):
        service.reconcile("order-1")
    assert db.get("broker-orders", "order-1")["value"]["response"] == final


def test_sell_acknowledgement_cannot_claim_a_fill_below_the_sell_limit(tmp_path):
    db = Database(str(tmp_path / "broker.db"))
    service = broker(db, Transport(snapshot(side="sell", status="filled", filled_qty="3",
                                           filled_avg_price="99")))
    with pytest.raises(ValueError, match="order_outcome_unknown"):
        service.submit({**ORDER, "side": "SELL"}, **{**CONTEXT, "position": 300, "gross": 300})


def test_legacy_client_id_only_record_requires_trusted_intent_before_reconciliation(tmp_path):
    db = Database(str(tmp_path / "broker.db"))
    db.put("broker-orders", "order-1", {"fingerprint": digest(ORDER), "status": "submitting"})
    transport = Transport(snapshot())
    with pytest.raises(ValueError, match="broker_order_contract_required"):
        broker(db, transport).reconcile("order-1")
    with pytest.raises(ValueError, match="broker_order_contract_required"):
        broker(db, transport).submit(ORDER, **CONTEXT)
    assert not transport.calls


def test_concurrent_retry_and_changed_intent_cannot_submit_a_second_order(tmp_path):
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
