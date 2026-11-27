"""Broker cancellation requires one intact, nonterminal persisted paper order."""
import json

import pytest

from osiris_api.control_plane import broker as broker_module
from osiris_api.control_plane.broker import AlpacaPaperBroker
from osiris_api.control_plane.clock import install_clock_health
from osiris_api.control_plane.database import Database, digest
from osiris_api.paper_execution import initialize_paper_account, set_kill_switch


class Transport:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response, error, []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if self.error is not None:
            raise self.error
        return self.response


def state(*, client_id="order-1", broker_id="broker-1", status="accepted"):
    order = {"client_order_id": client_id, "symbol": "TEST", "side": "BUY",
             "quantity": "3", "limit_price": "100"}
    filled = "3" if status == "filled" else "1" if status == "partially_filled" else "0"
    response = {"id": broker_id, "client_order_id": client_id, "symbol": "TEST",
                "side": "buy", "qty": "3", "limit_price": "100", "type": "limit",
                "time_in_force": "day", "status": status, "filled_qty": filled,
                "filled_avg_price": "99" if filled != "0" else None}
    return {"order": order, "fingerprint": digest(order),
            "status": "acknowledged", "response": response}


@pytest.fixture
def db(tmp_path):
    return Database(str(tmp_path / "broker.db"))


def service(db, transport, *, enabled=True):
    return AlpacaPaperBroker(db, {"key": "FIXTURE", "secret": "FIXTURE"},
                             enabled=enabled, transport=transport)


def rejected(db, value, reason):
    db.put("broker-orders", "order-1", value)
    before = db.get("broker-orders", "order-1")
    transport = Transport({"unexpected": "transport was called"})
    with pytest.raises(ValueError, match=f"^{reason}$"):
        service(db, transport).cancel("broker-1")
    assert transport.calls == []
    assert db.get("broker-orders", "order-1") == before


def test_unknown_broker_id_never_reaches_transport(db):
    db.put("broker-orders", "order-1", state())
    transport = Transport()
    with pytest.raises(ValueError, match="^unknown_broker_order$"):
        service(db, transport).cancel("never-authorized")
    assert transport.calls == []


def test_duplicate_broker_id_is_ambiguous_even_when_one_record_is_valid(db):
    db.put("broker-orders", "order-1", state())
    db.put("broker-orders", "order-2", state(client_id="order-2"))
    before = db.list("broker-orders")
    transport = Transport()
    with pytest.raises(ValueError, match="^ambiguous_broker_order$"):
        service(db, transport).cancel("broker-1")
    assert transport.calls == []
    assert db.list("broker-orders") == before


@pytest.mark.parametrize("change", ["missing_order", "non_object_order", "missing_key",
                                  "extra_key", "fingerprint", "missing_fingerprint",
                                  "client_id_binding"])
def test_cancel_requires_complete_digest_and_client_bound_intent(db, change):
    value = state()
    if change == "missing_order":
        value.pop("order")
    elif change == "non_object_order":
        value["order"] = []
    elif change == "fingerprint":
        value["fingerprint"] = "0" * 64
    elif change == "missing_fingerprint":
        value.pop("fingerprint")
    else:
        if change == "missing_key":
            value["order"].pop("side")
        elif change == "extra_key":
            value["order"]["unreviewed"] = True
        else:
            value["order"]["client_order_id"] = "another-client"
            value["response"]["client_order_id"] = "another-client"
        value["fingerprint"] = digest(value["order"])
    rejected(db, value, "broker_order_contract_required")


@pytest.mark.parametrize("change", [{"symbol": "../invalid"}, {"symbol": None}, {"side": "HOLD"}])
def test_invalid_intent_fields_cannot_authorize_cancel(db, change):
    value = state()
    value["order"].update(change)
    value["fingerprint"] = digest(value["order"])
    rejected(db, value, "broker_order_contract_required")


def test_invalid_persisted_client_identifier_cannot_authorize_cancel(db):
    value = state(client_id="../invalid")
    with db.transaction() as conn:
        conn.execute("INSERT INTO cp_documents VALUES ('broker-orders',?,1,?)",
                     ("../invalid", json.dumps(value)))
    transport = Transport()
    with pytest.raises(ValueError, match="^broker_order_contract_required$"):
        service(db, transport).cancel("broker-1")
    assert transport.calls == []


@pytest.mark.parametrize("local_status", ["submitting", "unknown", None])
def test_unacknowledged_outcome_requires_reconciliation(db, local_status):
    value = state()
    value["status"] = local_status
    rejected(db, value, "order_outcome_unknown_reconcile_first")


@pytest.mark.parametrize("change", [
    {"symbol": "OTHER"}, {"client_order_id": "OTHER"}, {"qty": "4"},
    {"limit_price": "101"}, {"status": "unknown"}, {"status": "filled"},
])
def test_cancel_revalidates_persisted_broker_snapshot(db, change):
    value = state()
    value["response"].update(change)
    rejected(db, value, "broker_snapshot_mismatch")


