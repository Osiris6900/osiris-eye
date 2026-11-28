"""External PAPER acknowledgements reserve the same durable book as native orders.

All transport is in memory. An acknowledgement never invents a canonical fill,
and a cancellation request never establishes terminal broker state.
"""
import json
import os
import sqlite3
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event
from urllib.parse import parse_qs, urlsplit

import pytest
from test_pretrade_risk_gateway_416 import FAVORABLE, _order, open_book

from osiris_api.control_plane.broker import AlpacaPaperBroker
from osiris_api.control_plane.database import Database, digest
from osiris_api.paper_execution import (
    cancel_paper_order,
    expire_paper_orders,
    mark_to_market,
    reconcile,
    recover,
    snapshot,
    submit_paper_order,
)
from osiris_api.risk import RiskLimits


def _limits(**changes):
    values = {"max_order_notional": 1e9, "max_position_notional": 1e9,
              "max_daily_loss": 1e9, "max_gross_exposure": 1e9}
    values.update(changes)
    return RiskLimits(**values)


class Transport:
    def __init__(self):
        self.calls = []
        self.responses = {}
        self.before_post = None
        self.post_error = False
        self.get_error = False
        self.delete_error = False

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if method == "POST":
            body = kwargs["body"]
            client_id = body["client_order_id"]
            if self.before_post:
                self.before_post(client_id)
            self.responses[client_id] = {
                **body, "id": "broker-" + client_id, "status": "accepted",
                "filled_qty": "0", "filled_avg_price": None,
            }
            if self.post_error:
                raise TimeoutError("response lost after request was sent")
            return dict(self.responses[client_id])
        if method == "GET":
            if self.get_error:
                raise TimeoutError("reconciliation response lost")
            client_id = parse_qs(urlsplit(url).query)["client_order_id"][0]
            return dict(self.responses[client_id])
        if method == "DELETE":
            if self.delete_error:
                raise TimeoutError("cancel response lost")
            return None  # HTTP 204 has no order snapshot.
        raise AssertionError("unexpected transport method")

    def change(self, client_id="order-1", *, status, filled="0"):
        self.responses[client_id].update(
            status=status, filled_qty=filled,
            filled_avg_price="100" if filled != "0" else None,
        )

    @property
    def posts(self):
        return [call for call in self.calls if call[0] == "POST"]


def _broker(tmp_path, transport, name="broker.db"):
    return AlpacaPaperBroker(
        Database(str(tmp_path / name)), {"key": "FIXTURE", "secret": "FIXTURE"},
        enabled=True, transport=transport,
    )


def _reservation(client_id="order-1"):
    return next(row for row in snapshot()["broker_reservations"]
                if row["client_order_id"] == client_id)


def _record(client_id="order-1"):
    with sqlite3.connect(os.environ["OSIRIS_PAPER_EXECUTION_DB"]) as conn:
        row = conn.execute(
            "SELECT body FROM paper_exec_broker_reservations WHERE client_order_id=?",
            (client_id,),
        ).fetchone()
    return json.loads(row[0])


def _events():
    return [row for row in snapshot()["audit"] if row["event"] == "broker_reservation_changed"]


def _native(order, *, working=True):
    stamp = datetime.now(UTC).isoformat()
    return submit_paper_order(
        client_order_id=order["client_order_id"], symbol=order["symbol"], side=order["side"],
        quantity=order["quantity"], price=order["limit_price"], fee="0",
        observed_at=stamp, now=stamp, working=working,
    )


def _accept(kind, service, order):
    if kind == "native":
        result = _native(order)
        assert result["status"] == "accepted", result
    else:
        assert service.submit(order, **FAVORABLE)["status"] == "accepted"


def _reject(kind, service, order, reason):
    if kind == "native":
        result = _native(order)
        assert result["status"] in {"risk_rejected", "execution_rejected"}, result
        assert result["reason"] == reason, result
    else:
        with pytest.raises(ValueError, match=reason):
            service.submit(order, **FAVORABLE)


