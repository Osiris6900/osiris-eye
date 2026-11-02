# Phase 10 — Institutional Intelligence & Capital Protection

Phase 10 adds eight deterministic, caller-data prototype engines and API routes.

| System | Endpoint | Scope |
|---|---|---|
| Real-Time Portfolio Risk Monitor | `POST /risk/portfolio/real-time-monitor` | Gross leverage, concentration and optional historical VaR/ES diagnostics |
| Global Data Vendor Failover Governance | `POST /platform/data/vendor-failover-audit` | Freshness, health, operator-declared authorization and cross-vendor price conflicts; blocks when no safe source |
| Alpha Research Factory | `POST /research/alpha/factory-rank` | OOS hypothesis ranking with trial-count penalty and reproducibility/holdout gates |
| Dynamic Capital Allocation Engine | `POST /portfolio/capital/risk-budget` | Caller-scored strategy risk-budget allocation with caps; unused budget is not redistributed |
| AI Agent Governance & Security | `POST /platform/agents/governance-audit` | Tool allowlists, mandatory human approval, audit logging and prohibition on risk-gate bypass |
| Execution Quality & TCA | `POST /research/execution/tca` | Arrival-to-average-fill implementation shortfall |
| Systemic Crisis & Contagion Simulator | `POST /research/risk/systemic-stress` | Deterministic factor shocks applied to caller-supplied exposures |
| Independent Strategy Approval Board | `POST /platform/strategies/approval-review` | Evidence gates for lineage, integrity, walk-forward, untouched holdout, multiple testing, risk, paper trading and human approval |

## Important limitations

These are prototype diagnostics, not production real-time services. No streaming market data, vendor credentials, independent entitlement verification, calibrated VaR/ES, options Greeks, portfolio covariance, venue routing, causal contagion model, liquidation engine, agent sandbox, security certification, or deployment authorization is supplied. The vendor selector uses caller-reported health/authorization and freshness only. The alpha score is a transparent heuristic, not a statistical multiple-testing correction. Capital allocations are research outputs only. TCA is a simplified price-shortfall calculation and excludes fees, market impact, opportunity cost and partial-fill timing.

All critical data must fail closed. Agents cannot bypass independent risk controls. Paper/shadow validation and separate security, operational, regulatory and human approvals remain mandatory before any live-trading consideration. No live broker or order routing is enabled.