def test_matching_id_without_snapshot_contract_is_rejected(db):
    value = state()
    value["response"] = {"id": "broker-1"}
    rejected(db, value, "broker_snapshot_mismatch")


@pytest.mark.parametrize("response", [None, [], "broken", {}, {"id": "another-id"}])
def test_missing_or_unmapped_response_cannot_authorize_cancel(db, response):
    value = state()
    value["response"] = response
    rejected(db, value, "unknown_broker_order")


@pytest.mark.parametrize("status", ["filled", "canceled", "expired", "rejected", "done_for_day"])
def test_terminal_broker_orders_cannot_be_canceled(db, status):
    rejected(db, state(status=status), "broker_order_terminal")


def test_pending_cancel_requires_reconciliation_before_another_delete(db):
    rejected(db, state(status="pending_cancel"), "order_outcome_unknown_reconcile_first")


@pytest.mark.parametrize("status", [
    "new", "pending_new", "accepted", "accepted_for_bidding", "partially_filled",
    "stopped", "suspended", "calculated",
])
def test_nonterminal_cancel_returns_raw_transport_result_without_snapshot_write(db, status):
    db.put("broker-orders", "order-1", state(status=status))
    before = db.get("broker-orders", "order-1")
    raw = {"cancel_request": "accepted"}
    transport = Transport(raw)
    assert service(db, transport).cancel("broker-1") is raw
    assert [(method, url) for method, url, _ in transport.calls] == [
        ("DELETE", "https://paper-api.alpaca.markets/v2/orders/broker-1"),
    ]
    assert db.get("broker-orders", "order-1") == before


@pytest.mark.parametrize("fails", [False, True])
def test_empty_success_or_transport_failure_never_marks_snapshot_canceled(db, fails):
    db.put("broker-orders", "order-1", state(status="partially_filled"))
    before = db.get("broker-orders", "order-1")
    error = ValueError("upstream_request_failed") if fails else None
    transport = Transport(error=error)
    adapter = service(db, transport)
    if fails:
        with pytest.raises(ValueError, match="^upstream_request_failed$") as caught:
            adapter.cancel("broker-1")
        assert caught.value is error
    else:
        assert adapter.cancel("broker-1") is None
    assert len(transport.calls) == 1
    assert db.get("broker-orders", "order-1") == before


def test_cancel_lookup_is_not_truncated_at_database_list_limit(db):
    with db.transaction() as conn:
        conn.executemany("INSERT INTO cp_documents VALUES ('broker-orders',?,1,?)", [
            (f"order-{index:04d}", json.dumps(state(
                client_id=f"order-{index:04d}", broker_id=f"broker-{index:04d}",
            ))) for index in range(1001)
        ])
    transport = Transport({})
    assert service(db, transport).cancel("broker-1000") == {}
    assert transport.calls[0][1].endswith("/broker-1000")
    assert db.get("broker-orders", "order-1000")["version"] == 1


def test_malformed_unrelated_rows_cannot_authorize_or_block_known_cancel(db):
    with db.transaction() as conn:
        conn.executemany("INSERT INTO cp_documents VALUES ('broker-orders',?,1,?)", [
            ("broken-json", "{"), ("non-object", "[]"), ("null", "null"),
            ("no-response", "{}"), ("invalid-response", '{"response": []}'),
        ])
    db.put("broker-orders", "order-1", state())
    before = db.get("broker-orders", "order-1")
    transport = Transport({})
    assert service(db, transport).cancel("broker-1") == {}
    assert len(transport.calls) == 1
    assert db.get("broker-orders", "order-1") == before


def test_disabled_broker_cannot_cancel_even_a_known_open_order(db):
    db.put("broker-orders", "order-1", state())
    transport = Transport()
    with pytest.raises(ValueError, match="^paper_broker_not_enabled$"):
        service(db, transport, enabled=False).cancel("broker-1")
    assert transport.calls == []


@pytest.mark.parametrize("broker_id", [None, "", "../broker", "a/b", "x" * 129])
def test_invalid_broker_ids_are_rejected_before_transport(db, broker_id):
    transport = Transport()
    with pytest.raises(ValueError, match="^invalid_identifier$"):
        service(db, transport).cancel(broker_id)
    assert transport.calls == []


def test_cancel_does_not_depend_on_new_risk_or_clock_health(db, tmp_path, monkeypatch):
    monkeypatch.setenv("OSIRIS_PAPER_EXECUTION_DB", str(tmp_path / "paper.db"))
    initialize_paper_account("1000")
    set_kill_switch(True)
    install_clock_health({"status": "degraded", "orders_fail_closed": True})

    def no_new_risk(*args, **kwargs):
        raise AssertionError("cancel must remain available while new risk is halted")

    monkeypatch.setattr(broker_module, "authorize_paper_submission", no_new_risk)
    db.put("broker-orders", "order-1", state())
    transport = Transport({})
    assert service(db, transport).cancel("broker-1") == {}
    assert len(transport.calls) == 1