def test_reservation_is_durable_and_bound_before_http_without_inventing_fill(tmp_path, monkeypatch):
    open_book(monkeypatch, tmp_path)
    transport = Transport()
    service = _broker(tmp_path, transport)
    order = _order()

    def before_post(client_id):
        reservation = _reservation(client_id)
        record = _record(client_id)
        assert record["order"] == order
        assert record["response"] is None
        assert reservation["reserved_quantity"] == "1"
        assert reservation["filled_quantity"] == "0"
        assert reservation["accounting_pending"] is False
        assert reservation["binding"] == digest({
            "broker": "alpaca-paper", "control_db": service.db.path, "order": order,
        })
        assert reconcile()["passed"] is True

    transport.before_post = before_post
    response = service.submit(order, **FAVORABLE)
    state = snapshot()
    assert _record()["response"] == response
    assert state["fill_count"] == 0
    assert state["positions"] == []
    assert state["cash"] == "1000000"
    assert state["reserved_cash"] == "100"
    assert state["available_cash"] == "999900"
    assert state["broker_accounting_pending"] is False
    assert state["live_execution"] is False
    assert len(transport.posts) == 1
    assert reconcile()["passed"] is True
    child = subprocess.run(
        [sys.executable, "-c", "import json; from osiris_api.paper_execution import recover; print(json.dumps(recover()))"],
        capture_output=True, text=True, env=os.environ.copy(), check=True, timeout=20,
    )
    recovered = json.loads(child.stdout)
    assert recovered["passed"] is True
    assert recovered["snapshot"]["broker_reservations"] == state["broker_reservations"]


@pytest.mark.parametrize("first,second", [("broker", "broker"), ("broker", "native"), ("native", "broker")])
@pytest.mark.parametrize("limit,reason", [
    ("max_position_notional", "max_position_notional_exceeded"),
    ("max_gross_exposure", "exposure_budget_exceeded"),
])
def test_pending_native_and_external_buys_share_exposure_limits(tmp_path, monkeypatch, first, second, limit, reason):
    open_book(monkeypatch, tmp_path, limits=_limits(**{limit: 150}))
    transport = Transport()
    service = _broker(tmp_path, transport)
    _accept(first, service, _order())
    _reject(second, service, _order(client_order_id="order-2"), reason)
    assert len(transport.posts) == (1 if first == "broker" else 0)
    assert snapshot()["fill_count"] == 0
    assert reconcile()["passed"] is True


def test_external_buy_counts_toward_gross_on_another_symbol(tmp_path, monkeypatch):
    stamp = open_book(monkeypatch, tmp_path, limits=_limits(max_gross_exposure=150))
    mark_to_market({"BTC-USD": "100"}, observed_at=stamp, now=stamp)
    transport = Transport()
    service = _broker(tmp_path, transport)
    service.submit(_order(), **FAVORABLE)
    _reject("broker", service, _order(client_order_id="other", symbol="BTC-USD"), "exposure_budget_exceeded")
    assert len(transport.posts) == 1


@pytest.mark.parametrize("first,second", [("broker", "broker"), ("broker", "native"), ("native", "broker")])
def test_pending_orders_share_cash_reservations(tmp_path, monkeypatch, first, second):
    open_book(monkeypatch, tmp_path, limits=_limits())
    transport = Transport()
    service = _broker(tmp_path, transport)
    _accept(first, service, _order(quantity="6000"))
    _reject(second, service, _order(client_order_id="order-2", quantity="5000"), "insufficient_paper_cash")
    assert snapshot()["cash"] == "1000000"
    assert reconcile()["passed"] is True


@pytest.mark.parametrize("first,second", [("broker", "broker"), ("broker", "native"), ("native", "broker")])
def test_pending_sells_share_held_quantity(tmp_path, monkeypatch, first, second):
    open_book(monkeypatch, tmp_path, limits=_limits(), shares="10")
    transport = Transport()
    service = _broker(tmp_path, transport)
    _accept(first, service, _order(side="SELL", quantity="6"))
    _reject(second, service, _order(client_order_id="order-2", side="SELL", quantity="5"), "insufficient_paper_position")
    assert snapshot()["positions"][0]["quantity"] == "10"
    assert reconcile()["passed"] is True


def test_pending_buy_does_not_supply_sell_inventory(tmp_path, monkeypatch):
    open_book(monkeypatch, tmp_path, limits=_limits())
    transport = Transport()
    service = _broker(tmp_path, transport)
    service.submit(_order(quantity="10"), **FAVORABLE)
    _reject("broker", service, _order(client_order_id="sell", side="SELL"), "invalid_order_or_position")
    assert len(transport.posts) == 1
    assert snapshot()["positions"] == []


