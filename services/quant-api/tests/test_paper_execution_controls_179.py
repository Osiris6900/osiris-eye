"""End-to-end paper execution failures. Live broker routing stays disabled."""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from osiris_api.main import app
from osiris_api.paper_execution import (
    initialize_paper_account,
    reconcile,
    recover,
    set_kill_switch,
    snapshot,
    submit_approved_workflow,
    submit_paper_order,
)
from osiris_api.paper_workflow import evaluate_paper_workflow
from osiris_api.risk import RiskLimits, check_execution


NOW = "2026-11-08T16:00:00+00:00"
FRESH = "2026-11-08T15:59:30+00:00"
STALE = "2026-11-08T14:00:00+00:00"


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "paper-exec.sqlite3"
    monkeypatch.setenv("OSIRIS_PAPER_EXECUTION_DB", str(path))
    monkeypatch.delenv("OSIRIS_ENABLE_LIVE_EXECUTION", raising=False)
    return path


def _init(**limits):
    initialize_paper_account("1000", RiskLimits(**limits) if limits else RiskLimits(
        max_order_notional=1_000, max_position_notional=5_000,
        max_daily_loss=1_000, max_gross_exposure=5_000,
    ))


def _buy(client_order_id="buy-1", **overrides):
    payload = dict(
        client_order_id=client_order_id, symbol="BTC", side="BUY",
        quantity="1", price="100", fee="0", observed_at=FRESH, now=NOW,
    )
    payload.update(overrides)
    return submit_paper_order(**payload)


def test_position_limit_rejects_second_buy_and_keeps_one_fill(db):
    _init(max_order_notional=1_000, max_position_notional=150,
          max_daily_loss=1_000, max_gross_exposure=5_000)
    first = _buy()
    assert first["status"] == "filled"
    assert first["live_execution"] is False
    assert first["broker_order_submitted"] is False
    second = _buy("buy-2")
    assert second["status"] == "risk_rejected"
    assert second["reason"] == "max_position_notional_exceeded"
    assert second["limits_evaluated"] is True
    assert second["fill"] is None
    state = snapshot()
    assert state["fill_count"] == 1
    assert state["cash"] == "900"
    assert state["positions"] == [{"symbol": "BTC", "quantity": "1", "average_cost": "100"}]
    assert reconcile()["passed"] is True


def test_exposure_budget_rejects_without_a_second_fill(db):
    _init(max_order_notional=1_000, max_position_notional=5_000,
          max_daily_loss=1_000, max_gross_exposure=150)
    assert _buy()["status"] == "filled"
    rejected = _buy("buy-2")
    assert rejected["reason"] == "exposure_budget_exceeded"
    assert snapshot()["fill_count"] == 1
    assert snapshot()["cash"] == "900"
    assert reconcile()["passed"] is True


def test_kill_switch_mid_flow_blocks_new_orders(db):
    _init()
    assert _buy()["status"] == "filled"
    tripped = set_kill_switch(True)
    assert tripped["kill_switch_active"] is True
    blocked = _buy("buy-2", quantity="1", price="10")
    assert blocked["status"] == "risk_rejected"
    assert blocked["reason"] == "kill_switch_active"
    assert blocked["limits_evaluated"] is False
    flatten = submit_paper_order(
        client_order_id="sell-1", symbol="BTC", side="SELL",
        quantity="1", price="100", observed_at=FRESH, now=NOW,
    )
    assert flatten["reason"] == "kill_switch_active"
    assert flatten["fill"] is None
    assert snapshot()["fill_count"] == 1
    assert snapshot()["positions"][0]["quantity"] == "1"
    assert reconcile()["passed"] is True


def test_stale_and_missing_feed_persist_a_rejection_that_cannot_fill_later(db):
    _init()
    missing = _buy(observed_at=None)
    assert missing["reason"] == "stale_market_data"
    assert missing["limits_evaluated"] is False
    assert missing["fill"] is None
    replay = _buy(observed_at=FRESH)
    assert replay["status"] == "idempotent_replay"
    assert replay["original_status"] == "risk_rejected"
    assert snapshot()["fill_count"] == 0
    future = _buy("buy-future", observed_at="2026-11-08T16:05:00+00:00")
    assert future["reason"] == "stale_market_data"
    old = _buy("buy-old", observed_at=STALE)
    assert old["reason"] == "stale_market_data"
    assert snapshot()["fill_count"] == 0
    assert snapshot()["cash"] == "1000"


def test_duplicate_client_order_id_after_restart_does_not_fill_again(db):
    _init()
    first = _buy()
    before = snapshot()["audit"]
    resumed = recover()
    assert resumed["audit_ok"] is True
    assert resumed["snapshot"]["cash"] == "900"
    assert resumed["snapshot"]["fill_count"] == 1
    replay = _buy()
    assert replay["status"] == "idempotent_replay"
    assert replay["risk_decision_id"] == first["risk_decision_id"]
    assert replay["fill"]["seq"] == first["fill"]["seq"]
    after = snapshot()
    assert after["fill_count"] == 1
    assert after["audit"] == before
    assert reconcile()["passed"] is True


