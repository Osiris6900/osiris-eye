"""Package 47: paper submissions use one fail-closed server-side risk check.

Caller position, gross, daily P&L, kill switch, freshness, and limits cannot
approve an order. The paper ledger, its kill switch, and its marks are the
inputs. A missing check, a check error, or a check that runs past the budget
rejects the order. Approvals and rejections are written to the paper audit.
"""
import json
from datetime import datetime, timezone

import pytest

from osiris_api.control_plane.broker import AlpacaPaperBroker
from osiris_api.control_plane.database import Database
from osiris_api.paper_execution import (
    LIVE_EXECUTION_ENABLED,
    initialize_paper_account,
    mark_to_market,
    reconcile,
    set_kill_switch,
    snapshot,
    submit_paper_order,
)
from osiris_api.paper_execution_gate import PaperOrderContext, authorize_paper_order
from osiris_api.risk import RiskLimits, check_execution

# Same reason map as test_risk_execution_conformance.py and the Rust kernel.
KERNEL_REASONS = {
    "approved_for_simulation_only": "approved",
    "invalid_numeric_input": "non_finite_input",
    "invalid_order_or_position": "invalid_order",
    "invalid_risk_limits": "invalid_order",
    "max_order_notional_exceeded": "order_limit",
    "max_position_notional_exceeded": "position_limit",
    "exposure_budget_exceeded": "exposure_budget",
    "daily_loss_limit_reached": "daily_loss_limit",
    "kill_switch_active": "kill_switch_active",
    "stale_market_data": "stale_market_data",
    "risk_service_unavailable": "risk_service_unavailable",
}

FAVORABLE = {
    "limits": RiskLimits(),
    "position": 0,
    "gross": 0,
    "daily_pnl": 0,
    "kill_switch": False,
    "fresh": True,
}


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def open_book(
    monkeypatch,
    tmp_path,
    *,
    limits: RiskLimits | None = None,
    shares: str = "0",
    price: str = "100",
    mark_price: str | None = None,
    marked: bool = True,
    stale: bool = False,
):
    """Initialize the paper ledger. Risk numbers live here, not on the broker call."""
    monkeypatch.setenv("OSIRIS_PAPER_EXECUTION_DB", str(tmp_path / "paper.sqlite3"))
    monkeypatch.delenv("OSIRIS_ENABLE_LIVE_EXECUTION", raising=False)
    initialize_paper_account("1000000", limits or RiskLimits(), "86400", "120")
    stamp = _stamp()
    if shares not in {"0", "0.0"}:
        filled = submit_paper_order(
            client_order_id="seed-buy", symbol="TEST", side="BUY",
            quantity=shares, price=price, fee="0", observed_at=stamp, now=stamp,
        )
        assert filled["status"] == "filled", filled
    if marked:
        observed = "2020-01-01T00:00:00+00:00" if stale else stamp
        mark_to_market(
            {"TEST": mark_price or price},
            observed_at=observed,
            now=observed if stale else stamp,
        )
    return stamp


def _order(**changes):
    body = {
        "client_order_id": "order-1", "symbol": "TEST", "side": "BUY",
        "quantity": "1", "limit_price": "100",
    }
    body.update(changes)
    return body


class _Transport:
    def __init__(self, response=None):
        self.calls = []
        self.response = response or {
            "id": "broker-1", "client_order_id": "order-1", "status": "accepted",
            "symbol": "TEST", "side": "buy", "qty": "1", "limit_price": "100",
            "type": "limit", "time_in_force": "day", "filled_qty": "0",
            "filled_avg_price": None,
        }

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.response


def _service(tmp_path, transport):
    return AlpacaPaperBroker(
        Database(str(tmp_path / "broker.db")),
        {"key": "FIXTURE", "secret": "FIXTURE"},
        enabled=True,
        transport=transport,
    )