def test_pending_sell_does_not_credit_cash(tmp_path, monkeypatch):
    open_book(monkeypatch, tmp_path, limits=_limits(), shares="6000")
    transport = Transport()
    service = _broker(tmp_path, transport)
    service.submit(_order(side="SELL", quantity="6000"), **FAVORABLE)
    _reject("broker", service, _order(client_order_id="buy", quantity="5000"), "insufficient_paper_cash")
    assert snapshot()["cash"] == "400000"
    assert len(transport.posts) == 1


def test_pending_sell_does_not_reduce_long_exposure(tmp_path, monkeypatch):
    open_book(monkeypatch, tmp_path, limits=_limits(max_position_notional=1000), shares="10")
    transport = Transport()
    service = _broker(tmp_path, transport)
    service.submit(_order(side="SELL", quantity="10"), **FAVORABLE)
    _reject("broker", service, _order(client_order_id="buy"), "max_position_notional_exceeded")
    assert snapshot()["positions"][0]["quantity"] == "10"
    assert len(transport.posts) == 1


def test_different_control_databases_cannot_race_the_shared_exposure_budget(tmp_path, monkeypatch):
    open_book(monkeypatch, tmp_path, limits=_limits(max_position_notional=150))
    transport = Transport()
    services = [_broker(tmp_path, transport, f"broker-{i}.db") for i in range(2)]
    barrier = Barrier(2)

    def submit(index):
        barrier.wait(timeout=5)
        try:
            services[index].submit(_order(client_order_id=f"race-{index}"), **FAVORABLE)
            return "accepted"
        except ValueError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(submit, range(2)))
    assert sorted(outcomes) == ["accepted", "max_position_notional_exceeded"]
    assert len(transport.posts) == 1
    assert len(snapshot()["broker_reservations"]) == 1
    assert reconcile()["passed"] is True


def test_separate_processes_cannot_race_the_shared_exposure_budget(tmp_path, monkeypatch):
    open_book(monkeypatch, tmp_path, limits=_limits(max_position_notional=150))
    worker = r'''
import json, pathlib, sys, time
from osiris_api.control_plane.broker import AlpacaPaperBroker
from osiris_api.control_plane.database import Database
from osiris_api.risk import RiskLimits
class Transport:
    posts = 0
    def request(self, method, url, **kwargs):
        assert method == 'POST'
        self.posts += 1
        body = kwargs['body']
        return {**body, 'id': 'broker-' + body['client_order_id'], 'status': 'accepted',
                'filled_qty': '0', 'filled_avg_price': None}
control, ready, gate, key = sys.argv[1:]
transport = Transport()
service = AlpacaPaperBroker(Database(control), {'key': 'FIXTURE', 'secret': 'FIXTURE'},
                            enabled=True, transport=transport)
pathlib.Path(ready).write_text('ready')
deadline = time.monotonic() + 10
while not pathlib.Path(gate).exists():
    if time.monotonic() > deadline: raise RuntimeError('parent barrier timed out')
    time.sleep(.01)
try:
    service.submit({'client_order_id': key, 'symbol': 'TEST', 'side': 'BUY',
                    'quantity': '1', 'limit_price': '100'}, limits=RiskLimits(),
                   position=0, gross=0, daily_pnl=0, kill_switch=False, fresh=True)
    outcome = 'accepted'
except ValueError as exc:
    outcome = str(exc)
print(json.dumps({'outcome': outcome, 'posts': transport.posts}))
'''
    gate = tmp_path / "start-workers"
    ready_paths = [tmp_path / f"ready-{index}" for index in range(2)]
    processes = [subprocess.Popen(
        [sys.executable, "-c", worker, str(tmp_path / f"process-{index}.db"),
         str(ready_paths[index]), str(gate), f"process-{index}"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=os.environ.copy(),
    ) for index in range(2)]
    try:
        deadline = time.monotonic() + 10
        while not all(path.exists() for path in ready_paths):
            assert time.monotonic() < deadline, "workers did not reach start barrier"
            time.sleep(.01)
        gate.write_text("go")
        results = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=20)
            assert process.returncode == 0, stderr
            results.append(json.loads(stdout))
        assert sorted(result["outcome"] for result in results) == ["accepted", "max_position_notional_exceeded"]
        assert sum(result["posts"] for result in results) == 1
        assert len(snapshot()["broker_reservations"]) == 1
        assert reconcile()["passed"] is True
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)


