# Canonical paper account integration

Issue #364; dashboard integration #363; paper/risk work packages in #412 and #416.

When `OSIRIS_PAPER_EXECUTION_DB` is configured, Admin Centre and the connected
paper pipeline read the same execution ledger. An absent, unreadable or corrupt
execution account never causes a fallback to a differently funded synthetic
account. The response identifies its source as `canonical_execution`.

The account, orders, recent fills, marks and reconciliation report are read from
one SQLite snapshot. Admin reads use a read-only connection: they create no
database, run no migrations and submit no order. Unknown fill timestamps stay
null because the existing fill table does not retain execution timestamps.
Failed reconciliation hides accounting values and raises a critical risk flag.

Both views share the same valuation function. Cash and realized P&L remain known
without marks. Market value, equity and unrealized P&L require a fresh mark for
every holding; an incomplete set is never displayed as a complete valuation.
The default mark policy allows 300 seconds of age and 60 seconds of future skew.
The paper pipeline may use a tighter age policy from its retained run metadata.

## Existing account transition

1. Keep a copy of both existing databases. Select the execution ledger that
   already owns the pipeline's orders; do not add the two accounts' balances.
2. Check `/paper/execution/reconcile`, positions, cash, fees, realized P&L,
   pending orders and marks against the intended paper account.
3. Configure the Admin Centre API process with the same
   `OSIRIS_PAPER_EXECUTION_DB` path as the pipeline and execution API.
4. Compare `/admin/paper/status` against `/paper/pipeline/status` and the execution
   snapshot, including a later partial fill, cancellation and process restart.
5. Retain the legacy synthetic database separately. If the execution setting is
   absent, legacy mode remains available and is labeled `legacy_synthetic`.

This is an account-view transition, not a financial-history import. No historical
fills are silently replayed or merged, and configuring a path does not initialize
an account. Importing a separate legacy history remains an explicit migration
decision requiring its own reconciliation evidence.

## Verification and acceptance boundary

Automated tests cover divergent legacy balances, fresh/missing/stale/future marks,
partial fills, cancellation, later sales, shared kill-switch state, corrupted
cash/audit chains, missing files, byte-identical read-only access, authenticated
HTTP and a new process reading the same account. UI tests cover source labeling,
stored open-order counts and withholding unverified accounting.

These tests establish the local integration contract. Independent acceptance and
broader release gates remain open. Live trading remains disabled.
