# #416 package 45 — Verified paper-broker cancellation

`AlpacaPaperBroker.cancel(broker_order_id)` previously sent DELETE for any
syntactically valid ID, including unknown IDs and orders already recorded as
filled or otherwise terminal.

Cancellation now resolves a unique saved `broker-orders` record by its broker
response ID. The lookup scans the collection rather than a truncated listing.
Before transport, it checks the saved intent's shape, fingerprint and client-ID
binding, requires an acknowledged outcome, and validates the saved broker
snapshot against that intent.

| Saved state | Result |
| --- | --- |
| No matching broker ID, including missing/unreadable response | `unknown_broker_order` |
| Multiple records name the broker ID | `ambiguous_broker_order` |
| Missing or inconsistent intent/fingerprint/client ID | `broker_order_contract_required` |
| Uncertain local outcome or broker `pending_cancel` | `order_outcome_unknown_reconcile_first` |
| Invalid broker snapshot | `broker_snapshot_mismatch` |
| `filled`, `canceled`, `expired`, `rejected`, `done_for_day` | `broker_order_terminal` |
| Verified nonterminal snapshot | DELETE the bound ID at the existing Alpaca paper origin |

Refusals make no HTTP request. A DELETE result is returned unchanged and does
not rewrite a snapshot, record a fill, mark an order canceled, or release a
reservation. Transport errors also preserve the saved snapshot. Call
`reconcile(client_order_id)` to establish the subsequent broker state.

Cancellation does not require a new-risk approval, fresh mark, or healthy order
clock. An operator can still request cancellation during a risk halt.

## Limits

This is a local snapshot guard. The database transaction ends before transport;
a newer local snapshot or a remote fill can race DELETE. It adds no distributed
cancel lock, durable cancellation intent, automatic retry, or exactly-once
transport guarantee. A successful empty DELETE response is not proof of final
cancellation. Until reconciliation records `pending_cancel` or a terminal
state, a subsequent explicit invocation may send another request.

Broker submissions still do not reserve canonical ledger exposure. Multiple
unfilled broker intents can therefore exceed an aggregate position/gross
budget even though each separate pretrade check passes. Fixing that requires
durable broker-intent reservations and coordinated fill/cancel/expiry recovery;
this cancellation guard does not close package 47. Live execution stays disabled.