def test_control_documents_can_share_the_canonical_sqlite_file(tmp_path, monkeypatch):
    open_book(monkeypatch, tmp_path, limits=_limits(max_position_notional=150))
    transport = Transport()
    service = _broker(tmp_path, transport, "paper.sqlite3")
    assert service.submit(_order(), **FAVORABLE)["status"] == "accepted"
    transport.change(status="canceled")
    assert service.reconcile("order-1")["status"] == "canceled"
    assert _reservation()["reserved_quantity"] == "0"
    assert service.submit(_order(client_order_id="next"), **FAVORABLE)["status"] == "accepted"
    assert len(transport.posts) == 2
    assert reconcile()["passed"] is True


def test_legacy_transport_intent_requires_reconciliation_before_new_send(tmp_path, monkeypatch):
    open_book(monkeypatch, tmp_path, limits=_limits(max_position_notional=150))
    transport = Transport()
    service = _broker(tmp_path, transport)
    order = _order(client_order_id="legacy")
    response = {
        "id": "broker-legacy", "client_order_id": "legacy", "symbol": "TEST",
        "side": "buy", "qty": "1", "limit_price": "100", "type": "limit",
        "time_in_force": "day", "status": "accepted", "filled_qty": "0", "filled_avg_price": None,
    }
    transport.responses["legacy"] = response
    service.db.put("broker-orders", "legacy", {
        "fingerprint": digest(order), "order": order, "status": "acknowledged", "response": response,
    })
    _reject("broker", service, _order(client_order_id="next"), "broker_reservation_required_reconcile_first")
    assert transport.calls == []
    assert snapshot()["broker_reservations"] == []
    assert service.reconcile("legacy") == response
    assert _reservation("legacy")["reserved_quantity"] == "1"
    _reject("broker", service, _order(client_order_id="next"), "max_position_notional_exceeded")
    transport.change("legacy", status="canceled")
    service.reconcile("legacy")
    assert service.submit(_order(client_order_id="next"), **FAVORABLE)["status"] == "accepted"
    assert len(transport.posts) == 1
    assert reconcile()["passed"] is True


def test_configured_legacy_transport_intent_also_halts_native_new_risk(tmp_path, monkeypatch):
    open_book(monkeypatch, tmp_path, limits=_limits(max_position_notional=150))
    transport = Transport()
    service = _broker(tmp_path, transport)
    order = _order(client_order_id="legacy")
    response = {
        "id": "broker-legacy", "client_order_id": "legacy", "symbol": "TEST",
        "side": "buy", "qty": "1", "limit_price": "100", "type": "limit",
        "time_in_force": "day", "status": "accepted", "filled_qty": "0", "filled_avg_price": None,
    }
    transport.responses["legacy"] = response
    service.db.put("broker-orders", "legacy", {
        "fingerprint": digest(order), "order": order, "status": "acknowledged", "response": response,
    })
    monkeypatch.setenv("OSIRIS_CONTROL_DB", service.db.path)
    _reject("native", service, _order(client_order_id="native-before"), "broker_reservation_required_reconcile_first")
    assert transport.calls == []
    service.reconcile("legacy")
    _reject("native", service, _order(client_order_id="native-after"), "max_position_notional_exceeded")
    transport.change("legacy", status="canceled")
    service.reconcile("legacy")
    _accept("native", service, _order(client_order_id="native-released"))
    assert transport.posts == []
    assert reconcile()["passed"] is True


