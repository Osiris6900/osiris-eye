"""Public Coinbase candles -> momentum research -> independent risk -> paper ledger.

One fixed-size proposal per strategy/symbol/closed minute. Fills use the latest
closed candle's close as an explicit simulation assumption; no broker is called.
The HTTP route never accepts prices, signals, risk approval, limits or a clock.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from datetime import datetime as DateTimeType
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from . import paper_execution as execution
from .market_gateway import MarketDataGateway, MarketDataUnavailable, parse_market_time
from .paper_pipeline_status import record_pipeline_run
from .paper_workflow import evaluate_paper_workflow
from .public_market_data import CoinbasePublicMarketDataAdapter
from .risk import RiskLimits

router = APIRouter(prefix="/paper/pipeline", tags=["paper-pipeline"])


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _decimal(value: Any, *, positive: bool) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise ValueError("pipeline_decimal_string_required")
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("invalid_pipeline_number") from exc
    if not number.is_finite() or number < 0 or (positive and number == 0):
        raise ValueError("invalid_pipeline_number")
    # Match the execution engine's finite, exact float risk boundary.
    if Decimal(str(float(number))) != number:
        raise ValueError("invalid_pipeline_number")
    return number


def _decision(run_id: str, fingerprint: str, result: dict | None = None) -> dict | None:
    """Claim a stable candle decision before execution; retry after any crash.

    Order execution is independently idempotent, including the crash window
    between its commit and saving this receipt. Corrected inputs cannot reuse an ID.
    """
    path = os.environ["OSIRIS_PAPER_EXECUTION_DB"]
    with sqlite3.connect(path, timeout=15) as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("""CREATE TABLE IF NOT EXISTS paper_pipeline_decisions (
            run_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, result TEXT)""")
        prior = conn.execute(
            "SELECT fingerprint, result FROM paper_pipeline_decisions WHERE run_id=?", (run_id,)
        ).fetchone()
        if prior and prior[0] != fingerprint:
            raise ValueError("pipeline_decision_conflict")
        if prior and prior[1] is not None:
            return json.loads(prior[1])
        conn.execute(
            "INSERT OR IGNORE INTO paper_pipeline_decisions VALUES (?,?,NULL)",
            (run_id, fingerprint),
        )
        if result is not None:
            conn.execute(
                "UPDATE paper_pipeline_decisions SET result=? WHERE run_id=?",
                (_json(result), run_id),
            )
    return None


def _account_result(result: dict) -> dict:
    # Concurrent evaluations may see different control states. A saved proposal
    # receipt must never hide an order another worker committed for this candle.
    run_id = result["pipeline_run_id"]
    actual_order = execution.get_paper_order(run_id)
    order = {**result.get("order", {}), "client_order_id": run_id}
    if actual_order is not None:
        order = actual_order
        if result.get("replayed"):
            order = {**order, "original_status": order["status"], "status": "idempotent_replay"}
    state = execution.execution_status()
    if not state["reconciliation"]["passed"]:
        raise ValueError("paper_reconciliation_failed")
    return {**result, "order": order, "account": state["account"], "reconciliation": state["reconciliation"]}


def run_paper_pipeline(
    *, symbol: str = "BTC-USD", quantity: Any = "0.001", fee: Any = "0",
    lookback: int = 3, threshold_bps: Any = "0", strategy_id: str = "momentum-v1",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Execute a bounded, public-data paper cycle against a configured account.

    ``now`` is a library-only deterministic test clock. HTTP always uses UTC wall
    time. Strategy is diagnostic momentum, not a validated profitable model.
    Account cash and all supported symbols use USD. Shorts are refused by the OMS.
    """
    if os.getenv("OSIRIS_ENABLE_LIVE_EXECUTION"):
        raise ValueError("live_execution_disabled")
    if not isinstance(symbol, str) or not re.fullmatch(r"[A-Z0-9]{2,15}-USD", symbol):
        raise ValueError("pipeline_requires_coinbase_usd_symbol")
    if not isinstance(strategy_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", strategy_id):
        raise ValueError("invalid_strategy_id")
    if isinstance(lookback, bool) or not isinstance(lookback, int) or not 1 <= lookback <= 60:
        raise ValueError("invalid_strategy_lookback")
    qty, cost = _decimal(quantity, positive=True), _decimal(fee, positive=False)
    threshold = _decimal(threshold_bps, positive=False)
    if threshold > 10000:
        raise ValueError("invalid_strategy_threshold")
    requested = now or datetime.now(timezone.utc)
    if not isinstance(requested, DateTimeType) or requested.tzinfo is None or requested.utcoffset() is None:
        raise ValueError("pipeline_clock_requires_timezone")
    end = requested.astimezone(timezone.utc).replace(second=0, microsecond=0)
    # Coinbase can publish a just-closed candle late. Select the latest complete
    # contiguous window; persisted freshness limits still reject old data.
    start = end - timedelta(minutes=lookback + 4)
    gateway = MarketDataGateway(
        adapters={"coinbase": CoinbasePublicMarketDataAdapter()},
        entitlement_references={"coinbase": "public-data-no-broker-entitlement"},
        supported_asset_classes={"coinbase": ["crypto"]},
    )
    response = gateway.fetch_bars("coinbase", symbol, start.isoformat(), end.isoformat(), "crypto")
    decision_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    bars = response["bars"]
    if len(bars) < lookback + 1:
        raise ValueError("pipeline_incomplete_candle_history")
    bars = bars[-(lookback + 1):]
    window_start = parse_market_time(bars[0]["event_time"])
    if window_start.second != 0 or window_start.microsecond != 0:
        raise ValueError("pipeline_candle_gap_or_misalignment")
    for index, bar in enumerate(bars):
        opened = parse_market_time(bar["event_time"])
        if opened != window_start + timedelta(minutes=index):
            raise ValueError("pipeline_candle_gap_or_misalignment")
        if opened + timedelta(minutes=1) > decision_time:
            raise ValueError("pipeline_candle_not_closed")
        if parse_market_time(bar["available_at"]) > decision_time:
            raise ValueError("pipeline_point_in_time_violation")
    observed = parse_market_time(bars[-1]["event_time"]) + timedelta(minutes=1)
    price = str(bars[-1]["close"])
    closes = [Decimal(str(bar["close"])) for bar in bars]
    returns = [0.0] + [float(closes[i] / closes[i - 1] - 1) for i in range(1, len(closes))]
    momentum = (closes[-1] / closes[0] - 1) * 10000
    signal = 1.0 if momentum > threshold else -1.0 if momentum < -threshold else 0.0
    signals = [0.0] * lookback + [signal]
    side = "BUY" if signal > 0 else "SELL" if signal < 0 else "HOLD"
    context = execution.execution_context(symbol=symbol, price=price, side=side if side != "HOLD" else "BUY")
    age = (decision_time - observed).total_seconds()
    if not 0 <= age <= float(context["max_market_age_seconds"]):
        raise ValueError("stale_market_data")
    # Availability changes on refetch. The actual normalized candle data does not.
    candles = [{key: value for key, value in bar.items() if key != "available_at"} for bar in bars]
    bars_hash = _digest(candles)
    run_id = "pipeline-" + _digest({
        "strategy_id": strategy_id, "symbol": symbol, "candle_end": observed.isoformat(),
    })
    fingerprint = _digest({
        "bars_sha256": bars_hash, "lookback": lookback,
        "threshold_bps": str(threshold.normalize()), "quantity": str(qty.normalize()),
        "fee": str(cost.normalize()),
    })
    prior = _decision(run_id, fingerprint)
    if prior is not None:
        # Reconciliation checks the live ledger rather than trusting a saved receipt.
        result = _account_result({**prior, "replayed": True})
        record_pipeline_run(result)
        return result
    workflow = evaluate_paper_workflow(
        symbol=symbol, signals=signals, asset_returns=returns,
        quantity=float(qty), price=float(closes[-1]),
        current_position_notional=float(context["current_position_notional"]),
        current_gross_exposure=float(context["current_gross_exposure"]),
        daily_pnl=float(context["daily_pnl"]), limits=RiskLimits(**context["limits"]),
        kill_switch_active=context["kill_switch_active"],
        market_data_fresh=True, risk_service_available=True,
    )
    existing = execution.get_paper_order(run_id)
    if existing is not None:
        # An earlier process may have committed the order but not its pipeline receipt.
        expected = {"symbol": symbol, "side": side, "quantity": qty, "price": Decimal(price), "fee": cost}
        for field, value in expected.items():
            actual = existing[field]
            mismatch = Decimal(actual) != value if isinstance(value, Decimal) else actual != value
            if mismatch:
                raise ValueError("pipeline_decision_conflict")
        order = {**existing, "original_status": existing["status"], "status": "idempotent_replay"}
    else:
        order = execution.submit_approved_workflow(
            workflow, client_order_id=run_id, quantity=str(qty), price=price, fee=str(cost),
            observed_at=observed.isoformat(), now=decision_time.isoformat(),
        )
    execution.mark_to_market(
        {symbol: price}, observed_at=observed.isoformat(), now=decision_time.isoformat(),
    )
    result = _account_result({
        "pipeline_run_id": run_id, "schema": "osiris.paper_pipeline.v1",
        "symbol": symbol, "provider": "coinbase", "decision_time": decision_time.isoformat(),
        "market": {
            "event_time": bars[-1]["event_time"], "observed_at": observed.isoformat(),
            "price": price, "bars_sha256": bars_hash, "bars": candles,
            "max_age_seconds": context["max_market_age_seconds"],
            "source_mode": "public", "fill_assumption": "closed_candle_close_no_slippage",
        },
        "strategy": {"id": strategy_id, "type": "momentum", "lookback": lookback,
                     "threshold_bps": str(threshold), "momentum_bps": str(momentum), "side": side},
        "workflow": workflow, "order": order, "replayed": existing is not None,
        "live_execution": False, "broker_order_submitted": False, "mode": "paper_only",
    })
    saved = _decision(run_id, fingerprint, result)
    if saved is not None:
        result = _account_result({**saved, "replayed": True})
    result = _account_result(result)
    record_pipeline_run(result)
    return result


class PipelineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    symbol: str = "BTC-USD"
    quantity: str = "0.001"
    fee: str = "0"
    strategy_id: str = "momentum-v1"
    lookback: int = Field(default=3, ge=1, le=60)
    threshold_bps: str = "0"


@router.post("/run")
def api_run_pipeline(request: PipelineRequest):
    try:
        return run_paper_pipeline(**request.model_dump())
    except MarketDataUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
