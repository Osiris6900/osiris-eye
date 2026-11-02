from typing import Any

from fastapi import Body, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from .data_store import ResearchStore
from .data_intelligence import audit_observations
from .backtesting import simulate_signals
from .phase5_backtesting import run_portfolio_backtest

from .phase10_systems import (monitor_portfolio_risk, audit_vendor_failover, score_alpha_hypotheses, allocate_risk_budget, audit_agent_governance, analyze_execution_quality, simulate_systemic_stress, evaluate_strategy_approval)

from .phase9_systems import audit_global_instruments, analyze_counterparty_risk, process_corporate_actions, estimate_portfolio_capacity, aggregate_derivatives_exposure, audit_financial_graph, simulate_disaster_recovery, benchmark_performance
from .phase8_systems import audit_data_lineage, audit_backtest_integrity, portfolio_risk_governor, catalyst_impact_scenarios, observe_model_health, reconcile_paper_trades, audit_security_resilience, orchestrate_research_workflow
from .phase7_market_intelligence import (cross_asset_alpha, options_flow_intelligence, liquidity_forecast, detect_market_anomalies, offline_rl_evaluation, trade_surveillance)
from .phase6_intelligence import (walk_forward_validation, portfolio_risk_diagnostics as phase6_portfolio_risk_diagnostics, execution_intelligence, model_validation, macro_intelligence, rank_strategies)
from .institutional_modules import portfolio_risk, options_surface_audit, cross_market_correlation, alternative_data_intake, agent_research_proposal, command_center_snapshot
from .advanced_systems import order_book, execution_sim, model_governance, knowledge_graph, strategy_discovery, regime_change, monte_carlo, news_audit, security_audit, attribution, compute_plan, incident_assessment
from .phase3_systems import connector_contract, market_replay, portfolio_optimizer, paper_order_lifecycle, factor_diagnostics, derivatives_risk, event_calendar_audit, experiment_manifest, portfolio_stress, sre_health, rbac_audit, workflow_gate
from .phase4_systems import audit_market_batch, normalize_market_record, audit_instrument_master, feed_health_snapshot
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




@app.post("/research/validation/walk-forward")
def phase6_walk_forward(p: dict[str, Any] = Body(...)):
    return guarded(walk_forward_validation, p.get("returns", []), p.get("train_size", 0),
                   p.get("test_size", 0), p.get("step"))


@app.post("/research/portfolio/advanced-risk")
def phase6_portfolio_risk(p: dict[str, Any] = Body(...)):
    return guarded(phase6_portfolio_risk_diagnostics, p.get("returns", {}), p.get("weights", {}),
                   p.get("periods_per_year", 252))


@app.post("/research/execution/intelligence")
def phase6_execution_intelligence(p: dict[str, Any] = Body(...)):
    return guarded(execution_intelligence, p.get("quantity", 0), p.get("adv", 0),
                   p.get("half_spread_bps", 0), p.get("volatility", 0),
                   p.get("impact_coefficient", 1.0), p.get("max_participation", 0.1))


@app.post("/research/models/advanced-validation")
def phase6_model_validation(p: dict[str, Any] = Body(...)):
    return guarded(model_validation, p.get("reference", []), p.get("current", []),
                   p.get("reference_accuracy", 0), p.get("current_accuracy", 0),
                   p.get("psi_threshold", 0.2), p.get("max_accuracy_drop", 0.05))


@app.post("/research/macro/intelligence")
def phase6_macro_intelligence(p: dict[str, Any] = Body(...)):
    return guarded(macro_intelligence, p.get("inflation_change", 0), p.get("rates_change", 0),
                   p.get("growth_change", 0), p.get("volatility_change", 0))


@app.post("/research/strategies/rank")
def phase6_strategy_ranking(p: dict[str, Any] = Body(...)):
    return guarded(rank_strategies, p.get("candidates", []), p.get("multiple_testing_penalty", 0.01))


@app.post("/research/alpha/cross-asset-discovery")
def phase7_cross_asset_alpha(p: dict[str, Any] = Body(...)):
    return guarded(cross_asset_alpha, p.get("returns", {}), p.get("max_lag", 3))


