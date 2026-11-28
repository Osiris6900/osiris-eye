"""Configured broker inventory cannot bypass native reservation checks."""

import json
import sqlite3
from datetime import UTC, datetime

import pytest
from test_pretrade_risk_gateway_416 import FAVORABLE, _order, open_book

from osiris_api import paper_execution as ledger
from osiris_api.control_plane.broker import AlpacaPaperBroker
from osiris_api.control_plane.database import Database, digest
from osiris_api.paper_broker_reservations import (
    broker_binding,
    configured_inventory_reason,
    synchronize_broker_reservation,
)
from osiris_api.risk import RiskLimits

REASON = "broker_reservation_required_reconcile_first"


@pytest.fixture
def control(tmp_path, monkeypatch):
    monkeypatch.delenv("OSIRIS_CONTROL_DB", raising=False)
    open_book(monkeypatch, tmp_path, limits=RiskLimits(
        max_order_notional=1e9, max_position_notional=1e9,
        max_daily_loss=1e9, max_gross_exposure=1e9,
    ))
    database = Database(str(tmp_path / "control.sqlite3"))
    monkeypatch.setenv("OSIRIS_CONTROL_DB", database.path)
    return database


def _response(*, status="accepted", filled="0"):
    order = _order()
    return {
        "id": "broker-1", "client_order_id": order["client_order_id"],
        "symbol": order["symbol"], "side": "buy", "qty": order["quantity"],
        "limit_price": order["limit_price"], "type": "limit", "time_in_force": "day",
        "status": status, "filled_qty": filled,
        "filled_avg_price": "100" if filled != "0" else None,
    }


def _state(response=None, *, status="acknowledged"):
    order = _order()
    result = {"order": order, "fingerprint": digest(order), "status": status}
    if response is not None:
        result["response"] = response
    return result


def _reason():
    with ledger._lock:
        connection = ledger._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            return configured_inventory_reason(connection)
        finally:
            ledger._rollback(connection)
            connection.close()


def test_unconfigured_inventory_needs_no_control_store(control, monkeypatch):
    monkeypatch.delenv("OSIRIS_CONTROL_DB")
    assert _reason() is None


def test_empty_configured_inventory_remains_valid(control):
    assert _reason() is None


@pytest.mark.parametrize("kind", ["missing", "schema", "not-sqlite"])
def test_missing_or_invalid_configured_store_blocks_new_risk(
    control, tmp_path, monkeypatch, kind,
):
    path = tmp_path / "invalid-control.sqlite3"
    if kind == "schema":
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE unrelated (value TEXT)")
    elif kind == "not-sqlite":
        path.write_bytes(b"invalid sqlite file")
    monkeypatch.setenv("OSIRIS_CONTROL_DB", str(path))
    assert _reason() == REASON
    if kind == "missing":
        assert not path.exists()


@pytest.mark.parametrize("status", ["acknowledged", "submitting"])
def test_legacy_uncertain_or_open_intent_requires_reconciliation(control, status):
    state = _state(_response()) if status == "acknowledged" else _state(status=status)
    control.put("broker-orders", "order-1", state)
    assert _reason() == REASON


@pytest.mark.parametrize("status", ["canceled", "expired", "rejected", "done_for_day"])
def test_unreserved_zero_fill_terminal_evidence_still_requires_fresh_get(control, status):
    control.put("broker-orders", "order-1", _state(_response(status=status)))
    # A mutable legacy document can claim terminality while the real broker
    # order remains open. Only explicit GET adoption can establish no risk.
    assert _reason() == REASON


def test_unreserved_terminal_fill_requires_accounting(control):
    control.put("broker-orders", "order-1", _state(_response(status="filled", filled="1")))
    assert _reason() == REASON


