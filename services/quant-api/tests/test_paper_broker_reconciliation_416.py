"""Package 48: the paper book against an independent broker snapshot.

Caller floats are not a source. The broker side is a recorded fixture or a
stored paper-broker document. Nothing in this module opens a network socket.
"""
import json

import pytest
from fastapi.testclient import TestClient

from osiris_api.control_plane.broker import validate_broker_snapshot
from osiris_api.control_plane.database import Database, digest
from osiris_api.control_plane.vendors import HttpJson
from osiris_api.main import app
from osiris_api.paper_execution import (
    BROKER_BOOK_SCHEMA,
    LIVE_EXECUTION_ENABLED,
    apply_paper_fill,
    broker_book_digest,
    broker_reconciliation_history,
    expire_paper_orders,
    initialize_paper_account,
    reconcile,
    reconcile_broker_book,
    submit_paper_order,
)
from osiris_api.phase11_systems import reconcile_ledger
from osiris_api.phase8_systems import reconcile_paper_trades
from osiris_api.risk import RiskLimits

NOW = "2026-11-08T16:00:00+00:00"
FRESH = "2026-11-08T15:59:30+00:00"
STALE = "2026-11-08T14:00:00+00:00"


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "paper.sqlite3"
    monkeypatch.setenv("OSIRIS_PAPER_EXECUTION_DB", str(path))
    monkeypatch.delenv("OSIRIS_ENABLE_LIVE_EXECUTION", raising=False)
    monkeypatch.delenv("OSIRIS_CONTROL_DB", raising=False)
    monkeypatch.delenv("OSIRIS_PAPER_BROKER_SNAPSHOT", raising=False)
    return path


def _init():
    initialize_paper_account("1000", RiskLimits(
        max_order_notional=10_000, max_position_notional=10_000,
        max_daily_loss=1_000, max_gross_exposure=10_000,
    ))


def _working(client_order_id="buy-1", **overrides):
    payload = dict(
        client_order_id=client_order_id, symbol="BTC", side="BUY",
        quantity="3", price="100", fee="0", observed_at=FRESH, now=NOW, working=True,
    )
    payload.update(overrides)
    return submit_paper_order(**payload)


def _fill(**overrides):
    payload = dict(
        client_order_id="buy-1", client_fill_id="fill-1", quantity="3",
        observed_at=FRESH, now=NOW,
    )
    payload.update(overrides)
    return apply_paper_fill(**payload)


def _flat():
    _init()
    _working()
    _fill()


def _book(**overrides):
    body = {
        "schema": BROKER_BOOK_SCHEMA,
        "mode": "paper",
        "as_of": NOW,
        "cash": "700.00",
        "buying_power": "700.00",
        "positions": [{"symbol": "btc", "quantity": "3.0"}],
        "fills": [{
            "execution_id": "fill-1",
            "client_order_id": "buy-1",
            "symbol": "BTC",
            "side": "buy",
            "quantity": "3.0",
            "price": "100.00",
        }],
        "open_orders": [],
    }
    body.update(overrides)
    return body


def _write(tmp_path, monkeypatch, body):
    document = dict(body)
    document["digest"] = broker_book_digest(document)
    path = tmp_path / "broker-book.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setenv("OSIRIS_PAPER_BROKER_SNAPSHOT", str(path))
    return document


def _codes(report):
    return [item["code"] for item in report["breaks"]]


def _forbid_network(monkeypatch):
    def explode(*_args, **_kwargs):
        raise AssertionError("broker network call")

    monkeypatch.setattr(HttpJson, "request", explode)


def test_direct_caller_floats_are_rejected():
    with pytest.raises(ValueError, match="caller_supplied_numbers_rejected"):
        reconcile_ledger(
            [{"asset": "A", "quantity": 2}], [{"asset": "A", "quantity": 2}], 100, 100,
        )
    with pytest.raises(ValueError, match="caller_supplied_numbers_rejected"):
        reconcile_paper_trades(
            [{"order_id": "o1", "asset": "A", "side": "buy", "quantity": 10}],
            [{"order_id": "o1", "quantity": 6, "price": 100}],
            [{"asset": "A", "quantity": 6}],
        )