@app.post("/research/options/flow-intelligence")
def phase7_options_flow(p: dict[str, Any] = Body(...)):
    return guarded(options_flow_intelligence, p.get("trades", []))


@app.post("/research/liquidity/forecast")
def phase7_liquidity_forecast(p: dict[str, Any] = Body(...)):
    return guarded(liquidity_forecast, p.get("spread_bps", []), p.get("volume", []),
                   p.get("volatility", []), p.get("horizon", 1))


@app.post("/research/market/anomalies")
def phase7_market_anomalies(p: dict[str, Any] = Body(...)):
    return guarded(detect_market_anomalies, p.get("returns", []), p.get("volumes", []),
                   p.get("spread_bps", []), p.get("z_threshold", 4.0))


@app.post("/research/rl/offline-evaluation")
def phase7_offline_rl(p: dict[str, Any] = Body(...)):
    return guarded(offline_rl_evaluation, p.get("episodes", []), p.get("max_importance_weight", 10.0))


@app.post("/surveillance/trades/analyze")
def phase7_trade_surveillance(p: dict[str, Any] = Body(...)):
    return guarded(trade_surveillance, p.get("events", []),
                   p.get("cancel_ratio_threshold", 0.8), p.get("burst_window_seconds", 1.0))

@app.post("/platform/data/lineage-audit")
def phase8_lineage(p: dict[str, Any] = Body(...)):
    return guarded(audit_data_lineage, p.get("records", []))

@app.post("/research/backtest/integrity-audit")
def phase8_backtest_integrity(p: dict[str, Any] = Body(...)):
    return guarded(audit_backtest_integrity, p.get("evidence", {}))

@app.post("/risk/governor/evaluate")
def phase8_risk_governor(p: dict[str, Any] = Body(...)):
    return guarded(portfolio_risk_governor, p.get("positions", []), p.get("max_gross_exposure", 0), p.get("max_single_name_weight", 0), p.get("current_drawdown", 0), p.get("max_drawdown", 0))

@app.post("/research/catalysts/impact-scenarios")
def phase8_catalyst_scenarios(p: dict[str, Any] = Body(...)):
    return guarded(catalyst_impact_scenarios, p.get("weights", {}), p.get("scenarios", {}))

@app.post("/research/models/observability")
def phase8_model_observability(p: dict[str, Any] = Body(...)):
    return guarded(observe_model_health, p.get("reference", []), p.get("current", []), p.get("max_mean_shift", 0.5), p.get("max_std_ratio", 2.0))

@app.post("/paper/trading/reconcile")
def phase8_paper_reconciliation(p: dict[str, Any] = Body(...)):
    return guarded(reconcile_paper_trades, p.get("orders", []), p.get("fills", []), p.get("positions", []))

@app.post("/platform/security/resilience-audit")
def phase8_security_resilience(p: dict[str, Any] = Body(...)):
    return guarded(audit_security_resilience, p.get("controls", {}))

@app.post("/platform/workflow/research-orchestrate")
def phase8_research_orchestrator(p: dict[str, Any] = Body(...)):
    return guarded(orchestrate_research_workflow, p.get("gates", {}))


@app.post("/platform/instruments/global-coverage")
def phase9_global_coverage(p: dict[str, Any] = Body(...)):
    return guarded(audit_global_instruments, p.get("instruments", []))

@app.post("/risk/counterparties/concentration")
def phase9_counterparty_risk(p: dict[str, Any] = Body(...)):
    return guarded(analyze_counterparty_risk, p.get("exposures", []), p.get("nav", 0), p.get("max_counterparty_weight", 0.25))

@app.post("/portfolio/corporate-actions/audit")
def phase9_corporate_actions(p: dict[str, Any] = Body(...)):
    return guarded(process_corporate_actions, p.get("actions", []), p.get("tax_lots", []))

@app.post("/research/portfolio/capacity")
def phase9_portfolio_capacity(p: dict[str, Any] = Body(...)):
    return guarded(estimate_portfolio_capacity, p.get("positions", []), p.get("participation_rate", 0.10), p.get("stress_volume_haircut", 0.50))

