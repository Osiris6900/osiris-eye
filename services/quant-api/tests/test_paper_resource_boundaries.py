"""Financial inputs cannot expand unboundedly; refusal logging cannot provision state."""

import sqlite3
from decimal import Decimal
from fractions import Fraction

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from osiris_api import durable_paper_ledger as legacy
from osiris_api import paper_execution as execution
from osiris_api.decimal_safety import bounded_decimal

NOW = "2026-11-12T12:00:00+00:00"
ATTACKS = [
    pytest.param("1e-999999999", id="tiny-exponent"),
    pytest.param("1e999999999", id="huge-exponent"),
    pytest.param("0e999999999", id="zero-with-huge-exponent"),
    pytest.param("1e-1001", id="exponent-past-boundary"),
    pytest.param("1e1001", id="positive-exponent-past-boundary"),
    pytest.param("1" * 257, id="long-coefficient"),
    pytest.param("1" * 1260, id="long-input-text"),
    pytest.param(Decimal("1e-999999999"), id="decimal-object-exponent"),
    pytest.param(10**5000, id="large-python-integer"),
]


@pytest.fixture
def ledger_paths(tmp_path, monkeypatch):
    canonical, old = tmp_path / "canonical.sqlite3", tmp_path / "legacy.sqlite3"
    monkeypatch.setenv("OSIRIS_PAPER_EXECUTION_DB", str(canonical))
    monkeypatch.setenv("OSIRIS_PAPER_LEDGER_DB", str(old))
    monkeypatch.delenv("OSIRIS_ENABLE_LIVE_EXECUTION", raising=False)
    return canonical, old


def order(**updates):
    return execution.submit_paper_order(
        **{
            "client_order_id": "resource-boundary",
            "symbol": "BTC",
            "side": "BUY",
            "quantity": "1",
            "price": "100",
            "fee": "0",
            "observed_at": NOW,
            "now": NOW,
            **updates,
        }
    )


@pytest.mark.parametrize("value", ATTACKS)
@pytest.mark.parametrize(
    "initialize,index", [(execution.initialize_paper_account, 0), (legacy.initialize_account, 1)]
)
def test_unbounded_cash_is_rejected_before_any_database_is_created(
    ledger_paths, value, initialize, index
):
    with pytest.raises(ValueError, match="decimal_precision_out_of_range"):
        initialize(value)
    assert not ledger_paths[index].exists()


@pytest.mark.parametrize("value", ATTACKS)
@pytest.mark.parametrize("field", ["quantity", "price", "fee"])
def test_bad_order_values_cannot_format_expand_or_modify_financial_state(
    ledger_paths, field, value
):
    execution.initialize_paper_account("1000")
    before = execution.snapshot()
    with pytest.raises(ValueError, match="decimal_precision_out_of_range"):
        order(**{field: value})
    assert execution.snapshot() == before


@pytest.mark.parametrize("field", ["quantity", "price", "fee"])
def test_bad_partial_fill_does_not_change_the_working_order(ledger_paths, field):
    execution.initialize_paper_account("1000")
    assert order(working=True)["state"] == "accepted"
    before = execution.snapshot()
    with pytest.raises(ValueError, match="decimal_precision_out_of_range"):
        execution.apply_paper_fill(
            **{
                "client_order_id": "resource-boundary",
                "client_fill_id": "bad-fill",
                "quantity": "1",
                "price": "100",
                "fee": "0",
                "observed_at": NOW,
                "now": NOW,
                field: "1e-999999999",
            }
        )
    assert execution.snapshot() == before


@pytest.mark.parametrize("field", ["quantity", "price", "fee"])
def test_legacy_fill_uses_the_same_resource_boundaries(ledger_paths, field):
    legacy.initialize_account("1000")
    before = legacy.snapshot()
    with pytest.raises(ValueError, match="decimal_precision_out_of_range"):
        legacy.synthetic_fill(
            **{
                "key": "bad",
                "symbol": "BTC",
                "side": "BUY",
                "quantity": "1",
                "price": "100",
                "fee": "0",
                field: "1e999999999",
            }
        )
    assert legacy.snapshot() == before


def test_mark_rejection_preserves_existing_positions_and_marks(ledger_paths):
    execution.initialize_paper_account("1000")
    order()
    execution.mark_to_market({"BTC": "110"}, observed_at=NOW, now=NOW)
    before = execution.snapshot()
    with pytest.raises(ValueError, match="decimal_precision_out_of_range"):
        execution.mark_to_market({"BTC": "1e999999999"}, observed_at=NOW, now=NOW)
    assert execution.snapshot() == before