@pytest.mark.parametrize("corruption", [
    "bad-json", "deep-json", "oversized", "wrong-db-id", "bad-fingerprint",
    "missing-response", "unknown-status", "invalid-response", "null-submitting-response",
])
def test_configured_inventory_rejects_corrupt_evidence(control, corruption):
    key = "order-1"
    state = _state(_response())
    if corruption == "wrong-db-id":
        key = "another-order"
    elif corruption == "bad-fingerprint":
        state["fingerprint"] = "0" * 64
    elif corruption == "missing-response":
        del state["response"]
    elif corruption == "unknown-status":
        state["status"] = "unknown"
    elif corruption == "invalid-response":
        state["response"]["filled_qty"] = "2"
    elif corruption == "null-submitting-response":
        state = {**_state(status="submitting"), "response": None}
    raw = json.dumps(state)
    if corruption == "bad-json":
        raw = "["
    elif corruption == "deep-json":
        raw = "[" * 2000 + "0" + "]" * 2000
    elif corruption == "oversized":
        raw = '"' + "a" * 1_048_576 + '"'
    with control.transaction() as connection:
        connection.execute(
            "INSERT INTO cp_documents VALUES ('broker-orders',?,1,?)", (key, raw),
        )
    assert _reason() == REASON


def test_matching_progress_is_required_when_a_canonical_reservation_exists(control):
    order = _order()
    accepted = _response()
    control.put("broker-orders", "order-1", _state(accepted))
    binding = broker_binding(control.path, order)
    synchronize_broker_reservation(binding, order, accepted)
    assert _reason() is None

    # Simulate the CP CAS committing before its canonical progress update.
    canceled = _response(status="canceled")
    control.put("broker-orders", "order-1", _state(canceled), expected_version=1)
    assert _reason() == REASON
    synchronize_broker_reservation(binding, order, canceled)
    assert _reason() is None

    # A stale retained CP snapshot cannot conceal known canonical progress.
    control.put("broker-orders", "order-1", _state(accepted), expected_version=2)
    assert _reason() == REASON


def test_canonical_reservation_cannot_be_rebound_to_another_control_store(
    control, tmp_path, monkeypatch,
):
    response = _response()
    synchronize_broker_reservation(broker_binding(control.path, _order()), _order(), response)
    other = Database(str(tmp_path / "other-control.sqlite3"))
    other.put("broker-orders", "order-1", _state(response))
    monkeypatch.setenv("OSIRIS_CONTROL_DB", other.path)
    assert _reason() == REASON


def test_shared_control_and_canonical_file_uses_the_callers_transaction(
    control, tmp_path, monkeypatch,
):
    shared = Database(str(tmp_path / "paper.sqlite3"))
    response = _response()
    shared.put("broker-orders", "order-1", _state(response))
    monkeypatch.setenv("OSIRIS_CONTROL_DB", shared.path)
    synchronize_broker_reservation(broker_binding(shared.path, _order()), _order(), response)
    # BEGIN IMMEDIATE is already held by _reason; acquiring another write
    # transaction on this file would time out instead of returning cleanly.
    assert _reason() is None


def test_legacy_external_fill_cannot_adopt_a_native_client_id(control):
    stamp = datetime.now(UTC).isoformat()
    native = ledger.submit_paper_order(
        client_order_id="order-1", symbol="TEST", side="BUY", quantity="1", price="100",
        fee="0", observed_at=stamp, now=stamp, working=True,
    )
    assert native["state"] == "accepted"
    response = _response(status="filled", filled="1")
    control.put("broker-orders", "order-1", _state(response))
    assert _reason() == REASON
    with pytest.raises(ValueError, match="broker_reservation_conflict"):
        synchronize_broker_reservation(broker_binding(control.path, _order()), _order(), response)
    assert ledger.snapshot()["broker_reservations"] == []