def _decisions():
    found = []
    for row in snapshot()["audit"]:
        if row["event"] == "pretrade_risk_decision":
            found.append(json.loads(row["payload"]))
    return found


def test_live_execution_flag_stays_disabled():
    assert LIVE_EXECUTION_ENABLED is False


def test_broker_ignores_favorable_caller_kill_switch(tmp_path, monkeypatch):
    open_book(monkeypatch, tmp_path)
    set_kill_switch(True)
    transport = _Transport()
    with pytest.raises(ValueError, match="kill_switch_active"):
        _service(tmp_path, transport).submit(_order(), **FAVORABLE)
    assert transport.calls == []
    assert Database(str(tmp_path / "broker.db")).get("broker-orders", "order-1") is None
    decision = _decisions()[-1]
    assert decision["approved"] is False
    assert decision["reason"] == "kill_switch_active"
    assert decision["inputs"]["persisted_kill_switch"] is True
    assert decision["inputs"]["kill_switch_active"] is True
    assert decision["live_execution"] is False
    assert decision["broker_order_submitted"] is False
    assert reconcile()["passed"] is True


def test_broker_rejects_missing_and_stale_marks_despite_caller_freshness(tmp_path, monkeypatch):
    open_book(monkeypatch, tmp_path, marked=False)
    transport = _Transport()
    service = _service(tmp_path, transport)
    with pytest.raises(ValueError, match="stale_market_data"):
        service.submit(_order(), **FAVORABLE)
    mark_to_market(
        {"TEST": "100"},
        observed_at="2020-01-01T00:00:00+00:00",
        now="2020-01-01T00:00:00+00:00",
    )
    with pytest.raises(ValueError, match="timestamp_untrusted"):
        service.submit(_order(client_order_id="order-2"), **FAVORABLE)
    assert transport.calls == []
    reasons = [row["reason"] for row in _decisions()]
    assert reasons == ["stale_market_data", "timestamp_untrusted"]
    assert all(row["inputs"]["market_data_fresh"] is False for row in _decisions())
    assert reconcile()["passed"] is True


def test_broker_rejects_when_the_ledger_is_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("OSIRIS_PAPER_EXECUTION_DB", raising=False)
    monkeypatch.delenv("OSIRIS_ENABLE_LIVE_EXECUTION", raising=False)
    transport = _Transport()
    with pytest.raises(ValueError, match="risk_service_unavailable"):
        _service(tmp_path, transport).submit(_order(), **FAVORABLE)
    assert transport.calls == []


def test_broker_rejects_when_the_risk_check_errors(tmp_path, monkeypatch):
    open_book(monkeypatch, tmp_path)

    def explode(**kwargs):
        raise RuntimeError("risk gateway down")

    monkeypatch.setattr(
        "osiris_api.control_plane.broker.check_execution", explode, raising=False,
    )
    monkeypatch.setattr("osiris_api.paper_execution.check_execution", explode)
    transport = _Transport()
    with pytest.raises(ValueError, match="risk_service_unavailable"):
        _service(tmp_path, transport).submit(_order(), **FAVORABLE)
    assert transport.calls == []
    decision = _decisions()[-1]
    assert decision["approved"] is False
    assert decision["reason"] == "risk_service_unavailable"
    assert reconcile()["passed"] is True


def test_broker_rejects_when_the_risk_check_is_missing(tmp_path, monkeypatch):
    open_book(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "osiris_api.control_plane.broker.check_execution", None, raising=False,
    )
    monkeypatch.setattr("osiris_api.paper_execution.check_execution", None)
    transport = _Transport()
    with pytest.raises(ValueError, match="risk_service_unavailable"):
        _service(tmp_path, transport).submit(_order(), **FAVORABLE)
    assert transport.calls == []
    assert _decisions()[-1]["reason"] == "risk_service_unavailable"


