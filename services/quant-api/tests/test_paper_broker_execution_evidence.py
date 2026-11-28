"""Fresh, bounded PAPER execution evidence never substitutes for fee accounting."""

import copy
import json
import sqlite3
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import pytest
from test_pretrade_risk_gateway_416 import FAVORABLE, _order, open_book

from osiris_api import paper_execution as ledger
from osiris_api.control_plane.broker import AlpacaPaperBroker
from osiris_api.control_plane.database import Database
from osiris_api.paper_broker_execution_evidence import collect


class Feed:
    def __init__(self):
        self.calls = []
        self.orders = {}
        self.activities = {}
        self.page_override = None
        self.after_page = None

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        path, query = urlsplit(url).path, parse_qs(urlsplit(url).query)
        assert url.startswith("https://paper-api.alpaca.markets/")
        if method == "POST":
            body = kwargs["body"]
            client = body["client_order_id"]
            self.orders[client] = {
                **body, "id": "broker-" + client, "status": "accepted",
                "filled_qty": "0", "filled_avg_price": None,
            }
            return copy.deepcopy(self.orders[client])
        assert method == "GET"
        if path == "/v2/orders:by_client_order_id":
            return copy.deepcopy(self.orders[query["client_order_id"][0]])
        assert path == "/v2/account/activities/FILL"
        assert query["direction"] == ["asc"]
        order_id = query["order_id"][0]
        if self.page_override is not None:
            result = self.page_override(query)
        else:
            rows = self.activities.get(order_id, [])
            token = query.get("page_token", [None])[0]
            start = 0 if token is None else next(
                index + 1 for index, activity in enumerate(rows) if activity["id"] == token
            )
            result = rows[start:start + int(query["page_size"][0])]
        if self.after_page is not None:
            self.after_page()
        return copy.deepcopy(result)

    def filled(self, client="order-1", *, quantity="1", price="100", status="partially_filled"):
        order = self.orders[client]
        order.update(status=status, filled_qty=quantity, filled_avg_price=price)
        self.activities[order["id"]] = [{
            "id": "20261128::execution/id?α", "order_id": order["id"],
            "activity_type": "FILL", "type": "partial_fill" if status != "filled" else "fill",
            "symbol": order["symbol"], "side": order["side"],
            "qty": quantity, "price": price, "cum_qty": quantity,
            "leaves_qty": str(2 - int(quantity)),
            "transaction_time": datetime.now(UTC).isoformat(),
        }]


def service(tmp_path, feed):
    return AlpacaPaperBroker(Database(str(tmp_path / "control.sqlite3")),
                             {"key": "TEST-KEY", "secret": "TEST-SECRET"},
                             enabled=True, transport=feed)


@pytest.fixture
def account(tmp_path, monkeypatch):
    monkeypatch.delenv("OSIRIS_CONTROL_DB", raising=False)
    open_book(monkeypatch, tmp_path)
    feed = Feed()
    broker = service(tmp_path, feed)
    broker.submit(_order(quantity="2"), **FAVORABLE)
    feed.filled()
    return broker, feed


def financial(snapshot):
    return {key: snapshot[key] for key in (
        "cash", "realized_pnl", "unrealized_pnl", "positions", "fill_count", "order_count",
    )}


def test_collection_is_get_only_and_does_not_write_state(account):
    broker, feed = account
    before = ledger.snapshot()
    retained = broker.db.get("broker-orders", "order-1")
    feed.calls.clear()
    bundle = collect(broker, "order-1", page_size=1)
    assert bundle["status"] == "complete"
    assert bundle["activities"][0]["id"] == "20261128::execution/id?α"
    assert bundle["fee_status"] == "unknown"
    assert bundle["accounting_authorized"] is False
    assert bundle["live_execution"] is False
    assert len(bundle["raw_page_hashes"]) == 2
    assert all(call[0] == "GET" for call in feed.calls)
    assert "TEST-KEY" not in json.dumps(bundle) and "TEST-SECRET" not in json.dumps(bundle)
    assert ledger.snapshot() == before
    assert broker.db.get("broker-orders", "order-1") == retained


def test_adapter_persists_real_activity_ids_without_accounting_or_releasing_fills(account, tmp_path):
    broker, feed = account
    before = ledger.snapshot()
    receipt = broker.fetch_execution_evidence("order-1", page_size=1)
    after = ledger.snapshot()
    assert receipt["status"] == "evidence_recorded"
    assert receipt["activity_count"] == 1
    assert receipt["fee_status"] == "unknown"
    assert receipt["accounting_authorized"] is False
    assert receipt["financial_state_changed"] is False
    assert financial(after) == financial(before)
    assert after["broker_accounting_pending"] is True
    assert after["broker_reservations"][0]["reserved_quantity"] == "2"
    assert after["broker_execution_evidence"][0]["evidence_hash"] == receipt["evidence_hash"]
    assert ledger.reconcile()["passed"] is True
    with sqlite3.connect(str(tmp_path / "paper.sqlite3")) as conn:
        activity = conn.execute("SELECT activity_id,body FROM paper_exec_broker_execution_activities").fetchone()
    assert activity[0] == feed.activities["broker-order-1"][0]["id"]
    assert "fee" not in json.loads(activity[1])["activity"]