def test_control_insert_failure_leaves_orphan_commit_and_restart_never_posts(tmp_path, monkeypatch):
    open_book(monkeypatch, tmp_path)
    transport = Transport()
    service = _broker(tmp_path, transport)
    with service.db.transaction() as conn:
        conn.execute("""CREATE TRIGGER refuse_broker_claim BEFORE INSERT ON cp_documents
            WHEN NEW.collection='broker-orders' BEGIN SELECT RAISE(FAIL, 'injected claim failure'); END""")
    with pytest.raises(sqlite3.DatabaseError, match="injected claim failure"):
        service.submit(_order(), **FAVORABLE)
    assert transport.posts == []
    assert service.db.get("broker-orders", "order-1") is None
    assert _reservation()["reserved_quantity"] == "1"
    with service.db.transaction() as conn:
        conn.execute("DROP TRIGGER refuse_broker_claim")
    service = _broker(tmp_path, transport)
    with pytest.raises(ValueError, match="order_outcome_unknown_reconcile_first"):
        service.submit(_order(), **FAVORABLE)
    assert service.db.get("broker-orders", "order-1") is None
    assert transport.posts == []
    assert _record()["response"] is None
    # Operator reconciliation can recover the canonical intent and GET its
    # outcome. Even if an external outcome exists, this path never sends it.
    transport.responses["order-1"] = {
        "id": "broker-order-1", "client_order_id": "order-1", "symbol": "TEST",
        "side": "buy", "qty": "1", "limit_price": "100", "type": "limit",
        "time_in_force": "day", "status": "canceled", "filled_qty": "0", "filled_avg_price": None,
    }
    assert service.reconcile("order-1")["status"] == "canceled"
    assert _reservation()["reserved_quantity"] == "0"
    assert transport.posts == []
    assert [call[0] for call in transport.calls] == ["GET"]
    assert recover()["passed"] is True


def test_same_client_replay_cannot_steal_a_fresh_submitters_send_claim(tmp_path, monkeypatch):
    from osiris_api.control_plane import broker as broker_module

    open_book(monkeypatch, tmp_path)
    transport = Transport()
    owner, replay = _broker(tmp_path, transport), _broker(tmp_path, transport)
    reserved, release_owner = Event(), Event()
    authorize = broker_module.authorize_paper_submission

    def pause_after_commit(**kwargs):
        decision = authorize(**kwargs)
        if decision.get("approved") and not decision.get("reservation_replay"):
            reserved.set()
            assert release_owner.wait(timeout=10), "fresh owner was never released"
        return decision

    monkeypatch.setattr(broker_module, "authorize_paper_submission", pause_after_commit)
    with ThreadPoolExecutor(max_workers=1) as pool:
        submitted = pool.submit(owner.submit, _order(), **FAVORABLE)
        try:
            assert reserved.wait(timeout=5), "fresh owner never committed its reservation"
            with pytest.raises(ValueError, match="order_outcome_unknown_reconcile_first"):
                replay.submit(_order(), **FAVORABLE)
            assert owner.db.get("broker-orders", "order-1") is None
            assert transport.posts == []
        finally:
            release_owner.set()
        assert submitted.result(timeout=10)["status"] == "accepted"
    assert replay.submit(_order(), **FAVORABLE)["status"] == "accepted"
    assert len(transport.posts) == 1
    assert reconcile()["passed"] is True


def test_post_acceptance_control_cas_failure_keeps_capacity_until_reconciliation(tmp_path, monkeypatch):
    open_book(monkeypatch, tmp_path, limits=_limits(max_position_notional=150))
    transport = Transport()
    service = _broker(tmp_path, transport)

    def fail_put(*_args, **_kwargs):
        raise ValueError("version_conflict")

    with monkeypatch.context() as patch:
        patch.setattr(service.db, "put", fail_put)
        with pytest.raises(ValueError, match="version_conflict"):
            service.submit(_order(), **FAVORABLE)
    assert len(transport.posts) == 1
    assert _reservation()["reserved_quantity"] == "1"
    assert _record()["response"] is None
    service = _broker(tmp_path, transport)
    with pytest.raises(ValueError, match="order_outcome_unknown_reconcile_first"):
        service.submit(_order(), **FAVORABLE)
    _reject("broker", service, _order(client_order_id="next"), "max_position_notional_exceeded")
    assert service.reconcile("order-1")["status"] == "accepted"
    assert service.submit(_order(), **FAVORABLE)["status"] == "accepted"
    assert len(transport.posts) == 1
    assert reconcile()["passed"] is True


