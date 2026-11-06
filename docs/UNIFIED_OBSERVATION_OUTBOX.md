# Unified observation outbox

`broadcast_unified_cycle` collects market, world-event and celestial observations,
passes them through local ingestion, and writes accepted records to a node
adapter. Its optional `ObservationOutbox` stores pending delivery in a file-backed
SQLite database so a failed writer can be retried after reopening the outbox.

## Setup

Both bridge and ingestion must be enabled explicitly:

```bash
export OSIRIS_UNIFIED_MESH_BROADCAST=1
export OSIRIS_P2P_INGEST_ENABLED=1
```

Pass the outbox to the existing bridge call:

```python
from osiris_api.data_store import ResearchStore
from osiris_api.observation_outbox import ObservationOutbox
from osiris_api.unified_observation_bridge import broadcast_unified_cycle

store = ResearchStore("data/research.sqlite3")
outbox = ObservationOutbox("data/observation-outbox.sqlite3")
summary = broadcast_unified_cycle(write_line, store=store, outbox=outbox)
```

The caller supplies `write_line`: it receives one JSON string and must append a
newline, write and flush it to the local node's stdin, and raise on failure. The
bridge does not launch a node. It retries pending rows before collection, then
queues and drains newly ingested records. Omitting `outbox` retains immediate
delivery without durable retries. `:memory:` is refused for an outbox.

## Retry admission and quarantine

Successful ingestion is not permanent authorization to broadcast. Each retry
uses the same ingestion validator as a new observation, without inserting it
into the research store again. The current ingestion switch, provider allowlist,
schema, payload hash and timestamp checks must pass before the writer runs.
Freshness defaults remain 300 seconds of age and 60 seconds of future skew.

The bridge derives retry permissions from the authorized providers of its active
collectors. Removing a source or provider removes its retry permission. Unknown
sources and configurations with no authorized provider fail before any output.
An explicitly empty collector mapping leaves pending records untouched.

Disabling ingestion pauses delivery and retains pending records. Stale,
future-dated, unauthorized or malformed queued records are quarantined instead:
their `rejected_reason` is recorded durably, and later valid rows can proceed.
Quarantined records are excluded from subsequent drains and are not automatically
reactivated if policy later changes. Existing outbox databases gain the nullable
`rejected_reason` column when opened; existing pending and delivered rows remain.

Direct users of the outbox must now provide the current provider allowlist:

```python
delivered = outbox.drain(write_line, allowed_providers={"binance-public"})
```

`drain(write_line, *, allowed_providers, now=None, limit=100)` requires enabled
ingestion and a nonempty provider set even when the queue is empty. Its integer
limit must be between 1 and 1000 and bounds rows examined per call. `now` is an
optional timezone-aware test clock; normal operation checks the current time.
Enqueue callers remain responsible for successful local ingestion first.

## Counters and delivery boundary

`pending_count()` counts undelivered, non-quarantined rows. `rejected_count()`
counts all quarantined rows retained in the database. A bridge cycle reports
`outbox_rejected` for newly quarantined retries, separately from `rejected` for
newly collected observations refused at ingestion. `broadcast` counts successful
local writer calls, including retries.

A writer failure leaves that row pending and propagates to the caller. Use one
drainer per outbox database. A crash after writing but before marking delivery
can cause another local write on restart, so retries have at-least-once behavior.
A successful write acknowledges only the local stdin adapter; it does not prove
node acceptance, GossipSub publication, remote receipt or exactly-once delivery.

Research-store ingestion and outbox enqueue are separate commits. A crash between
them can leave a stored observation with no queued delivery; duplicate ingestion
on the next cycle does not repair that gap. The outbox provides retry persistence
once enqueue succeeds, not an atomic ingestion-and-publication transaction.

## Local regression checks

```bash
pip install -e 'services/quant-api[dev]'
pytest -q services/quant-api/tests/test_p2p_ingestion.py \
  services/quant-api/tests/test_observation_outbox.py \
  services/quant-api/tests/test_unified_observation_bridge.py \
  services/quant-api/tests/test_unified_outbox_policy.py
```

These synthetic local tests cover retry policy and persistence. Independent-host
delivery and public rollout retain the separate [P2P release gates](P2P_RELEASE_GATES.md).
