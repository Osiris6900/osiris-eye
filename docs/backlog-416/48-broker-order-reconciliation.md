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
| Missing client record | `unknown_client_order` |
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
They do not reserve canonical exposure for external broker intents or establish
independent release acceptance. Live execution stays disabled.