def test_identical_evidence_replays_after_restart_without_duplicate_journal(account, tmp_path):
    broker, feed = account
    first = broker.fetch_execution_evidence("order-1")
    before = ledger.snapshot()
    second = service(tmp_path, feed).fetch_execution_evidence("order-1")
    assert second["replayed"] is True
    assert second["evidence_hash"] == first["evidence_hash"]
    assert ledger.snapshot() == before


@pytest.mark.parametrize("change", [
    {"id": ""}, {"order_id": "another-order"}, {"symbol": "OTHER"}, {"side": "sell"},
    {"qty": "0"}, {"qty": "NaN"}, {"qty": True}, {"price": "101"},
    {"price": "Infinity"}, {"cum_qty": "2"}, {"leaves_qty": "0"},
    {"transaction_time": "2026-11-28T00:00:00"}, {"type": "correction"},
])
def test_invalid_evidence_changes_neither_canonical_nor_control_state(account, change):
    broker, feed = account
    feed.activities["broker-order-1"][0].update(change)
    before = ledger.snapshot()
    retained = broker.db.get("broker-orders", "order-1")
    with pytest.raises(ValueError):
        broker.fetch_execution_evidence("order-1")
    assert ledger.snapshot() == before
    assert broker.db.get("broker-orders", "order-1") == retained


@pytest.mark.parametrize("bounds", [
    {"page_size": 0}, {"page_size": 101}, {"page_size": True},
    {"max_pages": 0}, {"max_pages": 11}, {"max_pages": True},
])
def test_pagination_bounds_are_validated_before_http(account, bounds):
    broker, feed = account
    feed.calls.clear()
    with pytest.raises(ValueError):
        broker.fetch_execution_evidence("order-1", **bounds)
    assert feed.calls == []


def test_page_budget_never_silently_accepts_truncation(account):
    broker, _feed = account
    before = ledger.snapshot()
    with pytest.raises(ValueError, match="incomplete"):
        broker.fetch_execution_evidence("order-1", max_pages=1)
    assert ledger.snapshot() == before


def test_duplicate_page_cycle_is_refused(account):
    broker, feed = account
    feed.page_override = lambda _query: feed.activities["broker-order-1"]
    with pytest.raises(ValueError):
        broker.fetch_execution_evidence("order-1", page_size=1)


def test_moving_order_does_not_persist_inconsistent_history(account):
    broker, feed = account
    before = ledger.snapshot()
    feed.after_page = lambda: feed.orders["order-1"].update(status="filled", filled_qty="2")
    with pytest.raises(ValueError, match="moving"):
        broker.fetch_execution_evidence("order-1")
    assert ledger.snapshot() == before


def test_missing_activities_do_not_prove_accounting_complete(account):
    broker, feed = account
    feed.activities.clear()
    with pytest.raises(ValueError, match="incomplete"):
        broker.fetch_execution_evidence("order-1")


@pytest.mark.parametrize("table", [
    "paper_exec_broker_execution_evidence", "paper_exec_broker_execution_activities",
])
def test_deleting_evidence_projection_closes_the_ledger_gate(account, tmp_path, table):
    broker, _feed = account
    broker.fetch_execution_evidence("order-1")
    with sqlite3.connect(str(tmp_path / "paper.sqlite3")) as conn:
        conn.execute(f"DELETE FROM {table}")
    result = ledger.reconcile()
    assert result["passed"] is False
    assert "broker_execution_evidence_projection" in result["breaks"]


def test_unmatched_retained_intent_is_refused_before_http(account):
    broker, feed = account
    state = broker.db.get("broker-orders", "order-1")
    body = state["value"]
    body["response"]["status"] = "canceled"
    broker.db.put("broker-orders", "order-1", body, expected_version=state["version"])
    feed.calls.clear()
    with pytest.raises(ValueError):
        broker.fetch_execution_evidence("order-1")
    assert feed.calls == []


def test_multiple_pages_preserve_exact_individual_activities(account):
    broker, feed = account
    original = feed.activities["broker-order-1"][0]
    first = {**original, "qty": "0.5", "cum_qty": "0.5", "leaves_qty": "1.5"}
    second = {**original, "id": "provider::second&activity", "qty": "0.5"}
    feed.activities["broker-order-1"] = [first, second]
    result = collect(broker, "order-1", page_size=1, max_pages=3)
    assert [row["id"] for row in result["activities"]] == [first["id"], second["id"]]
    assert len(result["raw_page_hashes"]) == 3
    tokens = [parse_qs(urlsplit(url).query).get("page_token")
              for _, url, _ in feed.calls if "/activities/FILL" in url]
    assert tokens == [None, [first["id"]], [second["id"]]]


