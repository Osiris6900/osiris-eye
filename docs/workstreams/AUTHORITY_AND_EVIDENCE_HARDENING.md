# Authority, paper reconciliation and evidence custody

This change continues W01–W20 engineering delivery on top of #426. It reuses
the existing authority database, paper broker and evidence client. It does not
merge the pending integration stack or accept any independent release gate.

## Corrected behavior

### W06 / W09 / W15: authority time

An expired lease previously became usable again if the authority clock moved
backward. A refused expired worker completion had the same problem, including
after restarting the process. Acquisitions and claims also sampled time before
waiting for the database lock; renewal sampled the clock twice.

Timed authority operations now sample once inside the database write lock and
persist the greatest observed authority time. Nonfinite, nonnumeric, negative
or unsupported calendar values fail closed. A backward observation returns
`authority_clock_rollback` until time catches up. The record survives restarts.
Refusal commits the time observation but rolls business changes back through a
savepoint, so observing expiry cannot be undone by a failed transaction.

The regression tests exercise expired writes, renewal, worker completion,
restart, contention, invalid clocks, failed business writes and catch-up with a
new fencing epoch. Clock recovery remains an operator concern: a forward clock
error can withhold authority until the recorded time is reached again. The
high-water mark detects observed rollback; it is not authenticated time, a
distributed clock, consensus or proof about unobserved time between requests.

### W02: incremental broker economics

Two apparently valid cumulative averages can imply an invalid new fill. For
example, a buy limit of 100 with one unit filled at 50 followed by two units at
an average of 100 implies a second fill at 150. The previous reconciliation
accepted both snapshots.

Reconciliation now uses exact rational arithmetic over the reported cumulative
quantity and average to check the incremental notional. Nonpositive economics,
a buy above its limit or a sell below its limit are refused without replacing
the last verified snapshot or sending another order. Tests cover both sides,
exact limit fills, sub-Decimal-context excess and negative/zero increments.
The adapter uses the exact reported values: ambiguous provider rounding or
corrections require investigation rather than an invented tolerance. This is
snapshot consistency checking, not independently verified trade-level fills.

### W19: immutable evidence bytes

S3 custody previously checked Object Lock metadata without reading back the
stored content. It also serialized the caller's mutable bundle after network
calls, permitting already-verified content to change before upload.

Custody now freezes and verifies the serialized bundle before external calls.
After the conditional upload and retention check, it reads the exact returned
version with `ChecksumMode=ENABLED`. It requires the same version, content
length, KMS encryption and byte-for-byte content, plus a matching SHA-256
checksum if returned. Reads are bounded to the expected size plus one byte;
the stream closes on success and failure. Only successful readback returns
`content_readback_verified=true`.

The configured S3 identity consequently needs permission to read the object
version and decrypt it in addition to the existing write/retention permissions.
Tests use local SDK-compatible fixtures for corruption, truncation, extra data,
wrong version/metadata/checksum, failed reads and mutation during upload.
No real S3 account, key, bucket or independently held signing key was used.

## Validation

```bash
python -m pytest -q services/quant-api/tests/test_authority_clock.py \
  services/quant-api/tests/test_evidence_readback.py \
  services/quant-api/tests/test_control_plane_broker.py \
  services/quant-api/tests/test_control_plane_coordination.py \
  services/quant-api/tests/test_control_plane_operations.py
python scripts/verify_all.py --profile engineering --execute \
  --report /tmp/osiris-authority-custody-verification.json
```

Use an editable project installation and the Python/Node/Rust toolchains on
PATH. Local process checks require permission to create loopback and Unix
sockets. The engineering report binds the source snapshot and retained artifact
hashes to all twenty workstream check groups; independent acceptance remains
pending even when every local check passes.

The expanded #413–#416 products and external acceptance remain separate work.
Vendor licences/credentials, independent crypto/model/risk review, sustained
independent-host P2P/HA/DR, actual staging/custody and owner signoff are still
required. Release inventory remains 3 evidenced / 14 partial / 3 remaining.
`live_trading_authorized=false`; no live broker request, production deployment,
financial commitment or external notification is part of this change.