def test_orders_stay_open_until_a_broker_run_is_recorded(db):
    _init()
    assert _working()["state"] == "accepted"
    assert reconcile()["passed"] is True
    assert "broker_reconciliation" not in reconcile()["breaks"]
    assert LIVE_EXECUTION_ENABLED is False


def test_missing_snapshot_is_unreconciled_and_blocks_orders(db, tmp_path, monkeypatch):
    _init()
    monkeypatch.setenv("OSIRIS_PAPER_BROKER_SNAPSHOT", str(tmp_path / "absent.json"))
    report = reconcile_broker_book(now=NOW)
    assert report["status"] == "unreconciled"
    assert report["reason"] == "snapshot_missing"
    assert report["new_risk_allowed"] is False
    assert report["live_trading_authorized"] is False
    assert report["live_execution"] is False
    with pytest.raises(ValueError, match="reconciliation_break"):
        _working()


def test_stale_snapshot_is_unreconciled(db, tmp_path, monkeypatch):
    _flat()
    _write(tmp_path, monkeypatch, _book(as_of=STALE))
    report = reconcile_broker_book(now=NOW)
    assert report["status"] == "unreconciled"
    assert report["reason"] == "snapshot_stale"
    assert report["breaks"] == []
    assert report["tolerances"]["snapshot_max_age_seconds"] == "300"


def test_bad_digest_and_float_cash_are_unverifiable(db, tmp_path, monkeypatch):
    _flat()
    sealed = _write(tmp_path, monkeypatch, _book())
    path = tmp_path / "broker-book.json"
    sealed["digest"] = "0" * 64
    path.write_text(json.dumps(sealed), encoding="utf-8")
    tampered = reconcile_broker_book(now=NOW)
    assert tampered["status"] == "unreconciled"
    assert tampered["reason"] == "snapshot_unverifiable"

    path.write_text(json.dumps({
        "schema": BROKER_BOOK_SCHEMA, "mode": "paper", "as_of": NOW,
        "cash": 700.0, "buying_power": "700", "positions": [], "fills": [],
        "open_orders": [], "digest": "not-a-real-digest",
    }), encoding="utf-8")
    floated = reconcile_broker_book(now=NOW)
    assert floated["status"] == "unreconciled"
    assert floated["reason"] == "snapshot_unverifiable"
    assert floated["status"] != "clean"


def test_matching_book_is_clean_with_exact_decimals(db, tmp_path, monkeypatch):
    _forbid_network(monkeypatch)
    _flat()
    _write(tmp_path, monkeypatch, _book())
    report = reconcile_broker_book(now=NOW)
    assert report["status"] == "clean"
    assert report["reason"] is None
    assert report["breaks"] == []
    assert report["new_risk_allowed"] is True
    assert report["tolerances"]["quantity"] == "0"
    assert report["tolerances"]["cash"] == "0"
    assert report["tolerances"]["buying_power"] == "0"
    assert report["live_trading_authorized"] is False
    assert report["broker_order_submitted"] is False
    assert reconcile()["passed"] is True
    assert _working("buy-2", quantity="1", price="10")["state"] == "accepted"


def test_position_cash_and_fill_breaks_are_classified(db, tmp_path, monkeypatch):
    _flat()
    _write(tmp_path, monkeypatch, _book(
        cash="699",
        positions=[{"symbol": "BTC", "quantity": "2"}],
        fills=[{
            "execution_id": "fill-1", "client_order_id": "buy-1", "symbol": "BTC",
            "side": "buy", "quantity": "2", "price": "101",
        }, {
            "execution_id": "fill-extra", "client_order_id": "buy-1", "symbol": "BTC",
            "side": "buy", "quantity": "1", "price": "100",
        }],
    ))
    report = reconcile_broker_book(now=NOW)
    assert report["status"] == "breaks"
    assert _codes(report) == [
        "cash", "extra_fill", "fill_price", "fill_quantity", "position_quantity",
    ]
    by_code = {item["code"]: item for item in report["breaks"]}
    assert by_code["cash"]["ledger"] == "700"
    assert by_code["cash"]["broker"] == "699"
    assert by_code["position_quantity"]["ledger"] == "3"
    assert by_code["position_quantity"]["broker"] == "2"
    assert by_code["fill_quantity"]["execution_id"] == "fill-1"
    assert by_code["fill_price"]["broker"] == "101"
    assert by_code["extra_fill"]["execution_id"] == "fill-extra"
    assert report["new_risk_allowed"] is False