def test_idempotency_conflict_preserves_original_audit(db):
    _init()
    _buy()
    before = snapshot()["audit"]
    with pytest.raises(ValueError, match="idempotency_key_conflict"):
        _buy(quantity="2")
    assert snapshot()["audit"] == before
    assert snapshot()["fill_count"] == 1


def test_crash_before_commit_rolls_back_and_retry_can_fill(db):
    _init()
    with pytest.raises(RuntimeError, match="simulated_crash_before_commit"):
        _buy(simulate_crash=True)
    state = snapshot()
    assert state["fill_count"] == 0
    assert state["order_count"] == 0
    assert state["cash"] == "1000"
    assert [row["event"] for row in state["audit"]] == ["account_initialized"]
    assert recover()["passed"] is True
    filled = _buy()
    assert filled["status"] == "filled"
    assert snapshot()["fill_count"] == 1
    assert reconcile()["passed"] is True


def test_cash_position_and_pnl_reconcile_after_restart(db):
    _init(max_order_notional=1_000, max_position_notional=5_000,
          max_daily_loss=1_000, max_gross_exposure=5_000)
    assert _buy(quantity="2", price="100", fee="1")["status"] == "filled"
    sold = submit_paper_order(
        client_order_id="sell-1", symbol="BTC", side="SELL",
        quantity="1", price="120", fee="1", observed_at=FRESH, now=NOW,
    )
    assert sold["status"] == "filled"
    assert sold["fill"]["realized_pnl"] == "18.5"
    resumed = recover()["snapshot"]
    assert resumed["cash"] == "918"
    assert resumed["realized_pnl"] == "18.5"
    assert resumed["positions"] == [{"symbol": "BTC", "quantity": "1", "average_cost": "100.5"}]
    assert reconcile() == {
        "initialized": True, "passed": True, "breaks": [],
        "live_execution": False, "broker_order_submitted": False, "mode": "paper_only",
    }


def test_realized_loss_blocks_new_buys_but_allows_a_reducing_sell(db):
    _init(max_order_notional=1_000, max_position_notional=5_000,
          max_daily_loss=10, max_gross_exposure=5_000)
    assert _buy(quantity="2", price="100")["status"] == "filled"
    assert submit_paper_order(
        client_order_id="sell-1", symbol="BTC", side="SELL",
        quantity="1", price="50", observed_at=FRESH, now=NOW,
    )["status"] == "filled"
    blocked = _buy("buy-again")
    assert blocked["reason"] == "daily_loss_limit_reached"
    assert blocked["fill"] is None
    flattened = submit_paper_order(
        client_order_id="sell-2", symbol="BTC", side="SELL",
        quantity="1", price="50", observed_at=FRESH, now=NOW,
    )
    assert flattened["status"] == "filled"
    state = snapshot()
    assert state["positions"] == []
    assert state["cash"] == "900"
    assert state["realized_pnl"] == "-100"
    assert reconcile()["passed"] is True


def test_unfunded_buy_does_not_go_negative_or_fill(db):
    initialize_paper_account("50")
    rejected = _buy()
    assert rejected["status"] == "execution_rejected"
    assert rejected["reason"] == "insufficient_paper_cash"
    assert rejected["fill"] is None
    assert snapshot()["cash"] == "50"
    assert snapshot()["fill_count"] == 0
    replay = _buy()
    assert replay["status"] == "idempotent_replay"
    assert snapshot()["fill_count"] == 0


def test_risk_service_outage_fails_closed(db, monkeypatch):
    _init()
    down = _buy(risk_service_available=False)
    assert down["reason"] == "risk_service_unavailable"
    assert down["limits_evaluated"] is False
    assert snapshot()["fill_count"] == 0

    def explode(**kwargs):
        raise RuntimeError("risk service down")

    monkeypatch.setattr("osiris_api.paper_execution.check_execution", explode)
    raised = _buy("buy-2")
    assert raised["reason"] == "risk_service_unavailable"
    assert raised["fill"] is None
    assert snapshot()["fill_count"] == 0
    assert snapshot()["cash"] == "1000"


def test_live_execution_request_and_env_are_refused(db, monkeypatch):
    _init()
    with pytest.raises(ValueError, match="live_execution_disabled"):
        _buy(live_execution=True)
    assert snapshot()["fill_count"] == 0
    monkeypatch.setenv("OSIRIS_ENABLE_LIVE_EXECUTION", "1")
    with pytest.raises(ValueError, match="live_execution_disabled"):
        _buy("buy-2")
    assert snapshot()["fill_count"] == 0
    assert snapshot()["cash"] == "1000"