def test_broker_rejects_when_the_risk_check_exceeds_its_budget(tmp_path, monkeypatch):
    open_book(monkeypatch, tmp_path)

    def approve(**kwargs):
        return True, "approved_for_simulation_only", True

    monkeypatch.setattr(
        "osiris_api.control_plane.broker.check_execution", approve, raising=False,
    )
    monkeypatch.setattr("osiris_api.paper_execution.check_execution", approve)
    ticks = {"n": 0}

    def clock():
        ticks["n"] += 1
        return 0.0 if ticks["n"] == 1 else 30.0

    monkeypatch.setattr("osiris_api.paper_execution._pretrade_monotonic", clock, raising=False)
    transport = _Transport()
    with pytest.raises(ValueError, match="risk_service_unavailable"):
        _service(tmp_path, transport).submit(_order(), **FAVORABLE)
    assert transport.calls == []
    decision = _decisions()[-1]
    assert decision["approved"] is False
    assert decision["inputs"]["timeout"] is True
    assert reconcile()["passed"] is True


def test_broker_exact_daily_loss_veto_ignores_caller_pnl(tmp_path, monkeypatch):
    limits = RiskLimits(
        max_order_notional=10_000, max_position_notional=50_000,
        max_daily_loss=10, max_gross_exposure=100_000,
    )
    open_book(monkeypatch, tmp_path, limits=limits, shares="2", price="100", mark_price="50")
    transport = _Transport()
    with pytest.raises(ValueError, match="daily_loss_limit_reached"):
        _service(tmp_path, transport).submit(_order(), **FAVORABLE)
    assert transport.calls == []
    decision = _decisions()[-1]
    assert decision["reason"] == "daily_loss_limit_reached"
    assert decision["inputs"]["daily_pnl"] == "0"
    assert decision["inputs"]["source"] == "paper_ledger"
    # The float kernel sees realized P&L only. The exact adverse veto is what rejects.
    approved, reason, _evaluated = check_execution(
        side="BUY", quantity=1, price=100,
        current_position_notional=float(decision["inputs"]["current_position_notional"]),
        current_gross_exposure=float(decision["inputs"]["current_gross_exposure"]),
        daily_pnl=float(decision["inputs"]["daily_pnl"]),
        limits=limits,
        kill_switch_active=False, market_data_fresh=True, risk_service_available=True,
    )
    assert approved is True
    assert reason == "approved_for_simulation_only"
    assert KERNEL_REASONS[decision["reason"]] == "daily_loss_limit"
    assert reconcile()["passed"] is True


def test_broker_gross_exposure_ignores_caller_gross(tmp_path, monkeypatch):
    limits = RiskLimits(
        max_order_notional=10_000, max_position_notional=50_000,
        max_daily_loss=1_000, max_gross_exposure=150,
    )
    open_book(monkeypatch, tmp_path, limits=limits, shares="1", price="100")
    transport = _Transport()
    with pytest.raises(ValueError, match="exposure_budget_exceeded"):
        _service(tmp_path, transport).submit(_order(), **FAVORABLE)
    assert transport.calls == []
    decision = _decisions()[-1]
    assert decision["reason"] == "exposure_budget_exceeded"
    assert decision["inputs"]["current_gross_exposure"] == "100"
    assert decision["inputs"]["max_gross_exposure"] == "150"
    assert KERNEL_REASONS[decision["reason"]] == "exposure_budget"
    assert reconcile()["passed"] is True


