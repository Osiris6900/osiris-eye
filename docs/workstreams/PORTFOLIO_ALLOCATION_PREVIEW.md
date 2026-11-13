# Constrained portfolio allocation and scenario preview

This implements a scoped research work package from #413/#416. It provides
named long-only allocations, concentration caps, a cash reserve and explicit
scenario-loss evaluation. It does not complete either master issue or change
any release acceptance gate.

The existing portfolio-construction module advertises these capabilities; no
duplicate module or additional module count is introduced.

## Behavior

The existing inverse-volatility primitives now share one capped allocation
implementation. It scales relative volatilities on every redistribution round,
avoiding overflow from `1 / 5e-324` and underflow when a very low-volatility
asset reaches its cap. Both existing research APIs return finite JSON for that
previously failing case. Numeric strings and booleans are rejected; at most 128
assets are accepted. The legacy optimizer continues to reject a uniform cap
that cannot fund its required fully invested portfolio.

The new preview permits a smaller invested allocation. Each asset's effective
cap is the smaller of its individual cap and the portfolio-wide cap. The
algorithm redistributes remaining investment to eligible assets; insufficient
capacity stays in cash. A requested cash reserve is preserved, including at
floating-point boundaries. Zero per-asset caps exclude instruments, and a
reserve of one produces a cash-only portfolio.

## Authenticated API

`POST /control-plane/portfolio/preview` requires the existing `research-read`
permission and configured admin identity/control storage. Analyst and higher
research roles can use their existing authorization; viewers cannot. This
calculation follows the existing read-only simulation permission contract.
Responses use `Cache-Control: no-store` and the existing secret-redaction guard.
It does not initialize a paper account, submit a job or create an order.

Example request body:

```json
{
  "assets": [
    {"symbol": "A", "volatility": 0.1, "max_weight": 0.2},
    {"symbol": "B", "volatility": 0.2}
  ],
  "max_weight": 0.5,
  "cash_reserve": 0.1,
  "scenarios": [
    {"id": "crash", "shocks": {"A": -0.5, "B": -0.2}}
  ],
  "max_scenario_loss": 0.15
}
```

The preview allocates 20% to A and 50% to B, leaving approximately 30% cash.
The submitted crash implies a 20% portfolio loss, so `scenario_policy` is
`breach`. The candidate allocation remains visible for analysis; a preview is
never an independent risk approval or permission to execute it.

Up to 32 uniquely named scenarios are supported. Each must cover every asset
exactly once, including excluded assets. Missing/unknown symbols, duplicate
names, unexpected fields, invalid numbers and returns below -100% fail closed.
A loss budget without scenarios is rejected. Without a budget, diagnostics are
returned with `scenario_policy=not_evaluated`, never an inferred pass.

Scenario arithmetic compares exact decimal representations of the normalized
finite floats under a fixed local decimal context. This avoids a false breach
at a 15% boundary and prevents sub-float losses from silently passing a zero
budget. Decimal return/loss strings accompany the float projections. The
normalized input SHA-256 allows repeated requests to be compared without
claiming a signed execution or independently licensed-data attestation.

## Scope and validation

Inverse volatility is a baseline, not a covariance-aware or return-optimal
solver. Inputs are caller supplied; cash has zero scenario return. Submitted
scenarios do not guarantee future drawdown limits and do not model FX, fees,
liquidity or borrowing. Every response leaves `optimality_claim`,
`future_drawdown_guaranteed`, `risk_approval_granted`, `live_execution` and
`broker_order_submitted` false. Existing execution risk gates remain authoritative.

Focused regressions cover independently derived allocations, 200 deterministic
randomized cap/budget cases, extreme floats and cap rounds, exclusions/cash,
exact scenario boundaries, malformed inputs, RBAC/no-store/redaction and the
previously failing legacy API. Run:

```bash
python -m pytest -q services/quant-api/tests/test_portfolio_construction.py \
  services/quant-api/tests/test_phase3_systems.py \
  services/quant-api/tests/test_platform_modules.py
```
