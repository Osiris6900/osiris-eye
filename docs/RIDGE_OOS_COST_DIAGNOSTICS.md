# Derived fitted-ridge walk-forward cost diagnostics

The original keyless forward archive's 0/10/25 bps sensitivity evaluates its
momentum reference model. Its fitted ridge baseline previously reports only
holdout MSE. This separate offline analysis evaluates **fitted ridge predictions**
against next-observed-trade returns, turnover and explicit cost assumptions.
It neither changes nor replaces the original capture, frozen plan or verifier.

Each fold fits the existing `ModelService` ridge estimator with three lagged
returns, train-only normalization, fixed ridge penalty 0.001, a minimum eight
training rows and four test rows. Every training label must arrive strictly
before the first test decision; the overlapping boundary row is purged. Model
decision/label milliseconds round client availability up, never before receipt.
Test features contain only returns already available at that test decision.

Predicted positive return takes an unlevered long position of one; other
predictions are flat. Each fold begins flat and liquidates at its end, including
both costs even if the next fold would stay long. Fixed 0/10/25 bps costs apply
per unit absolute position turnover. A separate sensitivity also applies half
the observed quoted spread at each rebalance and terminal liquidation. Terminal
spread uses the final label quote. Costs subtract from that interval's diagnostic
return, and interval/fold net factors compound. This is an explicit research cost
approximation, not the canonical cash ledger, executable quote, fee schedule,
venue-calibrated fill/queue/impact model or continuously deployed strategy.

Freeze the analysis plan **before running this new analysis**, in a clean
committed checkout with the capture's Python/NumPy environment:

```bash
python scripts/record_ridge_oos_costs.py plan \
  --archive /private/research-archive.json \
  --archive-commit FULL_CAPTURE_SHA --archive-sha256 TRUSTED_CAPTURE_DIGEST \
  --output /private/ridge-plan.json
python scripts/record_ridge_oos_costs.py evaluate \
  --archive /private/research-archive.json --plan /private/ridge-plan.json \
  --plan-sha256 PRINTED_PLAN_DIGEST --output /private/ridge-report.json
python scripts/record_ridge_oos_costs.py verify \
  --archive /private/research-archive.json --report /private/ridge-report.json \
  --sha256 PRINTED_REPORT_DIGEST --analysis-commit FULL_ANALYSIS_SHA
```

The plan binds the trusted original archive identity, fixed analysis policy,
analysis source hashes, exact analysis commit and runtime. The report retains
each model, purged fold, prediction, cost/turnover result and its canonical digest.
Offline verification rebuilds the original archive and derived analysis;
rehashed performance or approval edits do not pass reproduction. Files are
exclusive, private and outside the source checkout. No command accesses a
network or broker.

This is **post-capture diagnostic analysis**, explicitly not a blinded holdout.
The existing 32-quote sample covers roughly 95 seconds and cannot establish
representative strategy performance. Original source rights, independent leakage,
model-risk, cost calibration and release decisions remain pending. Every result
keeps `blinded_holdout`, `independent_model_reviewed`, `vendor_rights_verified`,
`release_gate_accepted` and `live_trading_authorized` false. No gate is promoted.
