from typing import Any

from fastapi import Body, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from .data_store import ResearchStore
from .data_intelligence import audit_observations
from .backtesting import simulate_signals
from .institutional_modules import portfolio_risk, options_surface_audit, cross_market_correlation, alternative_data_intake, agent_research_proposal, command_center_snapshot
from .advanced_systems import order_book, execution_sim, model_governance, knowledge_graph, strategy_discovery, regime_change, monte_carlo, news_audit, security_audit, attribution, compute_plan, incident_assessment
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


class DataAuditIn(BaseModel):
    records: list[dict[str, Any]] = Field(min_length=1, max_length=10000)
    as_of: str
    max_age_seconds: float = Field(default=300.0, ge=0)


class BacktestIn(BaseModel):
    signals: list[float] = Field(min_length=2, max_length=100000)
    asset_returns: list[float] = Field(min_length=2, max_length=100000)
    cost_bps_per_unit_turnover: float = Field(default=0.0, ge=0)


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


def guarded(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
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


@app.post("/data/audit")
def audit_data(request: DataAuditIn):
    return guarded(audit_observations, request.records, as_of=request.as_of,
                   max_age_seconds=request.max_age_seconds)


@app.post("/research/backtest/simulate")
def backtest_simulate(request: BacktestIn):
    return guarded(simulate_signals, request.signals, request.asset_returns,
                   cost_bps_per_unit_turnover=request.cost_bps_per_unit_turnover)



@app.get("/platform/command-center")
def command_center():
    return command_center_snapshot(MODULES)


@app.post("/platform/agents/propose-research")
def propose_research(payload: dict[str, Any] = Body(...)):
    return guarded(agent_research_proposal, payload.get("objective", ""), payload.get("roles", []))


@app.post("/research/portfolio/risk-diagnostics")
def portfolio_risk_diagnostics(payload: dict[str, list[float]] = Body(...)):
    return guarded(portfolio_risk, payload)


@app.post("/research/options/surface-audit")
def options_surface_audit_endpoint(points: list[dict[str, Any]] = Body(...)):
    return guarded(options_surface_audit, points)


@app.post("/research/global/cross-market-correlation")
def global_cross_market_correlation(series: dict[str, list[float]] = Body(...)):
    return guarded(cross_market_correlation, series)


@app.post("/research/alternative-data/hypothesis-intake")
def alternative_hypothesis_intake(payload: dict[str, Any] = Body(...)):
    return guarded(alternative_data_intake, payload.get("events", []), payload.get("outcome_returns", []))


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


@app.post("/research/microstructure/order-book")
def api_order_book(p: dict[str, Any] = Body(...)):
    return guarded(order_book, p.get("bids", []), p.get("asks", []))

@app.post("/research/execution/simulate")
def api_execution_sim(p: dict[str, Any] = Body(...)):
    return guarded(execution_sim, p.get("quantity", 0), p.get("arrival_price", 0), p.get("fills", []), p.get("fee_bps", 0))

@app.post("/research/models/governance")
def api_model_governance(p: dict[str, Any] = Body(...)):
    return guarded(model_governance, p.get("reference", []), p.get("current", []), p.get("threshold", 1))

@app.post("/research/knowledge-graph/audit")
def api_knowledge_graph(p: dict[str, Any] = Body(...)):
    return guarded(knowledge_graph, p.get("nodes", []), p.get("edges", []))

@app.post("/research/strategies/discover")
def api_strategy_discovery(p: dict[str, Any] = Body(...)):
    return guarded(strategy_discovery, p.get("signals", []), p.get("returns", []))

@app.post("/research/regime/change-point")
def api_regime_change(p: dict[str, Any] = Body(...)):
    return guarded(regime_change, p.get("values", []), p.get("window", 20), p.get("threshold", 2))

@app.post("/research/scenarios/monte-carlo")
def api_monte_carlo(p: dict[str, Any] = Body(...)):
    return guarded(monte_carlo, p.get("mean_return", 0), p.get("volatility", 0), p.get("periods", 20), p.get("paths", 1000), p.get("seed", 7))

@app.post("/research/news/audit")
def api_news_audit(p: dict[str, Any] = Body(...)):
    return guarded(news_audit, p.get("items", []))

@app.post("/platform/security/policy-audit")
def api_security_audit(p: dict[str, Any] = Body(...)):
    return guarded(security_audit, p.get("permissions", []))

@app.post("/research/portfolio/attribution")
def api_attribution(p: dict[str, Any] = Body(...)):
    return guarded(attribution, p.get("asset_returns", {}), p.get("weights", {}))

@app.post("/platform/compute/plan")
def api_compute_plan(p: dict[str, Any] = Body(...)):
    return guarded(compute_plan, p.get("tasks", 0), p.get("workers", 1))

@app.post("/platform/incidents/assess")
def api_incident_assessment(p: dict[str, Any] = Body(...)):
    return guarded(incident_assessment, p.get("checks", []))
