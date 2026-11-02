"""Fail-closed simulation risk checks; no broker integration."""
from dataclasses import dataclass
@dataclass(frozen=True)
class RiskLimits:
    max_order_notional: float=10000.0
    max_position_notional: float=50000.0
    max_daily_loss: float=1000.0

def check_order(*,quantity:float,price:float,current_position_notional:float,daily_pnl:float,limits:RiskLimits=RiskLimits())->tuple[bool,str]:
    vals=(quantity,price,current_position_notional,daily_pnl,limits.max_order_notional,limits.max_position_notional,limits.max_daily_loss)
    if any(not isinstance(v,(int,float)) or v!=v or abs(v)==float("inf") for v in vals): return False,"invalid_numeric_input"
    if quantity<=0 or price<=0 or current_position_notional<0: return False,"invalid_order_or_position"
    if limits.max_order_notional<=0 or limits.max_position_notional<=0 or limits.max_daily_loss<0: return False,"invalid_risk_limits"
    notional=quantity*price
    if notional>limits.max_order_notional: return False,"max_order_notional_exceeded"
    if current_position_notional+notional>limits.max_position_notional: return False,"max_position_notional_exceeded"
    if daily_pnl<=-limits.max_daily_loss: return False,"daily_loss_limit_reached"
    return True,"approved_for_simulation_only"