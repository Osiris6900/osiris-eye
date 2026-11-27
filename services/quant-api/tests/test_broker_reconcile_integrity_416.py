"""Reconciliation must retain the identity and progress of the saved order."""

import pytest

from osiris_api.control_plane.broker import AlpacaPaperBroker
from osiris_api.control_plane.database import Database, digest


def state(*, client_id="order-1", status="accepted", local_status="acknowledged"):
    order = {"client_order_id": client_id, "symbol": "TEST", "side": "BUY",
             "quantity": "3", "limit_price": "100"}
    filled = "3" if status == "filled" else "1" if status == "partially_filled" else "0"
    response = {"id": "broker-1", "client_order_id": client_id, "symbol": "TEST",
                "side": "buy", "qty": "3", "limit_price": "100", "type": "limit",
                "time_in_force": "day", "status": status, "filled_qty": filled,
                "filled_avg_price": "99" if filled != "0" else None}
    return {"order": order, "fingerprint": digest(order),
            "status": local_status, "response": response}


class Transport:
    def __init__(self, response=None, error=None, before_return=None):
        self.response, self.error = response, error
        self.before_return, self.calls = before_return, []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if self.before_return is not None:
            self.before_return()
        if self.error is not None:
            raise self.error
        return self.response


@pytest.fixture
def db(tmp_path):
    return Database(str(tmp_path / "broker.db"))


def service(db, transport):
    return AlpacaPaperBroker(db, {"key": "FIXTURE", "secret": "FIXTURE"},
                             enabled=True, transport=transport)


def rejected_before_http(db, value, reason):
    db.put("broker-orders", "order-1", value)
    before = db.get("broker-orders", "order-1")
    transport = Transport(state()["response"])
    with pytest.raises(ValueError, match=f"^{reason}$"):
        service(db, transport).reconcile("order-1")
    assert transport.calls == []
    assert db.get("broker-orders", "order-1") == before


@pytest.mark.parametrize("value", [None, [], "broken", {}])
def test_malformed_persisted_state_is_refused_before_http(db, value):
    rejected_before_http(db, value, "broker_order_contract_required")


@pytest.mark.parametrize("change", [
    "missing_order", "non_object_order", "missing_key", "extra_key",
    "wrong_fingerprint", "missing_fingerprint", "different_client",
])
def test_persisted_intent_requires_exact_digest_and_database_client_binding(db, change):
    value = state()
    if change == "missing_order":
        value.pop("order")
    elif change == "non_object_order":
        value["order"] = []
    elif change == "wrong_fingerprint":
        value["fingerprint"] = "0" * 64
    elif change == "missing_fingerprint":
        value.pop("fingerprint")
    else:
        if change == "missing_key":
            value["order"].pop("side")
        elif change == "extra_key":
            value["order"]["unreviewed"] = True
        else:
            value["order"]["client_order_id"] = "other-client"
            value["response"]["client_order_id"] = "other-client"
        value["fingerprint"] = digest(value["order"])
    rejected_before_http(db, value, "broker_order_contract_required")


@pytest.mark.parametrize("change", [
    {"client_order_id": "../invalid"}, {"symbol": "../invalid"}, {"side": "HOLD"},
    {"quantity": "0"}, {"quantity": "-1"}, {"quantity": "NaN"},
    {"limit_price": "0"}, {"limit_price": "Infinity"}, {"limit_price": True},
])
def test_invalid_persisted_order_fields_are_contract_failures_before_http(db, change):
    value = state()
    value["order"].update(change)
    value["fingerprint"] = digest(value["order"])
    rejected_before_http(db, value, "broker_order_contract_required")


@pytest.mark.parametrize("status", [None, "unknown", "completed", [], {}])
def test_unknown_local_outcomes_cannot_be_promoted_by_reconciliation(db, status):
    rejected_before_http(db, state(local_status=status), "order_outcome_unknown_reconcile_first")


@pytest.mark.parametrize("raw", ["{", "[" * 20_000 + '"leaf"' + "]" * 20_000])
def test_unparseable_saved_json_cannot_reach_http_or_overwrite_raw_evidence(db, raw):
    with db.transaction() as conn:
        conn.execute("INSERT INTO cp_documents VALUES ('broker-orders','order-1',1,?)", (raw,))
    transport = Transport(state()["response"])
    with pytest.raises(ValueError, match="^broker_order_contract_required$"):
        service(db, transport).reconcile("order-1")
    assert transport.calls == []
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT version,body FROM cp_documents WHERE collection='broker-orders' AND id='order-1'"
        ).fetchone()
    assert tuple(row) == (1, raw)