@app.post("/research/derivatives/exposure")
def phase9_derivatives_exposure(p: dict[str, Any] = Body(...)):
    return guarded(aggregate_derivatives_exposure, p.get("positions", []))

@app.post("/research/knowledge-graph/financial-audit")
def phase9_financial_graph(p: dict[str, Any] = Body(...)):
    return guarded(audit_financial_graph, p.get("nodes", []), p.get("edges", []))

@app.post("/platform/resilience/disaster-recovery-simulate")
def phase9_disaster_recovery(p: dict[str, Any] = Body(...)):
    return guarded(simulate_disaster_recovery, p.get("services", []))

@app.post("/research/performance/benchmark")
def phase9_benchmark_performance(p: dict[str, Any] = Body(...)):
    return guarded(benchmark_performance, p.get("portfolio_returns", []), p.get("benchmark_returns", []), p.get("periods_per_year", 252))

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


@app.post("/platform/connectors/contract-audit")
def api_connector_contract(p: dict[str, Any] = Body(...)):
    return guarded(connector_contract, p)

@app.post("/research/market/replay")
def api_market_replay(p: dict[str, Any] = Body(...)):
    return guarded(market_replay, p.get("events", []))

@app.post("/research/portfolio/optimize")
def api_portfolio_optimizer(p: dict[str, Any] = Body(...)):
    return guarded(portfolio_optimizer, p.get("volatilities", []), p.get("max_weight", 1.0))

@app.post("/paper/orders/lifecycle")
def api_paper_order(p: dict[str, Any] = Body(...)):
    return guarded(paper_order_lifecycle, p)

@app.post("/research/factors/diagnostics")
def api_factor_diagnostics(p: dict[str, Any] = Body(...)):
    return guarded(factor_diagnostics, p.get("asset_returns", {}), p.get("factor_returns", {}))

@app.post("/research/options/greeks-aggregate")
def api_derivatives_risk(p: dict[str, Any] = Body(...)):
    return guarded(derivatives_risk, p.get("positions", []))

@app.post("/research/events/calendar-audit")
def api_event_calendar(p: dict[str, Any] = Body(...)):
    return guarded(event_calendar_audit, p.get("events", []))

@app.post("/research/experiments/manifest")
def api_experiment_manifest(p: dict[str, Any] = Body(...)):
    return guarded(experiment_manifest, p)

@app.post("/research/portfolio/stress")
def api_portfolio_stress(p: dict[str, Any] = Body(...)):
    return guarded(portfolio_stress, p.get("returns", {}), p.get("weights", {}), p.get("shocks", {}))

@app.post("/platform/sre/health")
def api_sre_health(p: dict[str, Any] = Body(...)):
    return guarded(sre_health, p.get("services", []))

@app.post("/platform/security/rbac-audit")
def api_rbac_audit(p: dict[str, Any] = Body(...)):
    return guarded(rbac_audit, p.get("grants", []))

@app.post("/platform/workflow/gate")
def api_workflow_gate(p: dict[str, Any] = Body(...)):
    return guarded(workflow_gate, p)


@app.post("/platform/data/market-batch-audit")
def api_market_batch_audit(p: dict[str, Any] = Body(...)):
    return guarded(audit_market_batch, p.get("provider", ""), p.get("dataset", ""),
                   p.get("license_status", "unverified"), p.get("records", []))

@app.post("/data/market/batch-ingest")
def api_market_batch_ingest(p: dict[str, Any] = Body(...)):
    audit = guarded(audit_market_batch, p.get("provider", ""), p.get("dataset", ""),
                    p.get("license_status", "unverified"), p.get("records", []))
    if not audit["ingestion_allowed"]:
        raise HTTPException(422, detail={"message": "batch rejected by data-quality or license gate", "audit": audit})
    saved = []
    for record in p["records"]:
        normalized = guarded(normalize_market_record, record)
        saved.append(guarded(store.put, dataset=p["dataset"], record_key=normalized["record_key"],
            source=p["provider"], event_time=normalized["event_time"],
            available_at=normalized["available_at"], payload=normalized["payload"]))
    return {"accepted": len(saved), "records": saved, "live_execution": False}

