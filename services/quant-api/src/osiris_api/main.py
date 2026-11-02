from typing import Any

from fastapi import Body, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from .data_store import ResearchStore
from .module_registry import MODULES
from .platform_modules import (
    AgentTask, AlternativeEvent, HealthCheck, system_health,
    estimated_execution_cost_bps,
    historical_var_es,
    inverse_volatility_weights,
    macro_regime,
    order_flow_imbalance,
    population_stability_index,
    strategy_summary,
)
from .research import (
    relative_iv_zscore,
    estimated_one_way_cost,
    expiry_gamma_feature,
    implied_minus_forecast_vol,
    lagged_correlation,
    market_regime,
)
from .risk import check_order

app = FastAPI(
    title="OSIRIS EYE Quant API",
    version="0.2.0",
    description="Shared research API and point-in-time data store; no live execution.",
)
store = ResearchStore()


class OrderCheck(BaseModel):
    quantity: float = Field(gt=0)
    price: float = Field(gt=0)
    current_position_notional: float = Field(ge=0, default=0)
    daily_pnl: float = 0


class ObservationIn(BaseModel):
    dataset: str = Field(min_length=1, max_length=100)
    record_key: str = Field(min_length=1, max_length=200)
    source: str = Field(min_length=1, max_length=200)
    event_time: str
    available_at: str
    payload: dict[str, Any]


class WeightsIn(BaseModel):
    volatilities: list[float] = Field(min_length=1, max_length=500)


class ReturnsIn(BaseModel):
    returns: list[float] = Field(min_length=1, max_length=100000)
    periods_per_year: int = Field(default=252, ge=1, le=100000)


class HistoricalRiskIn(BaseModel):
    returns: list[float] = Field(min_length=1, max_length=100000)
    confidence: float = Field(default=0.95, gt=0, lt=1)


class DriftIn(BaseModel):
    reference: list[float] = Field(min_length=2, max_length=100000)
    current: list[float] = Field(min_length=1, max_length=100000)
    bins: int = Field(default=10, ge=2, le=100)


class MacroRegimeIn(BaseModel):
    realized_volatility: float
    inflation_change: float
    rates_change: float
    vol_threshold: float = Field(default=0.30, gt=0)


class FlowIn(BaseModel):
    bid_volume: float = Field(ge=0)
    ask_volume: float = Field(ge=0)


class AlternativeEventIn(BaseModel):
    source: str
    event_time: str
    published_at: str
    category: str
    sentiment: float


class HealthCheckIn(BaseModel):
    component: str
    healthy: bool
    detail: str = ""


class AgentTaskIn(BaseModel):
    role: str
    objective: str
    requires_human_approval: bool = True


class ExecutionCostIn(BaseModel):
    half_spread_bps: float = Field(ge=0)
    slippage_bps: float = Field(ge=0)
    impact_bps: float = Field(default=0, ge=0)


def guarded(fn, *args):
    try:
        return fn(*args)
    except (ValueError, TypeError, OverflowError) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/health")
def health():
    return {"status": "ok", "mode": "research_only", "live_execution": False,
            "data_store": "sqlite", "module_count": len(MODULES)}


@app.get("/platform/modules")
def platform_modules():
    return {"count": len(MODULES), "modules": MODULES,
            "execution_enabled": False}


@app.post("/data/observations")
def put_observation(observation: ObservationIn):
    return guarded(store.put, **observation.model_dump())


@app.get("/data/observations")
def list_observations(dataset: str = Query(..., min_length=1, max_length=100),
                      limit: int = Query(100, ge=1, le=1000)):
    return {"dataset": dataset, "records": guarded(store.list, dataset, limit)}


@app.post("/research/alternative-data/validate-event")
def validate_alternative_event(request: AlternativeEventIn):
    event = guarded(AlternativeEvent(**request.model_dump()).validate)
    return {"valid": True, "event": event.__dict__}


@app.post("/platform/reliability/health")
def aggregate_health(checks: list[HealthCheckIn] = Body(..., min_length=1, max_length=100)):
    return guarded(system_health, [HealthCheck(**item.model_dump()) for item in checks])


@app.post("/platform/agents/validate-task")
def validate_agent_task(request: AgentTaskIn):
    task = guarded(AgentTask(**request.model_dump()).validate)
    return {"valid": True, "role": task.role, "human_approval_required": True}


@app.post("/research/portfolio/weights")
def portfolio_weights(request: WeightsIn):
    return {"weights": guarded(inverse_volatility_weights, request.volatilities),
            "method": "inverse_volatility_baseline"}


@app.post("/research/risk/historical")
def historical_risk(request: HistoricalRiskIn):
    var, es = guarded(historical_var_es, request.returns, request.confidence)
    return {"historical_var": var, "expected_shortfall": es,
            "confidence": request.confidence, "units": "return_fraction"}


@app.post("/research/models/drift")
def model_drift(request: DriftIn):
    return {"psi": guarded(population_stability_index, request.reference,
                           request.current, request.bins)}


@app.post("/research/strategy/summary")
def strategy_performance(request: ReturnsIn):
    return guarded(strategy_summary, request.returns, request.periods_per_year)


@app.post("/research/execution/cost")
def execution_cost(request: ExecutionCostIn):
    return {"estimated_cost_bps": guarded(estimated_execution_cost_bps,
            request.half_spread_bps, request.slippage_bps, request.impact_bps),
            "execution": "disabled"}


@app.post("/research/microstructure/imbalance")
def microstructure_imbalance(request: FlowIn):
    return {"normalized_imbalance": guarded(order_flow_imbalance,
            request.bid_volume, request.ask_volume)}


@app.post("/research/macro/regime")
def macro_regime_label(request: MacroRegimeIn):
    return {"regime": guarded(macro_regime, request.realized_volatility,
            request.inflation_change, request.rates_change, request.vol_threshold)}


@app.post("/research/options-iv")
def options_iv(target_iv: float, peer_ivs: list[float] = Query(...)):
    return {"iv_zscore": guarded(relative_iv_zscore, target_iv, peer_ivs)}


@app.post("/research/liquidity")
def liquidity(half_spread: float, slippage: float):
    return {"estimated_one_way_cost": guarded(estimated_one_way_cost, half_spread, slippage)}


@app.post("/research/expiry-gamma")
def expiry_gamma(gamma: float, days_to_expiry: float):
    return {"expiry_gamma_feature": guarded(expiry_gamma_feature, gamma, days_to_expiry)}


@app.post("/research/volatility-spread")
def vol_spread(implied_vol: float, forecast_realized_vol: float):
    return {"implied_minus_forecast_vol": guarded(implied_minus_forecast_vol, implied_vol, forecast_realized_vol)}


@app.post("/research/lead-lag")
def lead_lag(x: list[float], y: list[float], lag: int = 0):
    return {"lagged_correlation": guarded(lagged_correlation, x, y, lag)}


@app.post("/research/regime")
def regime(realized_vol: float, spread_bps: float):
    return guarded(market_regime, realized_vol, spread_bps)


@app.post("/risk/check")
def risk_check(order: OrderCheck):
    ok, reason = check_order(**order.model_dump())
    return {"allowed": ok, "reason": reason, "execution": "disabled"}
