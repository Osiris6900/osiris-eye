# Phase 9 — Global Multi-Asset Intelligence & Operations

Phase 9 adds eight deterministic research/control prototypes to the OSIRIS EYE API.

## Systems and API routes

1. **Global Multi-Asset Coverage Engine** — `POST /platform/instruments/global-coverage`; validates instrument identifiers, asset-class metadata, currency, exchange, calendar, timezone, and derivative contract fields.
2. **Counterparty & Systemic Risk Engine** — `POST /risk/counterparties/concentration`; aggregates supplied exposure by counterparty and flags concentration above a caller-set NAV threshold.
3. **Corporate Actions & Tax-Lot Intelligence** — `POST /portfolio/corporate-actions/audit`; validates split/dividend event metadata and aggregates supplied lot cost basis.
4. **Portfolio Capacity & Crowding Engine** — `POST /research/portfolio/capacity`; estimates exit days from supplied ADV, participation, and stressed volume haircut.
5. **Cross-Asset Derivatives Intelligence** — `POST /research/derivatives/exposure`; aggregates multiplier-adjusted delta, gamma, vega, theta, and rho by underlying.
6. **Financial Knowledge Graph 2.0** — `POST /research/knowledge-graph/financial-audit`; checks node identifiers, edge endpoints, relationship labels, and optional observation timestamps.
7. **Disaster Recovery & Failover Simulator** — `POST /platform/resilience/disaster-recovery-simulate`; compares caller-reported RTO/RPO test results with targets.
8. **Institutional Performance & Benchmark Intelligence** — `POST /research/performance/benchmark`; calculates compounded portfolio/benchmark returns, active return, tracking error, and information ratio.

## Important boundaries

All calculations use request-supplied data. No market, broker, clearing, counterparty-credit, corporate-action, tax, benchmark, or FX feeds are connected. Exchange calendars and instrument terms are not externally certified. Tax-lot output is not tax advice and does not calculate tax liability. Capacity and stressed exit estimates are simple ADV heuristics, not calibrated market-impact or crowding models. Greeks are only as reliable as caller-supplied sensitivities and multipliers. Graph edges are not causal proof. DR results are self-reported test evidence, not an actual failover or certification. Performance metrics omit factor attribution, FX translation, cash flows, and manager-level attribution.

These prototypes do not enable live trading, order routing, production failover, or autonomous risk overrides. Missing or invalid critical fields should block readiness; human review and independent risk controls remain mandatory.
