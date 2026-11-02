from fastapi import FastAPI,HTTPException
from pydantic import BaseModel,Field
from .research import relative_iv_zscore,estimated_one_way_cost,expiry_gamma_feature,implied_minus_forecast_vol,lagged_correlation,market_regime
from .risk import check_order
app=FastAPI(title="OSIRIS EYE Quant API",version="0.1.0",description="Research diagnostics only; no live execution.")
class OrderCheck(BaseModel):
    quantity:float=Field(gt=0)
    price:float=Field(gt=0)
    current_position_notional:float=Field(ge=0,default=0)
    daily_pnl:float=0
@app.get("/health")
def health(): return {"status":"ok","mode":"research_only","live_execution":False}
def guarded(fn,*args):
    try:return fn(*args)
    except ValueError as exc: raise HTTPException(422,str(exc)) from exc
@app.post("/research/options-iv")
def options_iv(target_iv:float,peer_ivs:list[float]): return {"iv_zscore":guarded(relative_iv_zscore,target_iv,peer_ivs)}
@app.post("/research/liquidity")
def liquidity(half_spread:float,slippage:float): return {"estimated_one_way_cost":guarded(estimated_one_way_cost,half_spread,slippage)}
@app.post("/research/expiry-gamma")
def expiry_gamma(gamma:float,days_to_expiry:float): return {"expiry_gamma_feature":guarded(expiry_gamma_feature,gamma,days_to_expiry)}
@app.post("/research/volatility-spread")
def vol_spread(implied_vol:float,forecast_realized_vol:float): return {"implied_minus_forecast_vol":guarded(implied_minus_forecast_vol,implied_vol,forecast_realized_vol)}
@app.post("/research/lead-lag")
def lead_lag(x:list[float],y:list[float],lag:int=0): return {"lagged_correlation":guarded(lagged_correlation,x,y,lag)}
@app.post("/research/regime")
def regime(realized_vol:float,spread_bps:float): return guarded(market_regime,realized_vol,spread_bps)
@app.post("/risk/check")
def risk_check(order:OrderCheck):
    ok,reason=check_order(**order.model_dump())
    return {"allowed":ok,"reason":reason,"execution":"disabled"}