def test_missing_fill_is_classified(db, tmp_path, monkeypatch):
    _init()
    _working(quantity="4")
    _fill(quantity="2", client_fill_id="fill-1")
    _fill(quantity="2", client_fill_id="fill-2")
    body = _book(
        cash="600", buying_power="600",
        positions=[{"symbol": "BTC", "quantity": "4"}],
        fills=[{
            "execution_id": "fill-1", "client_order_id": "buy-1", "symbol": "BTC",
            "side": "BUY", "quantity": "2", "price": "100",
        }],
    )
    _write(tmp_path, monkeypatch, body)
    report = reconcile_broker_book(now=NOW)
    assert report["status"] == "breaks"
    assert _codes(report) == ["missing_fill"]
    assert report["breaks"][0]["execution_id"] == "fill-2"


def test_orphan_open_orders_and_reservation_buying_power(db, tmp_path, monkeypatch):
    _init()
    _working()
    resting = {
        "schema": BROKER_BOOK_SCHEMA,
        "mode": "paper",
        "as_of": NOW,
        "cash": "1000",
        "buying_power": "1000",
        "positions": [],
        "fills": [],
        "open_orders": [{
            "client_order_id": "buy-2", "symbol": "BTC", "side": "buy",
            "quantity": "3", "filled_quantity": "0", "leaves_quantity": "3",
        }],
    }
    _write(tmp_path, monkeypatch, resting)
    report = reconcile_broker_book(now=NOW)
    assert report["status"] == "breaks"
    assert _codes(report) == [
        "orphan_open_order_broker", "orphan_open_order_ledger", "reservation_buying_power",
    ]
    reservation = next(item for item in report["breaks"] if item["code"] == "reservation_buying_power")
    assert reservation["ledger"] == "700"
    assert reservation["broker"] == "1000"
    assert reservation["reserved_cash"] == "300"
    assert reservation["tolerance"] == "0"


def test_open_order_quantity_mismatch(db, tmp_path, monkeypatch):
    _init()
    _working()
    resting = {
        "schema": BROKER_BOOK_SCHEMA,
        "mode": "paper",
        "as_of": NOW,
        "cash": "1000",
        "buying_power": "700",
        "positions": [],
        "fills": [],
        "open_orders": [{
            "client_order_id": "buy-1", "symbol": "BTC", "side": "BUY",
            "quantity": "3", "filled_quantity": "1", "leaves_quantity": "2",
        }],
    }
    _write(tmp_path, monkeypatch, resting)
    report = reconcile_broker_book(now=NOW)
    assert _codes(report) == ["open_order_quantity"]
    assert report["breaks"][0]["ledger"] == "3"
    assert report["breaks"][0]["broker"] == "2"


def test_unresolved_break_blocks_until_a_later_clean_run(db, tmp_path, monkeypatch):
    _init()
    _working(quantity="1", price="100")
    wrong = {
        "schema": BROKER_BOOK_SCHEMA,
        "mode": "paper",
        "as_of": NOW,
        "cash": "1000",
        "buying_power": "1000",
        "positions": [],
        "fills": [],
        "open_orders": [{
            "client_order_id": "buy-1", "symbol": "BTC", "side": "buy",
            "quantity": "1", "filled_quantity": "0", "leaves_quantity": "1",
        }],
    }
    _write(tmp_path, monkeypatch, wrong)
    assert reconcile_broker_book(now=NOW)["status"] == "breaks"
    assert "broker_reconciliation" in reconcile()["breaks"]
    with pytest.raises(ValueError, match="reconciliation_break"):
        _working("buy-2", quantity="1", price="10")
    corrected = dict(wrong)
    corrected["buying_power"] = "900"
    _write(tmp_path, monkeypatch, corrected)
    assert reconcile_broker_book(now=NOW)["status"] == "clean"
    assert reconcile()["passed"] is True
    assert _working("buy-2", quantity="1", price="10")["state"] == "accepted"


