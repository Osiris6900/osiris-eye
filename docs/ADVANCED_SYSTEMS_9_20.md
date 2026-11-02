# Advanced Systems 9–20: Research Foundations

Twelve capability groups added; catalog total is 32. These are deterministic API foundations, not production integrations.

| # | System | Current scope |
|---|---|---|
| 9 | Order Book & Market Microstructure AI | multi-level depth, spread and imbalance diagnostics |
| 10 | Smart Execution Simulator | partial fills, fees and implementation shortfall simulation |
| 11 | AI Model Governance & Drift Lab | distribution shift diagnostics and deployment review |
| 12 | Financial Knowledge Graph | node and edge integrity audit |
| 13 | Strategy Discovery & Genetic Research Lab | lagged candidate diagnostics; multiple-testing warning |
| 14 | Market Regime & Change-Point Engine | two-window change heuristic |
| 15 | Monte Carlo & Scenario Simulation | seeded iid Gaussian scenario distributions |
| 16 | News, Filings & Sentiment Intelligence | source and publication-time intake audit |
| 17 | Security & Permission Intelligence | permission allowlist and privileged-role diagnostics |
| 18 | Portfolio Attribution & Performance Lab | arithmetic asset contribution attribution |
| 19 | Distributed Data & Compute Infrastructure | worker allocation plan only |
| 20 | Autonomous Monitoring & Incident Response | critical incident aggregation blocks new risk |

All endpoints are research-only. No external feeds, broker connectivity, order routing, deployment authorization, distributed runtime, or automated remediation is enabled. Strategy search is unadjusted and not out-of-sample validated. Monte Carlo assumes iid Gaussian returns and is not a forecast or guarantee. Security checks are diagnostics, not enforcement. Critical incident checks block new risk.

Routes: `POST /research/microstructure/order-book`, `/research/execution/simulate`, `/research/models/governance`, `/research/knowledge-graph/audit`, `/research/strategies/discover`, `/research/regime/change-point`, `/research/scenarios/monte-carlo`, `/research/news/audit`, `/platform/security/policy-audit`, `/research/portfolio/attribution`, `/platform/compute/plan`, `/platform/incidents/assess`.