def test_timeout_restart_and_duplicate_never_submit_twice_or_release_reservation(tmp_path, monkeypatch):
    open_book(monkeypatch, tmp_path, limits=_limits(max_position_notional=150))
    transport = Transport()
    transport.post_error = True
    service = _broker(tmp_path, transport)
    with pytest.raises(ValueError, match="order_outcome_unknown_reconcile_first"):
        service.submit(_order(), **FAVORABLE)
    assert _reservation()["reserved_quantity"] == "1"
    assert _record()["response"] is None
    service = _broker(tmp_path, transport)
    with pytest.raises(ValueError, match="order_outcome_unknown_reconcile_first"):
        service.submit(_order(), **FAVORABLE)
    _reject("broker", service, _order(client_order_id="next"), "max_position_notional_exceeded")
    assert service.reconcile("order-1")["status"] == "accepted"
    assert service.submit(_order(), **FAVORABLE)["status"] == "accepted"
    assert len(transport.posts) == 1
    assert _reservation()["reserved_quantity"] == "1"
    assert recover()["passed"] is True


@pytest.mark.parametrize("fails", [False, True])
def test_cancel_request_and_pending_cancel_keep_full_reservation(tmp_path, monkeypatch, fails):
    open_book(monkeypatch, tmp_path, limits=_limits(max_position_notional=150))
    transport = Transport()
    service = _broker(tmp_path, transport)
    service.submit(_order(), **FAVORABLE)
    before = _reservation()
    transport.delete_error = fails
    if fails:
        with pytest.raises(TimeoutError):
            service.cancel("broker-order-1")
    else:
        assert service.cancel("broker-order-1") is None
    assert _reservation() == before
    transport.change(status="pending_cancel")
    service.reconcile("order-1")
    assert _reservation()["reserved_quantity"] == "1"
    _reject("broker", service, _order(client_order_id="next"), "max_position_notional_exceeded")
    assert len(transport.posts) == 1


@pytest.mark.parametrize("failure", ["timeout", "invalid-snapshot"])
def test_failed_reconciliation_keeps_last_verified_reservation(tmp_path, monkeypatch, failure):
    open_book(monkeypatch, tmp_path, limits=_limits(max_position_notional=150))
    transport = Transport()
    service = _broker(tmp_path, transport)
    service.submit(_order(), **FAVORABLE)
    before, events = _reservation(), _events()
    if failure == "timeout":
        transport.get_error = True
        expected = TimeoutError
    else:
        transport.responses["order-1"]["symbol"] = "BTC-USD"
        transport.change(status="canceled")
        expected = ValueError
    with pytest.raises(expected):
        service.reconcile("order-1")
    assert _reservation() == before
    assert _events() == events
    _reject("broker", service, _order(client_order_id="next"), "max_position_notional_exceeded")
    assert len(transport.posts) == 1


@pytest.mark.parametrize("terminal", ["canceled", "expired", "rejected", "done_for_day"])
def test_validated_zero_fill_terminal_state_releases_once(tmp_path, monkeypatch, terminal):
    open_book(monkeypatch, tmp_path, limits=_limits(max_position_notional=150))
    transport = Transport()
    service = _broker(tmp_path, transport)
    service.submit(_order(), **FAVORABLE)
    transport.change(status=terminal)
    service.reconcile("order-1")
    assert _reservation()["reserved_quantity"] == "0"
    assert _reservation()["accounting_pending"] is False
    before = _events()
    service.reconcile("order-1")
    assert _events() == before
    assert service.submit(_order(client_order_id="next"), **FAVORABLE)["status"] == "accepted"
    assert snapshot()["fill_count"] == 0
    assert reconcile()["passed"] is True


@pytest.mark.parametrize("terminal,filled,reserved", [("partially_filled", "1", "3"), ("canceled", "1", "1"), ("filled", "3", "3")])
def test_reported_fills_stay_reserved_without_fabricated_accounting(tmp_path, monkeypatch, terminal, filled, reserved):
    open_book(monkeypatch, tmp_path, limits=_limits())
    transport = Transport()
    service = _broker(tmp_path, transport)
    service.submit(_order(quantity="3"), **FAVORABLE)
    transport.change(status=terminal, filled=filled)
    service.reconcile("order-1")
    reservation = _reservation()
    assert reservation["reserved_quantity"] == reserved
    assert reservation["filled_quantity"] == filled
    assert reservation["accounting_pending"] is True
    _reject("broker", service, _order(client_order_id="next"), "broker_fills_require_accounting")
    _reject("native", service, _order(client_order_id="native-next"), "broker_fills_require_accounting")
    assert snapshot()["fill_count"] == 0
    assert snapshot()["positions"] == []
    assert snapshot()["cash"] == "1000000"
    assert len(transport.posts) == 1
    before = _events()
    service.reconcile("order-1")
    assert _events() == before


