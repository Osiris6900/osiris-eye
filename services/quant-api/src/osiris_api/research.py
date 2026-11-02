"""Deterministic research diagnostics; not trading recommendations."""
from math import isfinite, sqrt
from statistics import mean, pstdev
from typing import Sequence

def _finite(values: Sequence[float], name: str) -> list[float]:
    result = [float(x) for x in values]
    if not result or not all(isfinite(x) for x in result): raise ValueError(f"{name} must contain finite values")
    return result

def relative_iv_zscore(target_iv: float, peer_ivs: Sequence[float]) -> float:
    peers = _finite(peer_ivs, "peer_ivs")
    if not isfinite(target_iv) or target_iv < 0 or any(x < 0 for x in peers): raise ValueError("IVs must be finite and non-negative")
    sd = pstdev(peers)
    return 0.0 if sd == 0 else (target_iv - mean(peers)) / sd

def estimated_one_way_cost(half_spread: float, slippage: float) -> float:
    if not all(isfinite(x) and x >= 0 for x in (half_spread, slippage)): raise ValueError("costs must be finite and non-negative")
    return half_spread + slippage

def expiry_gamma_feature(gamma: float, days_to_expiry: float) -> float:
    if not isfinite(gamma) or not isfinite(days_to_expiry) or days_to_expiry < 0: raise ValueError("invalid gamma or expiry")
    return abs(gamma) / sqrt(max(days_to_expiry, 1.0))

def implied_minus_forecast_vol(implied_vol: float, forecast_realized_vol: float) -> float:
    if not all(isfinite(x) and x >= 0 for x in (implied_vol, forecast_realized_vol)): raise ValueError("volatilities must be finite and non-negative")
    return implied_vol - forecast_realized_vol

def lagged_correlation(x: Sequence[float], y: Sequence[float], lag: int = 0) -> float:
    a,b=_finite(x,"x"),_finite(y,"y")
    if len(a)!=len(b) or not isinstance(lag,int) or abs(lag)>=len(a)-1: raise ValueError("series lengths or lag invalid")
    if lag>0: a,b=a[:-lag],b[lag:]
    elif lag<0: a,b=a[-lag:],b[:lag]
    ma,mb=mean(a),mean(b); da=[v-ma for v in a]; db=[v-mb for v in b]
    denom=sqrt(sum(v*v for v in da)*sum(v*v for v in db))
    return 0.0 if denom==0 else sum(u*v for u,v in zip(da,db))/denom

def market_regime(realized_vol: float, spread_bps: float, vol_limit: float=0.3, spread_limit: float=15.0) -> dict[str,object]:
    vals=(realized_vol,spread_bps,vol_limit,spread_limit)
    if not all(isfinite(v) and v>=0 for v in vals): raise ValueError("regime values must be finite and non-negative")
    restricted=realized_vol>vol_limit or spread_bps>spread_limit
    return {"regime":"risk_restricted" if restricted else "normal","risk_restricted":restricted}