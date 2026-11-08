"""Deterministic out-of-sample return gate including transaction costs."""
from decimal import Decimal, InvalidOperation

def net_returns(trades, fee_bps, slippage_bps):
    try:
        fee, slip = Decimal(str(fee_bps)), Decimal(str(slippage_bps))
        if not fee.is_finite() or not slip.is_finite() or min(fee, slip) < 0:
            raise ValueError("invalid cost assumptions")
        results = []
        for trade in trades:
            gross = Decimal(str(trade["gross_return"]))
            turnover = Decimal(str(trade["turnover"]))
            if not gross.is_finite() or not turnover.is_finite() or turnover < 0:
                raise ValueError("invalid trade")
            results.append(gross - turnover * (fee + slip) / Decimal("10000"))
        return results
    except (KeyError, TypeError, InvalidOperation) as exc:
        raise ValueError("invalid backtest inputs") from exc
