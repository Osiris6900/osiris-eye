# OSIRIS EYE Phase 3 — 12 Institutional Workflow Foundations

This phase adds twelve API-level research and control foundations (catalog: 44). These are deliberately bounded prototypes, not production integrations.

| System | Endpoint | Current boundary |
|---|---|---|
| Global Data Connector Hub | POST /platform/connectors/contract-audit | Licensed-source contract audit; no vendor adapter |
| Tick-by-Tick Market Replay | POST /research/market/replay | Deterministic timezone-aware ordering of supplied events |
| Portfolio Construction Optimizer | POST /research/portfolio/optimize | Capped inverse-volatility baseline, not a constrained optimizer |
| Paper Trading OMS | POST /paper/orders/lifecycle | Stateless simulated order lifecycle; no broker |
| Factor Intelligence | POST /research/factors/diagnostics | Historical single-factor OLS exposures; not causal |
| Derivatives Risk | POST /research/options/greeks-aggregate | Aggregates caller-supplied Greeks; no pricing model |
| Event & Earnings Intelligence | POST /research/events/calendar-audit | Event metadata/timezone audit; no calendar feed |
| Experiment Reproducibility | POST /research/experiments/manifest | Canonical manifest hash; no immutable registry |
| Portfolio Stress Lab | POST /research/portfolio/stress | Deterministic user-supplied shocks; no probability forecast |
| Production SRE | POST /platform/sre/health | Service health aggregation; no telemetry collector/remediation |
| Institutional Security | POST /platform/security/rbac-audit | Privilege audit; does not enforce permissions |
| Research-to-Execution Control Plane | POST /platform/workflow/gate | Readiness checklist; no live order routing |

## Safety boundaries
- All endpoints are research, audit, or simulation only. No exchange/broker connections or live order routing are enabled.
- Connector acceptance is not proof of data licensing; operators must verify contracts and entitlements.
- Replay requires timezone-aware timestamps and preserves input order for equal timestamps.
- Portfolio optimizer is a transparent inverse-volatility heuristic, not a globally optimal allocation.
- Factor exposures and supplied Greeks are diagnostics; neither implies causality or validated valuation.
- Experiment hashes support reproducibility only when source data, code, parameters, and seeds are actually versioned.
- A workflow gate only makes a run eligible for separate governance review. It never authorizes live trading.
- Missing, stale, contradictory or unhealthy critical inputs must block new risk in downstream production integrations.

## Validation
API contract tests cover all twelve systems, invalid timestamps, allocation-cap feasibility, and the invariant that live order routing remains disabled. CI results must be checked after the commit; a pushed change is not a passing test.
