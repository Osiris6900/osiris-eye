# #416 package 48 — Saved broker-order reconciliation integrity

`AlpacaPaperBroker.reconcile(client_id)` validates retained evidence before GET.
An upstream response cannot repair a mismatched client identity or silently
replace an absent/corrupt acknowledged snapshot.

The saved intent has the same five fields accepted by submission. Its client ID
matches the database key, its fingerprint matches the complete intent, symbol
and client identifiers are valid, side is BUY/SELL (case-insensitive), and quantity
and limit price satisfy the existing positive bounded money contract. Cancellation
uses this shared intent check too.

| Retained state | Reconciliation behavior |
| --- | --- |
| Missing client record | Recover a matching canonical reservation's intent for GET only; otherwise `unknown_client_order` |
| Malformed JSON/state or invalid intent/fingerprint/client binding | `broker_order_contract_required` before HTTP |
| Local status other than `acknowledged` or `submitting` | `order_outcome_unknown_reconcile_first` before HTTP |
| `acknowledged` with missing or invalid response | `broker_snapshot_mismatch` before HTTP |
| `submitting` without a response field | Allow GET to recover an uncertain send |
| Either allowed status with a response field | Validate that response before GET, including explicit `null` |

A fetched snapshot must match the saved intent and preserve the previous broker
ID, fill progress, terminal state, and execution-price rules. Valid terminal
snapshots can still be reconciled. Integer intent amounts, equivalent decimal
response strings, and accepted side casing retain their existing meaning.

Invalid retained evidence, failed transport and invalid new responses leave the
record unchanged. A concurrent write still causes a version conflict rather than
overwriting the newer record. HTTP runs outside the database transaction; this
check adds no distributed lock or automatic retries.

These are local integrity checks, not signatures or proof of broker/source truth.
They do not establish independent release acceptance. Live execution stays disabled.

## Canonical external-order reservations

The opt-in Alpaca PAPER adapter now commits a reservation in
`paper_exec_broker_reservations` in `OSIRIS_PAPER_EXECUTION_DB` together with its
server-side pretrade approval, before creating the control-store send claim or
making POST. Native simulated orders and external commitments share cash,
sellable shares, position and gross limits. Pending sells do not supply cash or
reduce gross exposure; pending buys do not supply sellable inventory. A fresh
higher mark increases external pending-buy exposure without changing the limit
price used to reserve cash. Caller-supplied risk arguments cannot approve a send.

Reservations preserve the original five-field intent and bind it to the resolved
control-database path and the PAPER adapter. The canonical audit journal binds
the complete reservation projection; row changes, deletion and invented rows
close the ledger gate. Native and external orders cannot reuse each other's
client IDs, even after terminality. Repeated identical snapshots write nothing.

| Broker evidence | Quantity still reserved |
| --- | --- |
| No response, timeout, invalid response, POST result not durably saved | Entire requested quantity |
| Accepted, partially filled, pending cancel, suspended or other nonterminal snapshot | Entire requested quantity |
| Verified canceled, expired, rejected or day-order `done_for_day` snapshot | Reported filled quantity; release only its unfilled remainder |
| Verified filled snapshot | Entire filled quantity |
| DELETE success or failure, local cancellation or local day expiry | No external reservation release |

Filled commitments deliberately remain held. Cumulative broker quantity and
average price do not provide execution IDs, exact individual execution prices
or fees. This change imports **no fills, cash, positions or P&L** from those
snapshots. Any reported filled quantity sets `broker_accounting_pending` and
blocks new submissions and simulated fills with `broker_fills_require_accounting`.
Cancellation and verified terminal reconciliation remain available. Completing
external fill ingestion requires a separate, journal-bound accounting change;
there is no flag or clean-book acknowledgement that clears this hold.

The execution snapshot exposes `broker_reservations`, `broker_accounting_pending`,
`reserved_cash`, `available_cash` and `reserved_sell_quantities`. Broker-book
comparison includes external pending commitments, while missing external fill
accounting remains a mismatch. Internal arithmetic reconciliation can still pass
with an explicitly reported accounting hold; that does not authorize new risk.

### Crash and migration behavior

Control and execution databases may share a SQLite file, but no operation nests
their write transactions or holds a transaction across HTTP. A crash between
canonical reservation and control-store claim leaves capacity held. Retrying
submission never POSTs that intent again. Explicit `reconcile(client_id)` can
rebuild its uncertain transport claim from the bound canonical intent and GET
the broker result. A timeout or 404 keeps the commitment for investigation.
Reconciliation persists its version-checked control-store response before
updating the reservation; a failure between those commits retains the older,
conservative reservation until another reconciliation. This is a recovery
protocol, not an atomic two-database commit or exactly-once broker execution.

Before a new broker send, every retained intent in that adapter's control store
must have matching canonical commitment and progress. Configure `OSIRIS_CONTROL_DB`
to that same store so native submissions and fills also check its retained
inventory. Legacy uncertain/open orders need explicit reconciliation to adopt
their commitments, even if current limits would reject a fresh order. Legacy
client IDs already used by native simulated orders fail closed for investigation.
Do not move or replace a control store to bypass this binding; missing/malformed
configured inventory closes the new-risk gate. Arbitrary unconfigured broker
accounts/stores and independently placed orders cannot be discovered here.

Tests use fake transport and isolated SQLite accounts. They do not activate an
order route, connect a real broker, promote a release gate or authorize live
trading.
