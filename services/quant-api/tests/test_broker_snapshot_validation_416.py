"""Malformed independent broker evidence must close the persisted order gate."""

import json

import pytest

from osiris_api.control_plane.database import Database, digest
from osiris_api.control_plane.vendors import HttpJson
from osiris_api.paper_execution import (
    BROKER_BOOK_SCHEMA,
    broker_book_digest,
    broker_reconciliation_history,
    initialize_paper_account,
    reconcile_broker_book,
    submit_paper_order,
)

NOW = "2026-11-08T16:00:00+00:00"


@pytest.fixture(autouse=True)
def isolated_book(tmp_path, monkeypatch):
    monkeypatch.setenv("OSIRIS_PAPER_EXECUTION_DB", str(tmp_path / "paper.sqlite3"))
    monkeypatch.delenv("OSIRIS_CONTROL_DB", raising=False)
    monkeypatch.delenv("OSIRIS_PAPER_BROKER_SNAPSHOT", raising=False)
    monkeypatch.delenv("OSIRIS_ENABLE_LIVE_EXECUTION", raising=False)

    def no_network(*_args, **_kwargs):
        raise AssertionError("broker snapshot verification must not use HTTP")

    monkeypatch.setattr(HttpJson, "request", no_network)
    initialize_paper_account("1000")


def _book(**changes):
    document = {
        "schema": BROKER_BOOK_SCHEMA,
        "mode": "paper",
        "as_of": NOW,
        "cash": "1000",
        "buying_power": "1000",
        "positions": [],
        "fills": [],
        "open_orders": [],
    }
    document.update(changes)
    return document


def _seal(document):
    return {**document, "digest": broker_book_digest(document)}


def _write(tmp_path, monkeypatch, document):
    path = tmp_path / "broker-book.json"
    path.write_text(json.dumps(_seal(document)), encoding="utf-8")
    monkeypatch.setenv("OSIRIS_PAPER_BROKER_SNAPSHOT", str(path))
    return path


def _assert_rejected():
    report = reconcile_broker_book(now=NOW)
    assert report["status"] == "unreconciled"
    assert report["reason"] == "snapshot_unverifiable"
    assert report["new_risk_allowed"] is False
    assert report["live_execution"] is False
    assert report["live_trading_authorized"] is False
    assert report["persisted"] is True
    history = broker_reconciliation_history()
    assert history[-1]["payload"]["status"] == "unreconciled"
    assert history[-1]["payload"]["reason"] == "snapshot_unverifiable"
    with pytest.raises(ValueError, match="^reconciliation_break$"):
        submit_paper_order(
            client_order_id="blocked-order",
            symbol="BTC",
            side="BUY",
            quantity="1",
            price="100",
            fee="0",
            observed_at=NOW,
            now=NOW,
            working=True,
        )
    return report


@pytest.mark.parametrize("field", ["positions", "fills", "open_orders"])
@pytest.mark.parametrize("invalid", [{}, ""])
def test_empty_non_list_collection_cannot_report_clean(tmp_path, monkeypatch, field, invalid):
    _write(tmp_path, monkeypatch, _book(**{field: invalid}))
    _assert_rejected()


@pytest.mark.parametrize("field", ["positions", "fills", "open_orders"])
def test_required_collection_cannot_be_omitted(tmp_path, monkeypatch, field):
    document = _book()
    document.pop(field)
    _write(tmp_path, monkeypatch, document)
    _assert_rejected()


@pytest.mark.parametrize(
    "quantities", [("0", "0"), ("0", "3"), ("3", "0"), ("0", "0", "3"), ("-0", "0")]
)
def test_zero_positions_do_not_hide_normalized_duplicate_symbols(
    tmp_path,
    monkeypatch,
    quantities,
):
    document = _book()
    if "3" in quantities:
        filled = submit_paper_order(
            client_order_id="seed-buy",
            symbol="BTC",
            side="BUY",
            quantity="3",
            price="100",
            fee="0",
            observed_at=NOW,
            now=NOW,
        )
        assert filled["state"] == "filled"
        document.update(
            {
                "cash": "700",
                "buying_power": "700",
                "fills": [
                    {
                        "execution_id": filled["fill"]["client_fill_id"],
                        "client_order_id": "seed-buy",
                        "symbol": "BTC",
                        "side": "BUY",
                        "quantity": "3",
                        "price": "100",
                    }
                ],
            }
        )
    document["positions"] = [
        {"symbol": "BTC" if index % 2 else "btc", "quantity": quantity}
        for index, quantity in enumerate(quantities)
    ]
    _write(tmp_path, monkeypatch, document)
    _assert_rejected()