@pytest.mark.parametrize("local_status", ["acknowledged", "submitting"])
@pytest.mark.parametrize("response", [None, [], "broken", {}])
def test_any_present_invalid_prior_snapshot_is_refused_before_http(db, local_status, response):
    value = state(local_status=local_status)
    value["response"] = response
    rejected_before_http(db, value, "broker_snapshot_mismatch")


def test_acknowledged_order_requires_a_prior_snapshot(db):
    value = state()
    value.pop("response")
    rejected_before_http(db, value, "broker_snapshot_mismatch")


@pytest.mark.parametrize("change", [
    {"client_order_id": "other-client"}, {"symbol": "OTHER"}, {"side": "sell"},
    {"status": "unknown"}, {"status": "filled"}, {"qty": "4"},
])
def test_prior_snapshot_is_fully_validated_before_http(db, change):
    value = state()
    value["response"].update(change)
    rejected_before_http(db, value, "broker_snapshot_mismatch")


def test_genuine_submitting_intent_without_response_can_recover(db):
    value = state(local_status="submitting")
    value.pop("response")
    db.put("broker-orders", "order-1", value)
    response = state()["response"]
    transport = Transport(response)
    assert service(db, transport).reconcile("order-1") is response
    persisted = db.get("broker-orders", "order-1")
    assert persisted["version"] == 2
    assert persisted["value"] == {**value, "status": "acknowledged", "response": response}
    assert [(method, url) for method, url, _ in transport.calls] == [
        ("GET", "https://paper-api.alpaca.markets/v2/orders:by_client_order_id?client_order_id=order-1"),
    ]


@pytest.mark.parametrize("before_status,after_status", [
    ("accepted", "partially_filled"), ("partially_filled", "filled"),
    ("filled", "filled"), ("canceled", "canceled"), ("done_for_day", "done_for_day"),
])
def test_valid_execution_progress_and_terminal_replay_still_persist(db, before_status, after_status):
    before = state(status=before_status)
    db.put("broker-orders", "order-1", before)
    response = state(status=after_status)["response"]
    transport = Transport(response)
    assert service(db, transport).reconcile("order-1") is response
    assert len(transport.calls) == 1
    assert db.get("broker-orders", "order-1") == {
        "version": 2, "value": {**before, "response": response},
    }


def test_integer_intent_amounts_and_mixed_case_side_keep_their_original_contract(db):
    before = state()
    before["order"].update(quantity=3, limit_price=100, side="bUy")
    before["fingerprint"] = digest(before["order"])
    db.put("broker-orders", "order-1", before)
    response = state(status="partially_filled")["response"]
    response.update(qty="3.000", limit_price="100.00", filled_qty="1.00", filled_avg_price="99.0")
    transport = Transport(response)
    assert service(db, transport).reconcile("order-1") is response
    assert len(transport.calls) == 1
    assert db.get("broker-orders", "order-1") == {
        "version": 2, "value": {**before, "response": response},
    }


@pytest.mark.parametrize("change", [
    {"id": "another-broker"}, {"client_order_id": "other-client"},
    {"symbol": "OTHER"}, {"filled_qty": "0", "filled_avg_price": None, "status": "accepted"},
])
def test_invalid_upstream_snapshot_preserves_valid_prior_progress(db, change):
    before = state(status="partially_filled")
    db.put("broker-orders", "order-1", before)
    response = {**before["response"], **change}
    transport = Transport(response)
    with pytest.raises(ValueError, match="^broker_snapshot_mismatch$"):
        service(db, transport).reconcile("order-1")
    assert len(transport.calls) == 1
    assert db.get("broker-orders", "order-1") == {"version": 1, "value": before}


def test_http_failure_preserves_saved_intent_and_snapshot(db):
    before = state(status="partially_filled")
    db.put("broker-orders", "order-1", before)
    error = ValueError("upstream_request_failed")
    transport = Transport(error=error)
    with pytest.raises(ValueError, match="^upstream_request_failed$") as caught:
        service(db, transport).reconcile("order-1")
    assert caught.value is error
    assert len(transport.calls) == 1
    assert db.get("broker-orders", "order-1") == {"version": 1, "value": before}


def test_reconciliation_cas_conflict_cannot_overwrite_a_newer_terminal_snapshot(db):
    db.put("broker-orders", "order-1", state())
    newer = state(status="filled")

    def concurrent_reconcile():
        db.put("broker-orders", "order-1", newer, expected_version=1)

    transport = Transport(state(status="partially_filled")["response"],
                          before_return=concurrent_reconcile)
    with pytest.raises(ValueError, match="^version_conflict$"):
        service(db, transport).reconcile("order-1")
    assert len(transport.calls) == 1
    assert db.get("broker-orders", "order-1") == {"version": 2, "value": newer}