def test_external_zero_fill_open_commitment_matches_independent_broker_book(control):
    order = _order()
    response = _response()
    control.put("broker-orders", "order-1", _state(response))
    synchronize_broker_reservation(broker_binding(control.path, order), order, response)
    stamp = datetime.now(UTC).isoformat()
    book = {
        "schema": ledger.BROKER_BOOK_SCHEMA, "mode": "paper", "as_of": stamp,
        "cash": "1000000", "buying_power": "999900", "positions": [], "fills": [],
        "open_orders": [{
            "client_order_id": "order-1", "symbol": "TEST", "side": "BUY",
            "quantity": "1", "filled_quantity": "0", "leaves_quantity": "1",
        }],
    }
    control.put("broker-book", "account", {**book, "digest": ledger.broker_book_digest(book)})
    report = ledger.reconcile_broker_book(now=stamp)
    assert report["status"] == "clean", report
    assert report["breaks"] == []
    assert ledger.snapshot()["fill_count"] == 0
    assert ledger.snapshot()["positions"] == []


class _RecordedBroker:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def request(self, method, url, **_kwargs):
        self.calls.append((method, url))
        assert method == "GET", "a retained retry must never submit another order"
        return dict(self.response)


def _service(control, transport):
    return AlpacaPaperBroker(
        control, {"key": "FIXTURE", "secret": "FIXTURE"}, enabled=True, transport=transport,
    )


@pytest.mark.parametrize("terminal", ["canceled", "expired", "rejected", "done_for_day"])
def test_retained_cp_terminal_edit_cannot_release_capacity_on_submit_replay(control, terminal):
    order, accepted = _order(), _response()
    control.put("broker-orders", "order-1", _state(accepted))
    synchronize_broker_reservation(broker_binding(control.path, order), order, accepted)
    before = ledger.snapshot()
    transport = _RecordedBroker(accepted)
    service = _service(control, transport)

    # The forged body is structurally valid, correctly bound to the intent,
    # and has no fills. It is still only mutable retained JSON, not a GET.
    forged = _response(status=terminal)
    control.put("broker-orders", "order-1", _state(forged), expected_version=1)
    with pytest.raises(ValueError, match="^order_outcome_unknown_reconcile_first$"):
        service.submit(order, **FAVORABLE)
    assert transport.calls == []
    assert ledger.snapshot()["broker_reservations"] == before["broker_reservations"]
    assert ledger.snapshot()["audit"] == before["audit"]

    # A genuine still-open broker response cannot implicitly repair a
    # terminal local record or release its independently retained capacity.
    with pytest.raises(ValueError, match="^broker_snapshot_mismatch$"):
        service.reconcile("order-1")
    assert [method for method, _url in transport.calls] == ["GET"]
    assert control.get("broker-orders", "order-1")["value"]["response"] == forged
    assert ledger.snapshot()["broker_reservations"] == before["broker_reservations"]
    assert ledger.snapshot()["audit"] == before["audit"]
    assert ledger.reconcile()["passed"] is True


@pytest.mark.parametrize("status,filled,reserved", [
    ("accepted", "0", "1"), ("canceled", "0", "0"), ("filled", "1", "1"),
])
def test_legacy_acknowledged_submit_never_adopts_evidence_without_explicit_get(
    control, status, filled, reserved,
):
    response = _response(status=status, filled=filled)
    control.put("broker-orders", "order-1", _state(response))
    transport = _RecordedBroker(response)
    service = _service(control, transport)
    before = ledger.snapshot()["audit"]
    with pytest.raises(ValueError, match="^order_outcome_unknown_reconcile_first$"):
        service.submit(_order(), **FAVORABLE)
    assert transport.calls == []
    assert ledger.snapshot()["broker_reservations"] == []
    assert ledger.snapshot()["audit"] == before

    assert service.reconcile("order-1") == response
    assert [method for method, _url in transport.calls] == ["GET"]
    reservations = ledger.snapshot()["broker_reservations"]
    assert len(reservations) == 1
    assert reservations[0]["reserved_quantity"] == reserved
    assert reservations[0]["accounting_pending"] is (filled != "0")
    assert ledger.reconcile()["passed"] is True
