# Policy-gated outbox retry

This scheduler is opt-in and disabled unless `OSIRIS_OUTBOX_RECEIPT_RETRY=1`.
It does not change `ObservationOutbox.drain`. A successful local stdin write is
still not a recipient acknowledgement, and a verified receipt does not start a
new transmission.

It depends on the receipt ledger merged in PR #151. Do not enable it in
production from this scaffold. Issue #133 also asks for cross-host acceptance
and operational alerts; those are not done here.

## What a retry is

The Rust node creates a new random message id and ciphertext for every stdin
observation. The recipient admits that message id once and then replay-rejects
it. Sending the line again therefore does not retry the same receipt. It mints
a new envelope and can duplicate the observation if the first one was admitted
without a receipt coming back.

The scheduler writes stdin only while an attempt has no tracked envelope:

1. An explicit trusted set and an explicit revoked set are both required.
   Either set missing is `policy_uncertain` and does not send. This is stricter
   than the node's open mode, where an unset allowlist accepts peers.
2. The recipient must be in the trusted set and absent from the revoked set.
   Revocation and any other explicit denial are terminal for that row.
3. Ingestion kill switch, provider allowlist, payload hash and freshness are
   rechecked. A stale observation expires with no write. A future-dated
   observation or a disabled kill switch stays scheduled and is not counted as
   a send.
4. The verified-receipt channel must be reported ready. If it is not, nothing
   is written. Repeated outages become `held` after 8 deferrals for manual review.
5. A writer that fails before acceptance (`PublishNotSent`) backs off and may
   try again. Backoff is deterministic: 2, 4, 8, then 16 seconds, capped at 60.
   The fourth failed send is `held` with `retry_budget_exhausted`.
6. A tracked result registers `(message_id, recipient, envelope_sha256)` in
   `RecipientReceiptLedger` before the attempt is marked `published`. No later
   pass writes that pair again.

After publication the attempt waits up to 120 seconds, the same window as the
in-memory receipt tracker. `acknowledged` in the ledger suppresses the row.
If the wait ends first, the ledger row becomes `unconfirmed` and the attempt
stops. Unconfirmed is not a permission to send a new envelope.

## Crash and concurrency

The claim state is `publishing`. Opening an enabled scheduler moves any
`publishing` row to `held` / `crash_while_publishing` before a send. That covers
a crash after the claim, including a crash after the node accepted the line but
before the correlation was stored. The cost is that a crashed attempt is not
retried automatically.

Two workers can call `advance`. The SQLite claim lets one of them publish.
Schedule of the same observation key and recipient is idempotent and does not
reset a published, held, denied, or acknowledged row.

`publish` must raise `PublishNotSent` only when the node cannot have accepted
the line. Any other exception is treated as uncertain and the row is held.

## Manual review

`held`, `denied`, `expired`, and `unconfirmed` do not send again. An operator
has to inspect the ledger and the node log before doing anything else. Deleting
a row in order to force another stdin write creates a new message id. This
scheduler will not do that by itself.

There is no exactly-once network delivery. A recipient can admit an envelope
and fail before signing the receipt. The matching attempt stays unconfirmed
instead of being re-sent.

## Configuration

```bash
export OSIRIS_OUTBOX_RECEIPT_RETRY=0   # default when unset
export OSIRIS_P2P_INGEST_ENABLED=1     # required before a write is attempted
```

`ReceiptRetryScheduler` reads the retry flag only when `enabled` is omitted.
Tests and supervisors that construct it with `enabled=False` cannot schedule.

Counts from `counts()` are local row totals. They are not a metrics endpoint
and they do not page an operator.