@app.get("/data/market/as-of")
def api_market_as_of(dataset: str = Query(..., min_length=1, max_length=100),
                     as_of: str = Query(...), limit: int = Query(100, ge=1, le=1000)):
    return {"dataset": dataset, "as_of": as_of, "records": guarded(store.list_as_of, dataset, as_of, limit)}

@app.post("/platform/data/instrument-master-audit")
def api_instrument_master_audit(p: dict[str, Any] = Body(...)):
    return guarded(audit_instrument_master, p.get("instruments", []))

@app.post("/platform/data/feed-health")
def api_feed_health(p: dict[str, Any] = Body(...)):
    return guarded(feed_health_snapshot, p.get("last_event_time", ""),
                   p.get("last_available_at", ""), p.get("as_of", ""), p.get("max_lag_seconds", 60.0))


@app.post("/research/backtest/portfolio")
def api_portfolio_backtest(p: dict[str, Any] = Body(...)):
    return guarded(run_portfolio_backtest,
        p.get("asset_returns", []), p.get("target_weights", []),
        commission_bps=p.get("commission_bps", 0.0),
        spread_bps=p.get("spread_bps", 0.0),
        slippage_bps=p.get("slippage_bps", 0.0),
        impact_bps=p.get("impact_bps", 0.0),
        periods_per_year=p.get("periods_per_year", 252),
        max_gross_leverage=p.get("max_gross_leverage", 1.0),
        initial_capital=p.get("initial_capital", 1_000_000.0))


@app.post("/risk/portfolio/real-time-monitor")
def api_phase10_risk_monitor(p: dict[str, Any] = Body(...)):
    return guarded(monitor_portfolio_risk, p.get("positions", []), p.get("nav", 0),
                   p.get("max_gross_leverage", 2.0), p.get("max_name_weight", 0.25),
                   p.get("returns"), p.get("confidence", 0.95))

@app.post("/platform/data/vendor-failover-audit")
def api_phase10_vendor_failover(p: dict[str, Any] = Body(...)):
    return guarded(audit_vendor_failover, p.get("feeds", []),
                   p.get("max_age_seconds", 60), p.get("conflict_tolerance_bps", 5))

@app.post("/research/alpha/factory-rank")
def api_phase10_alpha_factory(p: dict[str, Any] = Body(...)):
    return guarded(score_alpha_hypotheses, p.get("experiments", []))

@app.post("/portfolio/capital/risk-budget")
def api_phase10_risk_budget(p: dict[str, Any] = Body(...)):
    return guarded(allocate_risk_budget, p.get("strategies", []),
                   p.get("total_risk_budget", 0), p.get("max_strategy_weight", 0.4))

@app.post("/platform/agents/governance-audit")
def api_phase10_agent_governance(p: dict[str, Any] = Body(...)):
    return guarded(audit_agent_governance, p.get("agents", []))

@app.post("/research/execution/tca")
def api_phase10_tca(p: dict[str, Any] = Body(...)):
    return guarded(analyze_execution_quality, p.get("trades", []))

@app.post("/research/risk/systemic-stress")
def api_phase10_systemic_stress(p: dict[str, Any] = Body(...)):
    return guarded(simulate_systemic_stress, p.get("exposures", []),
                   p.get("shocks", {}), p.get("nav", 0))

@app.post("/platform/strategies/approval-review")
def api_phase10_strategy_approval(p: dict[str, Any] = Body(...)):
    return guarded(evaluate_strategy_approval, p)


from .phase11_systems import (
    reconcile_ledger, simulate_order_lifecycle, calibrate_transaction_costs,
    options_greeks_risk, margin_funding_stress, plan_position_exit,
    evaluate_model_rollout, evaluate_kill_switch,
)

@app.post("/portfolio/ledger/reconcile")
def api_phase11_ledger(p: dict[str, Any] = Body(...)):
    return guarded(reconcile_ledger, p.get("positions", []), p.get("broker_positions", []),
                   p.get("cash", 0), p.get("broker_cash", 0), p.get("tolerance", 1e-6))

