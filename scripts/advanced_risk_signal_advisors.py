"""Paper-only stress scenario and signal conflict advisory foundations.

No order execution, broker connections, automatic approvals or predictions.
"""
from __future__ import annotations
import math


def finite_number(value: object) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def stress_portfolio(positions: object, shocks_bps: object, max_loss: object) -> dict:
    if (not isinstance(positions, list) or not 1 <= len(positions) <= 100
            or not isinstance(shocks_bps, dict)
            or not finite_number(max_loss) or max_loss <= 0):
        return {"status": "rejected", "reason": "invalid_stress_inputs"}
    seen = set()
    losses = []
    for p in positions:
        if not isinstance(p, dict) or set(p) != {"instrument", "signed_notional"}:
            return {"status": "rejected", "reason": "invalid_position"}
        symbol = p["instrument"]
        notional = p["signed_notional"]
        if (not isinstance(symbol, str) or not symbol or symbol in seen
                or not finite_number(notional)):
            return {"status": "rejected", "reason": "invalid_position_value"}
        seen.add(symbol)
        shock = shocks_bps.get(symbol)
        if not finite_number(shock) or abs(shock) > 10000:
            return {"status": "rejected", "reason": "missing_or_invalid_shock"}
        # Scale the bounded shock first: notional * shock can overflow even
        # when the final P&L is representable.
        losses.append(-notional * (shock / 10000))
    if set(shocks_bps) != seen:
        return {"status": "rejected", "reason": "shock_instrument_mismatch"}
    try:
        loss = math.fsum(losses)
    except (OverflowError, ValueError):
        return {"status": "rejected", "reason": "nonfinite_scenario_loss"}
    if not math.isfinite(loss):
        return {"status": "rejected", "reason": "nonfinite_scenario_loss"}
    return {"status": "stress_advisory", "scenario_loss": round(loss, 6),
            "risk_budget_breached": loss > max_loss,
            "model_validated": False, "paper_only": True,
            "live_trading_authorized": False}


def resolve_signals(signals: object) -> dict:
    if not isinstance(signals, list) or not 1 <= len(signals) <= 100:
        return {"status": "rejected", "reason": "invalid_signal_count"}
    groups = {}
    seen = set()
    for signal in signals:
        if not isinstance(signal, dict) or set(signal) != {"id", "instrument", "direction"}:
            return {"status": "rejected", "reason": "invalid_signal"}
        sid, symbol, direction = (signal[k] for k in ("id", "instrument", "direction"))
        if (not isinstance(sid, str) or not sid or sid in seen
                or not isinstance(symbol, str) or not symbol
                or direction not in ("buy", "sell", "hold")):
            return {"status": "rejected", "reason": "invalid_signal_value"}
        seen.add(sid)
        groups.setdefault(symbol, set()).add(direction)
    results = {}
    for symbol, directions in sorted(groups.items()):
        actionable = directions & {"buy", "sell"}
        results[symbol] = {"decision": "conflict_blocked" if len(actionable) > 1 else
                           "hold" if not actionable else next(iter(actionable)),
                           "has_conflict": len(actionable) > 1}
    return {"status": "advisory_only", "instruments": results,
            "orders_placed": 0, "live_trading_authorized": False}