@pytest.mark.parametrize(
    "positions",
    [
        [],
        [{"symbol": "btc", "quantity": "0"}],
        [{"symbol": "BTC", "quantity": "0"}, {"symbol": "ETH", "quantity": "0"}],
    ],
)
def test_empty_lists_and_unique_zero_positions_remain_valid(tmp_path, monkeypatch, positions):
    _write(tmp_path, monkeypatch, _book(positions=positions))
    report = reconcile_broker_book(now=NOW)
    assert report["status"] == "clean"
    assert report["new_risk_allowed"] is True


@pytest.mark.parametrize("flag", [None, False])
def test_explicit_inactive_live_authorization_remains_valid(tmp_path, monkeypatch, flag):
    _write(tmp_path, monkeypatch, _book(live_trading_authorized=flag))
    assert reconcile_broker_book(now=NOW)["status"] == "clean"


@pytest.mark.parametrize("flag", [0, 0.0, "", "false", [], {}, True])
def test_false_aliases_cannot_authorize_broker_evidence(tmp_path, monkeypatch, flag):
    _write(tmp_path, monkeypatch, _book(live_trading_authorized=flag))
    _assert_rejected()


def test_excessively_nested_digested_metadata_is_unverifiable(tmp_path, monkeypatch):
    extra = "leaf"
    for _ in range(40):
        extra = {"nested": extra}
    _write(tmp_path, monkeypatch, _book(metadata=extra))
    _assert_rejected()


@pytest.mark.parametrize("as_of", ["0001-01-01T00:00:00+23:59", "9999-12-31T23:59:59-23:59"])
def test_timestamp_utc_overflow_closes_a_previously_clean_gate(tmp_path, monkeypatch, as_of):
    _write(tmp_path, monkeypatch, _book())
    assert reconcile_broker_book(now=NOW)["status"] == "clean"
    _write(tmp_path, monkeypatch, _book(as_of=as_of))
    _assert_rejected()
    assert [item["payload"]["status"] for item in broker_reconciliation_history()] == [
        "clean",
        "unreconciled",
    ]


@pytest.mark.parametrize("source", ["fixture", "control-book", "control-orders"])
@pytest.mark.parametrize("corruption", ["deep", "oversized"])
def test_malformed_raw_json_closes_a_previously_clean_gate(
    tmp_path,
    monkeypatch,
    source,
    corruption,
):
    if corruption == "deep":
        # Construct bytes directly: recursive fixture construction or serialization
        # would exercise the test process instead of the broker input boundary.
        malformed = "[" * 20_000 + '"leaf"' + "]" * 20_000
    else:
        # Fewer than 1 MiB characters, but more than 1 MiB UTF-8 bytes. For the
        # book sources this would otherwise be digest-valid, matching evidence.
        malformed = json.dumps(_seal(_book(metadata="é" * 524_288)), ensure_ascii=False)
        assert len(malformed) < 1_048_576 < len(malformed.encode("utf-8"))
    if source == "fixture":
        path = _write(tmp_path, monkeypatch, _book())
        control = None
    else:
        path = tmp_path / "control.sqlite3"
        control = Database(str(path))
        control.put("broker-book", "account", _seal(_book()))
        monkeypatch.setenv("OSIRIS_CONTROL_DB", str(path))
        if source == "control-orders":
            order = {
                "client_order_id": "broker-intent",
                "symbol": "BTC",
                "side": "BUY",
                "quantity": "1",
                "limit_price": "100",
            }
            response = {
                "id": "broker-1",
                "client_order_id": "broker-intent",
                "symbol": "BTC",
                "side": "buy",
                "qty": "1",
                "limit_price": "100",
                "type": "limit",
                "time_in_force": "day",
                "status": "rejected",
                "filled_qty": "0",
                "filled_avg_price": None,
            }
            control.put(
                "broker-orders",
                "broker-intent",
                {
                    "order": order,
                    "response": response,
                    "fingerprint": digest(order),
                    "status": "acknowledged",
                },
            )
    assert reconcile_broker_book(now=NOW)["status"] == "clean"
    if source == "fixture":
        path.write_text(malformed, encoding="utf-8")
    else:
        collection, key = (
            ("broker-book", "account")
            if source == "control-book"
            else ("broker-orders", "broker-intent")
        )
        # Malformed stored bytes cannot be inserted through Database.put's
        # canonical encoder, so inject corruption through the underlying DB.
        with control.transaction() as conn:
            conn.execute(
                "UPDATE cp_documents SET body=? WHERE collection=? AND id=?",
                (malformed, collection, key),
            )
    _assert_rejected()
    assert [item["payload"]["status"] for item in broker_reconciliation_history()] == [
        "clean",
        "unreconciled",
    ]
