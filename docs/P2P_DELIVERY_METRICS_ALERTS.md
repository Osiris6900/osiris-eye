# Delivery metrics and operational alerts

Issue #133 requires metrics for pending, acknowledged, unconfirmed,
quarantined, retry age and dead-letter counts, plus documented operational
alerts. `osiris_api.delivery_metrics` provides both as read-only functions
over the existing durable stores. It writes nothing, sends nothing and
acknowledges nothing.

## What is measured

`collect_delivery_metrics(outbox=, ledger=, scheduler=, now=)` returns an
`osiris.delivery_metrics.v1` report. Every component is optional; an omitted
component reports `configured: false` instead of guessing.

| Section | Metric | Meaning |
| --- | --- | --- |
| `outbox` | `pending` | Undelivered, non-quarantined rows in `ObservationOutbox` |
| `outbox` | `quarantined` | Rows retained with a recorded rejection reason |
| `receipt_ledger` | `pending` | Registered correlations awaiting a verified receipt |
| `receipt_ledger` | `acknowledged` | Rows committed by the authenticated receipt consumer |
| `receipt_ledger` | `unconfirmed` | Receipt wait expired; outcome unknown |
| `receipt_ledger` | `oldest_outstanding_age_seconds` | Retry age: now minus the oldest pending or unconfirmed row's `created_at`, clamped at 0 for clock skew |
| `retry_scheduler` | `counts` | Row totals per attempt state |
| `retry_scheduler` | `dead_letter` | `held` + `denied` + `expired` attempts |

A disabled scheduler reports `enabled: false` and contributes no counts.

## Alerts

`evaluate_operational_alerts(report, ...)` compares the snapshot against
explicit thresholds and returns deterministic alerts. Defaults:

| Alert | Default condition | Severity | Operator action |
| --- | --- | --- | --- |
| `outbox_backlog` | `pending` > 100 | warning | Check the drainer, the writer and the ingestion kill switch |
| `quarantined_observations` | `quarantined` > 0 | warning | Review recorded rejection reasons; quarantine is terminal until then |
| `stale_pending_receipts` | oldest outstanding age > 240s | warning | Check the receipt listener, supervisor PID/UID pinning and recipients; do not resend |
| `unconfirmed_deliveries` | `unconfirmed` > 0 | warning | Unknown outcome, not loss; never a reason to mint a new envelope |
| `dead_letter_rows` | `dead_letter` > 0 | critical | Inspect each row's reason (`crash_while_publishing`, `retry_budget_exhausted`, revocation, …) before any action |

Thresholds are caller-supplied integers; negative or non-integer values are
rejected. `delivery_health_report` runs collection and evaluation in one call.

## Honest edges

- All numbers are local SQLite observations. There is no metrics endpoint and
  nothing here pages an operator; wire the report into your own monitoring.
- `acknowledged` means a verified admission receipt was durably committed. It
  is not application processing, market truth or a trading confirmation.
- `unconfirmed` means the 120-second receipt window expired. The recipient may
  have admitted the envelope and failed before signing. It must not trigger a
  resend; the retry scheduler will not rewrite a tracked envelope either.
- Retry age comes from ledger `created_at`; the outbox stores no timestamps,
  so queue age is not reported.
- Alerts never authorize retransmission. Remediation is always operator
  inspection first.