def test_tampered_cash_or_audit_blocks_new_orders(db):
    _init()
    _buy()
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE paper_exec_meta SET cash='1' WHERE id=1")
    assert reconcile()["passed"] is False
    assert "cash" in reconcile()["breaks"]
    with pytest.raises(ValueError, match="reconciliation_break"):
        _buy("buy-2")
    with pytest.raises(ValueError, match="reconciliation_break"):
        recover()
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE paper_exec_meta SET cash='900' WHERE id=1")
        conn.execute("UPDATE paper_exec_audit SET payload='{}' WHERE seq=2")
    with pytest.raises(ValueError, match="audit_chain_corrupt"):
        recover()
    with pytest.raises(ValueError, match="audit_chain_corrupt"):
        _buy("buy-3")
    assert snapshot()["fill_count"] == 1


def test_blocked_workflow_never_opens_the_ledger(tmp_path, monkeypatch):
    path = tmp_path / "unused.sqlite3"
    monkeypatch.setenv("OSIRIS_PAPER_EXECUTION_DB", str(path))
    blocked = evaluate_paper_workflow(
        symbol="BTC-USD", signals=[0.0, 1.0], asset_returns=[0.0, 0.01],
        quantity=1, price=100, enforce_execution_controls=True,
        kill_switch_active=False, market_data_fresh=False, risk_service_available=True,
    )
    result = submit_approved_workflow(
        blocked, client_order_id="nope", observed_at=FRESH, now=NOW,
    )
    assert result["status"] == "not_submitted"
    assert result["reason"] == "stale_market_data"
    assert result["live_execution"] is False
    assert path.exists() is False


def test_approved_workflow_submits_one_paper_fill(db):
    _init()
    approved = evaluate_paper_workflow(
        symbol="btc", signals=[0.0, 1.0], asset_returns=[0.0, 0.01],
        quantity=1, price=100, enforce_execution_controls=True,
        kill_switch_active=False, market_data_fresh=True, risk_service_available=True,
        current_gross_exposure=0,
    )
    submitted = submit_approved_workflow(
        approved, client_order_id="wf-1", observed_at=FRESH, now=NOW,
    )
    assert submitted["status"] == "filled"
    assert submitted["live_execution"] is False
    assert snapshot()["fill_count"] == 1
    assert recover()["snapshot"]["cash"] == "900"


def test_http_routes_stay_paper_only(db):
    client = TestClient(app)
    opened = client.post("/paper/execution/initialize", json={
        "initial_cash": "1000",
        "limits": {"max_order_notional": 1000, "max_position_notional": 5000,
                   "max_daily_loss": 1000, "max_gross_exposure": 5000},
    })
    assert opened.status_code == 200
    assert opened.json()["live_execution"] is False
    live = client.post("/paper/execution/orders", json={
        "client_order_id": "live-1", "symbol": "BTC", "side": "BUY",
        "quantity": "1", "price": "100", "observed_at": FRESH, "now": NOW,
        "live_execution": True,
    })
    assert live.status_code == 422
    assert live.json()["detail"] == "live_execution_disabled"
    filled = client.post("/paper/execution/orders", json={
        "client_order_id": "http-1", "symbol": "BTC", "side": "BUY",
        "quantity": "1", "price": "100", "observed_at": FRESH, "now": NOW,
    })
    assert filled.status_code == 200
    assert filled.json()["status"] == "filled"
    assert filled.json()["broker_order_submitted"] is False
    halted = client.post("/paper/execution/kill-switch", json={"active": True})
    assert halted.status_code == 200
    blocked = client.post("/paper/execution/orders", json={
        "client_order_id": "http-2", "symbol": "BTC", "side": "BUY",
        "quantity": "1", "price": "10", "observed_at": FRESH, "now": NOW,
    })
    assert blocked.json()["reason"] == "kill_switch_active"
    snap = client.get("/paper/execution/snapshot")
    assert snap.json()["fill_count"] == 1
    assert snap.json()["live_execution"] is False
    assert client.get("/paper/execution/reconcile").json()["passed"] is True


def test_direct_execution_gate_matches_fail_closed_defaults():
    approved, reason, evaluated = check_execution(
        side="BUY", quantity=1, price=100, current_position_notional=0,
        current_gross_exposure=0, daily_pnl=0,
        kill_switch_active=False, market_data_fresh=True, risk_service_available=True,
    )
    assert (approved, reason, evaluated) == (True, "approved_for_simulation_only", True)
    closed = check_execution(
        side="BUY", quantity=1, price=100, current_position_notional=0,
        current_gross_exposure=0, daily_pnl=0,
    )
    assert closed == (False, "risk_service_unavailable", False)