def test_run_is_idempotent_and_replayable(db, tmp_path, monkeypatch):
    _flat()
    _write(tmp_path, monkeypatch, _book())
    first = reconcile_broker_book(now=NOW)
    second = reconcile_broker_book(now=NOW)
    assert first["persisted"] is True
    assert second["persisted"] is False
    assert second["audit_seq"] == first["audit_seq"]
    history = broker_reconciliation_history()
    assert len(history) == 1
    assert history[0]["payload"]["status"] == "clean"
    assert history[0]["payload"]["breaks"] == []
    assert history[0]["payload"]["ledger_fingerprint"] == first["ledger_fingerprint"]
    _write(tmp_path, monkeypatch, _book(cash="699"))
    third = reconcile_broker_book(now=NOW)
    assert third["status"] == "breaks"
    assert third["persisted"] is True
    replay = broker_reconciliation_history()
    assert [row["payload"]["status"] for row in replay] == ["clean", "breaks"]
    assert replay[0]["payload"] == history[0]["payload"]


def test_internally_broken_ledger_is_not_clean(db, tmp_path, monkeypatch):
    _flat()
    import sqlite3
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE paper_exec_meta SET cash='701'")
    _write(tmp_path, monkeypatch, _book(cash="701"))
    report = reconcile_broker_book(now=NOW)
    assert report["status"] == "unreconciled"
    assert report["reason"] == "ledger_reconciliation_break"
    assert report["new_risk_allowed"] is False


def test_verified_order_snapshot_must_agree(db, tmp_path, monkeypatch):
    _forbid_network(monkeypatch)
    _flat()
    order = {
        "client_order_id": "buy-1", "symbol": "BTC", "side": "BUY",
        "quantity": "3", "limit_price": "100",
    }
    filled = {
        "id": "broker-1", "client_order_id": "buy-1", "status": "filled",
        "symbol": "BTC", "side": "buy", "qty": "3", "limit_price": "100",
        "type": "limit", "time_in_force": "day", "filled_qty": "3",
        "filled_avg_price": "100",
    }
    validate_broker_snapshot(order, filled)
    control = Database(str(tmp_path / "control.sqlite3"))
    control.put("broker-orders", "buy-1", {
        "fingerprint": digest(order), "status": "acknowledged",
        "order": order, "response": filled,
    })
    monkeypatch.setenv("OSIRIS_CONTROL_DB", str(tmp_path / "control.sqlite3"))
    _write(tmp_path, monkeypatch, _book())
    clean = reconcile_broker_book(now=NOW)
    assert clean["status"] == "clean"
    assert clean["live_trading_authorized"] is False

    partial = {
        **filled, "status": "partially_filled", "filled_qty": "1", "filled_avg_price": "100",
    }
    validate_broker_snapshot(order, partial)
    control.put("broker-orders", "buy-1", {
        "fingerprint": digest(order), "status": "acknowledged",
        "order": order, "response": partial,
    }, expected_version=1)
    disagreed = reconcile_broker_book(now=NOW)
    assert disagreed["status"] == "unreconciled"
    assert disagreed["reason"] == "snapshot_unverifiable"
    assert disagreed["breaks"] == []
    assert disagreed["new_risk_allowed"] is False