def test_broker_approval_journals_ledger_inputs_not_caller_floats(tmp_path, monkeypatch):
    open_book(monkeypatch, tmp_path, shares="1", price="100")
    transport = _Transport()
    response = _service(tmp_path, transport).submit(_order(), **FAVORABLE)
    assert response["status"] == "accepted"
    assert transport.calls and transport.calls[0][0] == "POST"
    decision = _decisions()[-1]
    assert decision["approved"] is True
    assert decision["reason"] == "approved_for_simulation_only"
    assert decision["limits_evaluated"] is True
    assert decision["inputs"]["source"] == "paper_ledger"
    assert decision["inputs"]["current_position_notional"] == "100"
    assert decision["inputs"]["current_gross_exposure"] == "100"
    assert decision["inputs"]["kill_switch_active"] is False
    assert decision["inputs"]["market_data_fresh"] is True
    assert decision["inputs"]["daily_pnl"] == "0"
    assert decision["live_execution"] is False
    assert decision["broker_order_submitted"] is False
    approved, reason, evaluated = check_execution(
        side=decision["inputs"]["side"],
        quantity=float(decision["inputs"]["quantity"]),
        price=float(decision["inputs"]["price"]),
        current_position_notional=float(decision["inputs"]["current_position_notional"]),
        current_gross_exposure=float(decision["inputs"]["current_gross_exposure"]),
        daily_pnl=float(decision["inputs"]["daily_pnl"]),
        limits=RiskLimits(
            max_order_notional=float(decision["inputs"]["max_order_notional"]),
            max_position_notional=float(decision["inputs"]["max_position_notional"]),
            max_daily_loss=float(decision["inputs"]["max_daily_loss"]),
            max_gross_exposure=float(decision["inputs"]["max_gross_exposure"]),
        ),
        kill_switch_active=decision["inputs"]["kill_switch_active"],
        market_data_fresh=decision["inputs"]["market_data_fresh"],
        risk_service_available=True,
    )
    assert approved is True
    assert KERNEL_REASONS[reason] == "approved"
    assert KERNEL_REASONS[decision["reason"]] == KERNEL_REASONS[reason]
    assert evaluated is True
    assert reconcile()["passed"] is True


def test_canonical_order_records_ledger_risk_inputs(tmp_path, monkeypatch):
    open_book(monkeypatch, tmp_path, marked=False)
    stamp = _stamp()
    result = submit_paper_order(
        client_order_id="canon-1", symbol="TEST", side="BUY",
        quantity="1", price="100", fee="0", observed_at=stamp, now=stamp,
    )
    assert result["status"] == "filled"
    decided = [
        json.loads(row["payload"])
        for row in snapshot()["audit"]
        if row["event"] == "order_decided" and row["client_order_id"] == "canon-1"
    ]
    assert decided[-1]["reason"] == "approved_for_simulation_only"
    inputs = decided[-1]["risk_inputs"]
    assert inputs["source"] == "paper_ledger"
    assert inputs["current_gross_exposure"] == "0"
    assert inputs["current_position_notional"] == "0"
    assert inputs["kill_switch_active"] is False
    assert inputs["market_data_fresh"] is True
    assert inputs["persisted_kill_switch"] is False
    assert reconcile()["passed"] is True


def test_authorize_paper_order_rejects_missing_or_breached_gross():
    base = PaperOrderContext(
        quantity=1, price=1, current_position_notional=0, daily_pnl=0,
        observation_time_ms=1000, decision_time_ms=1200,
        model_approved=True, feed_authenticated=True, feed_entitled=True,
        kill_switch_active=False, symbol="BTC", current_gross_exposure=0,
    )
    assert authorize_paper_order(base) == (True, "approved_for_simulation_only")
    missing = PaperOrderContext(
        quantity=1, price=1, current_position_notional=0, daily_pnl=0,
        observation_time_ms=1000, decision_time_ms=1200,
        model_approved=True, feed_authenticated=True, feed_entitled=True,
        kill_switch_active=False, symbol="BTC",
    )
    assert authorize_paper_order(missing) == (False, "exposure_input_missing")
    breached = PaperOrderContext(
        quantity=1, price=1, current_position_notional=0, daily_pnl=0,
        observation_time_ms=1000, decision_time_ms=1200,
        model_approved=True, feed_authenticated=True, feed_entitled=True,
        kill_switch_active=False, symbol="BTC", current_gross_exposure=100_000,
    )
    assert authorize_paper_order(breached) == (False, "exposure_budget_exceeded")
