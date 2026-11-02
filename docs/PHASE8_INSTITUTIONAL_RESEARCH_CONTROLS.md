# Phase 8 — Institutional Research Controls

Phase 8 adds eight deterministic, request-driven prototype capabilities. They are control-plane diagnostics, not production services, vendor integrations, or investment recommendations.

## Capabilities and API

| Capability | Endpoint | Scope |
|---|---|---|
| Data lineage audit | `POST /platform/data/lineage-audit` | Required provenance fields, timezone-aware event/availability timestamps, duplicate IDs, operator-declared license status |
| Backtest integrity audit | `POST /research/backtest/integrity-audit` | PIT data, corporate actions, delistings, costs, walk-forward, untouched holdout, multiple-testing disclosure |
| Portfolio risk governor | `POST /risk/governor/evaluate` | Gross exposure, concentration and drawdown threshold diagnostics |
| Catalyst impact scenarios | `POST /research/catalysts/impact-scenarios` | Caller-supplied deterministic shocks and portfolio weights |
| Model observability | `POST /research/models/observability` | Mean and standard-deviation shift alerts |
| Paper trade reconciliation | `POST /paper/trading/reconcile` | Fill-to-order quantity and filled-position reconciliation |
| Security resilience audit | `POST /platform/security/resilience-audit` | MFA, least privilege, secrets, logs, restore and kill-switch controls |
| Research workflow orchestrator | `POST /platform/workflow/research-orchestrate` | Explicit gates plus human approval; never enables routing |

## Safety and limitations

- All eight registry entries are `prototype`; no live feeds, broker adapters, model deployment, policy enforcement, or order routing are added.
- License status is an operator assertion and is not independently verified.
- Backtest integrity flags are caller-supplied evidence; they do not prove a backtest is unbiased.
- Catalyst shocks are deterministic what-if inputs, not predictions, probabilities, or forecasts.
- Risk thresholds and NAV weights are supplied by the caller; production-grade limits require governed configuration and independent review.
- Paper reconciliation is a lightweight quantity reconciliation, not a full cash, fees, corporate-actions, or clearing ledger.
- Security checks are self-attested control flags, not penetration tests or certification.
- The workflow orchestrator is a gate summary only. Models and agents cannot bypass the independent risk kernel or human approval.