def test_normal_exact_decimals_and_legacy_float_contract_are_preserved(ledger_paths):
    assert (
        execution.initialize_paper_account("1000.000000000000000001")["cash"]
        == "1000.000000000000000001"
    )
    order(quantity="0.000000000000000001", price="1")
    state = execution.snapshot()
    assert state["cash"] == "1000"
    assert state["positions"][0]["quantity"] == "0.000000000000000001"
    assert execution.reconcile()["passed"] is True
    assert bounded_decimal("1e-200") == Decimal("1e-200")
    assert legacy.initialize_account(1000.25)["cash"] == "1000.25"
    legacy.synthetic_fill("normal", "BTC", "BUY", 1.5, 100.0)
    assert Decimal(legacy.snapshot()["cash"]) == Decimal("850.25")


def test_reconciliation_never_expands_a_corrupt_stored_decimal(ledger_paths):
    execution.initialize_paper_account("1000")
    with sqlite3.connect(ledger_paths[0]) as db:
        db.execute("UPDATE paper_exec_meta SET cash='1e-999999999'")
    with pytest.raises(ValueError, match="decimal_precision_out_of_range"):
        execution.reconciliation_report()


@pytest.mark.parametrize("quantity", ["1e-255", "1e-300"])
def test_small_exact_orders_survive_canonical_audit_replay(ledger_paths, quantity):
    execution.initialize_paper_account("1000")
    assert order(quantity=quantity, price="1")["state"] == "filled"
    state = execution.snapshot()
    assert state["fill_count"] == 1
    assert Fraction(state["cash"]) == Fraction("1000") - Fraction(quantity)
    assert Decimal(state["positions"][0]["quantity"]) == Decimal(quantity)
    assert execution.reconcile()["passed"] is True
    assert order(quantity=quantity, price="1")["status"] == "idempotent_replay"
    replayed = execution.snapshot()
    for field in ("cash", "positions", "fill_count", "realized_pnl"):
        assert replayed[field] == state[field]
    assert execution.reconcile()["passed"] is True


@pytest.mark.parametrize("cash", ["1e1000", "1e-1000"])
def test_bounded_account_values_remain_readable_after_fixed_point_storage(ledger_paths, cash):
    execution.initialize_paper_account(cash)
    assert Decimal(execution.snapshot()["cash"]) == Decimal(cash)
    assert execution.reconcile()["passed"] is True


@pytest.mark.parametrize("env_flag,requested", [("", True), ("1", False)])
def test_refusal_does_not_create_a_missing_database_or_directory(
    tmp_path, monkeypatch, env_flag, requested
):
    target = tmp_path / "missing-parent" / "account.sqlite3"
    monkeypatch.setenv("OSIRIS_PAPER_EXECUTION_DB", str(target))
    monkeypatch.setenv("OSIRIS_ENABLE_LIVE_EXECUTION", env_flag)
    with pytest.raises(ValueError, match="live_execution_disabled"):
        order(live_execution=requested)
    assert not target.parent.exists()


@pytest.mark.parametrize("kind", ["empty", "legacy", "corrupt"])
def test_refusal_does_not_initialize_migrate_or_rewrite_an_existing_file(
    tmp_path, monkeypatch, kind
):
    target = tmp_path / "existing.sqlite3"
    if kind == "corrupt":
        target.write_bytes(b"not a sqlite database")
    else:
        with sqlite3.connect(target) as db:
            db.execute("CREATE TABLE paper_exec_meta(id INTEGER PRIMARY KEY, cash TEXT)")
            if kind == "legacy":
                db.execute("INSERT INTO paper_exec_meta VALUES(1,'1000')")
    before = target.read_bytes()
    monkeypatch.setenv("OSIRIS_PAPER_EXECUTION_DB", str(target))
    monkeypatch.delenv("OSIRIS_ENABLE_LIVE_EXECUTION", raising=False)
    with pytest.raises(ValueError, match="live_execution_disabled"):
        order(live_execution=True)
    assert target.read_bytes() == before


def test_valid_account_still_records_refusal_with_uri_sensitive_filename(tmp_path, monkeypatch):
    target = tmp_path / "account #1?.sqlite3"
    monkeypatch.setenv("OSIRIS_PAPER_EXECUTION_DB", str(target))
    monkeypatch.delenv("OSIRIS_ENABLE_LIVE_EXECUTION", raising=False)
    execution.initialize_paper_account("1000")
    with pytest.raises(ValueError, match="live_execution_disabled"):
        order(live_execution=True)
    state = execution.snapshot()
    assert state["audit"][-1]["event"] == "live_execution_refused"
    assert state["cash"] == "1000" and state["fill_count"] == 0
    assert execution.reconcile()["passed"] is True


def test_http_rejects_resource_amplification_without_provisioning_a_ledger(ledger_paths):
    app = FastAPI()
    app.include_router(execution.router)
    client = TestClient(app)
    response = client.post("/paper/execution/initialize", json={"initial_cash": "1e-999999999"})
    assert response.status_code == 422
    assert response.json() == {"detail": "decimal_precision_out_of_range"}
    assert not ledger_paths[0].exists()
    response = client.post("/paper/execution/orders", json={"live_execution": True})
    assert response.status_code == 422
    assert response.json() == {"detail": "live_execution_disabled"}
    assert not ledger_paths[0].exists()