@app.post("/execution/orders/lifecycle-audit")
def api_phase11_order_lifecycle(p: dict[str, Any] = Body(...)):
    return guarded(simulate_order_lifecycle, p.get("order", {}))

@app.post("/research/execution/cost-calibration")
def api_phase11_cost_calibration(p: dict[str, Any] = Body(...)):
    return guarded(calibrate_transaction_costs, p.get("observations", []))

@app.post("/risk/options/greeks-aggregate")
def api_phase11_options_greeks(p: dict[str, Any] = Body(...)):
    return guarded(options_greeks_risk, p.get("positions", []))

@app.post("/risk/margin/funding-stress")
def api_phase11_margin(p: dict[str, Any] = Body(...)):
    return guarded(margin_funding_stress, p.get("positions", []), p.get("available_funds", 0),
                   p.get("maintenance_margin", 0), p.get("funding_bps", 0))

@app.post("/portfolio/liquidity/exit-plan")
def api_phase11_exit_plan(p: dict[str, Any] = Body(...)):
    return guarded(plan_position_exit, p.get("positions", []),
                   p.get("adv_participation", 0.1), p.get("stress_multiplier", 2.0))

@app.post("/platform/models/rollout-gate")
def api_phase11_model_rollout(p: dict[str, Any] = Body(...)):
    return guarded(evaluate_model_rollout, p.get("metrics", {}))

@app.post("/platform/risk/kill-switch/evaluate")
def api_phase11_kill_switch(p: dict[str, Any] = Body(...)):
    return guarded(evaluate_kill_switch, p.get("health", {}), p.get("risk_breaches", 0),
                   p.get("data_critical"), p.get("manual_halt", False))


from .phase13_integration import evaluate_data_to_risk_pipeline


@app.post("/platform/integration/data-to-risk")
def api_phase13_data_to_risk(p: dict[str, Any] = Body(...)):
    observations = guarded(store.list_as_of, p.get("dataset", ""), p.get("as_of", ""),
                           p.get("limit", 100))
    return guarded(
        evaluate_data_to_risk_pipeline, observations, p.get("as_of", ""),
        p.get("max_age_seconds", 60.0), p.get("positions", []),
        p.get("max_gross_exposure", 1.0), p.get("max_single_name_weight", 0.5),
        p.get("current_drawdown", 0.0), p.get("max_drawdown", 0.2),
        p.get("manual_halt", False),
    )


from .phase14_operations import audit_data_quality, evaluate_recovery_gate


@app.post("/platform/operations/data-quality-audit")
def api_phase14_data_quality(p: dict[str, Any] = Body(...)):
    return guarded(audit_data_quality, p.get("observations", []),
                   p.get("as_of", ""), p.get("max_age_seconds", 60.0))


@app.post("/platform/operations/recovery-gate")
def api_phase14_recovery_gate(p: dict[str, Any] = Body(...)):
    return guarded(evaluate_recovery_gate, p.get("service_checks", []),
                   p.get("data_quality_pass", False), p.get("risk_reconciled", False),
                   p.get("manual_approval", False), p.get("consecutive_healthy_checks", 0),
                   p.get("required_healthy_checks", 3))


from .phase15_audit_chain import build_audit_chain, verify_audit_chain


@app.post("/platform/audit/chain-build")
def api_phase15_audit_chain_build(p: dict[str, Any] = Body(...)):
    return guarded(build_audit_chain, p.get("events", []))


@app.post("/platform/audit/chain-verify")
def api_phase15_audit_chain_verify(p: dict[str, Any] = Body(...)):
    return guarded(verify_audit_chain, p.get("chain", {}))


from .phase16_durable_audit import DurableAuditStore

durable_audit_store = DurableAuditStore(store.database_path)


@app.post("/platform/audit/durable-append")
def api_phase16_audit_append(p: dict[str, Any] = Body(...)):
    return guarded(durable_audit_store.append, p.get("event", {}))


@app.get("/platform/audit/durable-verify")
def api_phase16_audit_verify():
    return durable_audit_store.verify()