def test_zero_fill_history_requires_no_invented_fee_or_execution(account):
    broker, feed = account
    feed.orders["order-1"].update(status="accepted", filled_qty="0", filled_avg_price=None)
    feed.activities.clear()
    receipt = broker.fetch_execution_evidence("order-1", max_pages=1)
    assert receipt["activity_count"] == 0
    assert receipt["fee_status"] == "unknown"
    assert ledger.snapshot()["fill_count"] == 0
    assert ledger.snapshot()["broker_accounting_pending"] is False


@pytest.mark.parametrize("status,quantity,reserved", [
    ("filled", "2", "2"), ("canceled", "1", "1"),
    ("expired", "1", "1"), ("done_for_day", "1", "1"),
])
def test_terminal_execution_evidence_retains_every_unaccounted_fill(account, status, quantity, reserved):
    broker, feed = account
    feed.filled(status=status, quantity=quantity)
    before = ledger.snapshot()
    broker.fetch_execution_evidence("order-1")
    after = ledger.snapshot()
    assert financial(after) == financial(before)
    assert after["broker_reservations"][0]["reserved_quantity"] == reserved
    assert after["broker_accounting_pending"] is True
    assert ledger.read_risk_limits()["reason"] == "broker_fills_require_accounting"


def test_provider_fee_like_fields_do_not_establish_fee_finality(account):
    broker, feed = account
    feed.activities["broker-order-1"][0].update(fee="0", fee_currency="USD", final=True)
    receipt = broker.fetch_execution_evidence("order-1")
    assert receipt["fee_status"] == "unknown"
    assert receipt["accounting_authorized"] is False
    assert ledger.snapshot()["broker_accounting_pending"] is True


def test_contradictory_average_cannot_replace_real_execution_prices(account):
    broker, feed = account
    feed.activities["broker-order-1"][0]["price"] = "99"
    before = ledger.snapshot()
    with pytest.raises(ValueError, match="incomplete"):
        broker.fetch_execution_evidence("order-1")
    assert ledger.snapshot() == before


@pytest.mark.parametrize("bad_page", [None, {}, "FILL", [None]])
def test_provider_page_shape_is_strict(account, bad_page):
    broker, feed = account
    feed.page_override = lambda _query: bad_page
    with pytest.raises(ValueError):
        broker.fetch_execution_evidence("order-1")


def test_oversized_page_is_bounded_before_persistence(account):
    broker, feed = account
    feed.activities["broker-order-1"][0]["extra"] = "x" * 1_048_576
    before = ledger.snapshot()
    with pytest.raises(ValueError):
        broker.fetch_execution_evidence("order-1")
    assert ledger.snapshot() == before


def test_control_version_race_changes_no_canonical_evidence(account):
    broker, feed = account
    before = ledger.snapshot()
    changed = False

    def change_version():
        nonlocal changed
        if not changed:
            current = broker.db.get("broker-orders", "order-1")
            broker.db.put("broker-orders", "order-1", current["value"],
                          expected_version=current["version"])
            changed = True

    feed.after_page = change_version
    with pytest.raises(ValueError, match="version_conflict"):
        broker.fetch_execution_evidence("order-1")
    assert ledger.snapshot() == before


def test_canonical_commit_failure_keeps_capacity_and_requires_reconciliation(account, monkeypatch):
    broker, _feed = account
    monkeypatch.setenv("OSIRIS_CONTROL_DB", broker.db.path)
    before = ledger.snapshot()

    def fail_commit(_conn):
        raise sqlite3.OperationalError("fixture_commit_failure")

    with monkeypatch.context() as patch:
        patch.setattr(ledger, "_commit", fail_commit)
        with pytest.raises(sqlite3.OperationalError):
            broker.fetch_execution_evidence("order-1")
    assert ledger.snapshot() == before
    assert ledger.daily_loss_gate()["reason"] == "broker_reservation_required_reconcile_first"
    broker.reconcile("order-1")
    assert ledger.snapshot()["broker_accounting_pending"] is True
    assert broker.fetch_execution_evidence("order-1")["activity_count"] == 1


def test_activity_id_reuse_for_another_order_does_not_overwrite_prior_evidence(account):
    broker, feed = account
    # Reserve both before either execution is observed: their provider IDs must
    # still remain unique across orders in the same configured paper store.
    broker.submit(_order(client_order_id="order-2", quantity="2"), **FAVORABLE)
    feed.filled("order-2")
    first = broker.fetch_execution_evidence("order-1")
    before = ledger.snapshot()
    with pytest.raises(ValueError, match="conflict"):
        broker.fetch_execution_evidence("order-2")
    assert ledger.snapshot() == before
    assert before["broker_execution_evidence"][0]["evidence_hash"] == first["evidence_hash"]