def test_native_cancel_and_day_expiry_do_not_release_external_day_order(tmp_path, monkeypatch):
    open_book(monkeypatch, tmp_path)
    transport = Transport()
    service = _broker(tmp_path, transport)
    service.submit(_order(), **FAVORABLE)
    before = _reservation()
    with pytest.raises(ValueError, match="order_not_found"):
        cancel_paper_order(client_order_id="order-1")
    tomorrow = (datetime.now(UTC) + timedelta(days=2)).isoformat()
    assert expire_paper_orders(now=tomorrow)["expired"] == []
    assert _reservation() == before
    assert reconcile()["passed"] is True


@pytest.mark.parametrize("collision", ["changed-intent", "different-control-db", "native-first", "external-first"])
def test_client_ids_cannot_cross_intents_or_execution_owners(tmp_path, monkeypatch, collision):
    open_book(monkeypatch, tmp_path)
    transport = Transport()
    service = _broker(tmp_path, transport)
    if collision == "native-first":
        assert _native(_order())["status"] == "accepted"
        with pytest.raises(ValueError):
            service.submit(_order(), **FAVORABLE)
        assert transport.posts == []
    else:
        service.submit(_order(), **FAVORABLE)
        before = _reservation()
        if collision == "external-first":
            with pytest.raises(ValueError):
                _native(_order())
        elif collision == "different-control-db":
            with pytest.raises(ValueError):
                _broker(tmp_path, transport, "other.db").submit(_order(), **FAVORABLE)
        else:
            with pytest.raises(ValueError):
                service.submit(_order(quantity="2"), **FAVORABLE)
        assert _reservation() == before
        assert len(transport.posts) == 1
    assert reconcile()["passed"] is True


def test_failed_reservation_commit_rolls_back_before_http_and_retry_is_possible(tmp_path, monkeypatch):
    from osiris_api import paper_execution

    open_book(monkeypatch, tmp_path)
    transport = Transport()
    service = _broker(tmp_path, transport)
    commit = paper_execution._commit

    def fail_commit(_conn):
        raise sqlite3.OperationalError("injected disk failure")

    with monkeypatch.context() as patch:
        patch.setattr(paper_execution, "_commit", fail_commit)
        with pytest.raises(ValueError):
            service.submit(_order(), **FAVORABLE)
    assert transport.posts == []
    assert snapshot()["broker_reservations"] == []
    assert _events() == []
    assert reconcile()["passed"] is True
    assert paper_execution._commit is commit
    assert service.submit(_order(), **FAVORABLE)["status"] == "accepted"
    assert len(transport.posts) == 1


@pytest.mark.parametrize("tamper", ["delete", "quantity", "response"])
def test_reservation_projection_tampering_fails_closed(tmp_path, monkeypatch, tamper):
    open_book(monkeypatch, tmp_path)
    transport = Transport()
    service = _broker(tmp_path, transport)
    service.submit(_order(), **FAVORABLE)
    with sqlite3.connect(tmp_path / "paper.sqlite3") as conn:
        row = conn.execute("SELECT body FROM paper_exec_broker_reservations WHERE client_order_id='order-1'").fetchone()
        record = json.loads(row[0])
        if tamper == "delete":
            conn.execute("DELETE FROM paper_exec_broker_reservations WHERE client_order_id='order-1'")
        else:
            if tamper == "quantity":
                record["order"]["quantity"] = "0.1"
            else:
                record["response"]["status"] = "canceled"
            conn.execute("UPDATE paper_exec_broker_reservations SET body=? WHERE client_order_id='order-1'", (json.dumps(record),))
    assert reconcile()["passed"] is False
    with pytest.raises(ValueError):
        _broker(tmp_path, transport).submit(_order(client_order_id="next"), **FAVORABLE)
    assert len(transport.posts) == 1