def test_control_plane_account_document_is_an_independent_snapshot(db, tmp_path, monkeypatch):
    _forbid_network(monkeypatch)
    _flat()
    document = _book()
    document["digest"] = broker_book_digest(document)
    control = Database(str(tmp_path / "control.sqlite3"))
    control.put("broker-book", "account", document)
    monkeypatch.setenv("OSIRIS_CONTROL_DB", str(tmp_path / "control.sqlite3"))
    report = reconcile_broker_book(now=NOW)
    assert report["status"] == "clean"
    assert report["snapshot_digest"] == document["digest"]
    assert report["live_trading_authorized"] is False


def test_unacknowledged_broker_order_is_unverifiable(db, tmp_path, monkeypatch):
    _forbid_network(monkeypatch)
    _flat()
    _write(tmp_path, monkeypatch, _book())
    control = Database(str(tmp_path / "control.sqlite3"))
    control.put("broker-orders", "buy-1", {
        "fingerprint": "unknown", "status": "submitting", "order": {"client_order_id": "buy-1"},
    })
    monkeypatch.setenv("OSIRIS_CONTROL_DB", str(tmp_path / "control.sqlite3"))
    report = reconcile_broker_book(now=NOW)
    assert report["status"] == "unreconciled"
    assert report["reason"] == "snapshot_unverifiable"
    assert report["new_risk_allowed"] is False


def test_routes_reject_caller_numbers_and_empty_body_uses_the_book(db, tmp_path, monkeypatch):
    _forbid_network(monkeypatch)
    _flat()
    _write(tmp_path, monkeypatch, _book())
    client = TestClient(app)
    spoiled = client.post("/portfolio/ledger/reconcile", json={
        "positions": [{"asset": "A", "quantity": 2}],
        "broker_positions": [{"asset": "A", "quantity": 2}],
        "cash": 100, "broker_cash": 100,
    })
    assert spoiled.status_code == 200
    assert spoiled.json()["status"] == "unreconciled"
    assert spoiled.json()["reason"] == "caller_supplied_numbers_rejected"
    assert spoiled.json()["new_risk_allowed"] is False
    assert spoiled.json()["live_trading_authorized"] is False
    assert broker_reconciliation_history() == []

    paper = client.post("/paper/trading/reconcile", json={
        "orders": [{"order_id": "o", "asset": "A", "side": "buy", "quantity": 1}],
        "fills": [], "positions": [{"asset": "A", "quantity": 1}],
    })
    assert paper.status_code == 200
    assert paper.json()["status"] == "unreconciled"
    assert paper.json()["new_risk_allowed"] is False

    supplied = client.post("/paper/execution/broker-reconcile", json={
        "now": NOW, "cash": "1", "positions": [],
    })
    assert supplied.status_code == 200
    assert supplied.json()["reason"] == "caller_supplied_numbers_rejected"
    assert broker_reconciliation_history() == []

    ran = client.post("/portfolio/ledger/reconcile", json={"now": NOW})
    assert ran.status_code == 200
    assert ran.json()["status"] == "clean"
    assert ran.json()["new_risk_allowed"] is True
    replay = client.get("/paper/execution/broker-reconcile")
    assert replay.status_code == 200
    assert replay.json()[0]["payload"]["status"] == "clean"
    assert replay.json()[0]["payload"]["live_trading_authorized"] is False


def test_expired_partial_day_order_reconciles_released_reservation(db, tmp_path, monkeypatch):
    """Expiry and independent reconciliation agree on fills and released cash."""
    _init()
    _working()
    assert _fill(quantity="1")["state"] == "partially_filled"
    expiry = "2026-11-09T00:00:00+00:00"
    assert expire_paper_orders(now=expiry)["expired"] == ["buy-1"]
    _write(tmp_path, monkeypatch, _book(
        as_of=expiry, cash="900", buying_power="900",
        positions=[{"symbol": "BTC", "quantity": "1"}],
        fills=[{
            "execution_id": "fill-1", "client_order_id": "buy-1", "symbol": "BTC",
            "side": "BUY", "quantity": "1", "price": "100",
        }],
        open_orders=[],
    ))
    report = reconcile_broker_book(now=expiry)
    assert report["status"] == "clean"
    assert report["breaks"] == []
    assert reconcile()["passed"] is True
