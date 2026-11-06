# Verified Rust receipt → Python ledger

This bridge records one verified recipient-admission receipt in the Python
SQLite ledger. It does not retransmit observations, change the outbox
`delivered` flag, or claim end-to-end delivery.

## Inventory before this wiring

These boundaries were already in the tree. Their trust assumptions are the
reason a stdout line or a boolean is not a receipt.

| Path | Role then | Trust limit |
| --- | --- | --- |
| `services/p2p-node/src/delivery_receipt.rs` | `create_receipt` signs after the caller has decrypted, validated, and durably admitted the replay id. `PendingReceipts::accept` checked schema, local sender, GossipSub source, key-binding identity, signature, message id, envelope SHA-256, freshness, and the in-memory pending attempt. | Admission allow/revoke policy was enforced by `services/p2p-node/src/main.rs` before `accept`. Pending state was process memory. Success was only a log line. |
| `services/p2p-node/src/main.rs` | On topic `osiris/e2ee/receipts/1`, rejected a missing source or a peer outside the allow/revoke policy, verified the key binding, then called `accept`. | `observation_delivery_acknowledged` was an in-process log. Any local writer can forge that line. |
| `services/quant-api/src/osiris_api/recipient_receipt_ledger.py` | SQLite rows `pending`, `unconfirmed`, and `acknowledged`. | `acknowledge_verified(verified=True)` treated the caller's boolean as proof. |
| `services/quant-api/src/osiris_api/verified_receipt_event.py` | Schema checks. | `authenticated_rust_peer=True` was also a caller boolean, not a transport check. |
| `services/quant-api/src/osiris_api/verified_receipt_consumer.py` | One SQLite transaction for the event id and the correlated row. | It ran only if the caller passed that boolean. It did not ack a transport. |
| `services/quant-api/src/osiris_api/verified_receipt_unix.py` | `SO_PEERCRED` on an already accepted socket. | No listener, no spool, no ack, and no Rust sender. The boolean was then set inside the process. |
| `services/quant-api/src/osiris_api/observation_outbox.py` | Local stdin delivery only. | Unchanged. It must not be driven by this bridge. |
| `services/quant-api/src/osiris_api/p2p_routes.py` | Operator identity enrollment. | No receipt route. |

`docs/P2P_DELIVERY_RECEIPTS.md` describes the GossipSub receipt itself.
Topic `osiris/e2ee/receipts/1`, schema `osiris.p2p.delivery-receipt.v1`.

## Trust boundary now

Rust emits a verified event only from `PendingReceipts::verify`, which returns
`Ok` only when all of the following hold:

- admission policy flags for the authenticated GossipSub source and the
  propagation peer are both true (the node sets them from its allow and revoke
  lists, not from receipt fields);
- schema, local sender, source peer, and key-binding identity match;
- the recipient signature verifies over the domain-separated receipt;
- message id and lowercase envelope SHA-256 are well formed and match the
  pending attempt;
- the receipt time and the in-memory attempt are inside the existing windows.

`verify` does not remove the pending attempt. The node appends the event to the
SQLite spool and commits that transaction before `commit_verified` removes the
attempt. The spool row is marked acked only after the listener returns
`applied` or `duplicate`.

The event schema is `osiris.p2p.verified-receipt-event.v1`. Fields: `schema`,
`event_id`, `sender_peer_id`, `recipient_peer_id`, `message_id`,
`envelope_sha256`, `accepted_at`, `provenance`. Provenance is exactly
`rust.delivery_receipt.accept.v1`. `event_id` is the SHA-256 of those verified
fields under the domain `OSIRIS-EYE-VERIFIED-RECEIPT-EVENT-v1`, so a retry names
the same ledger row. There is no `verified` field. An extra field, including
`verified: true`, is rejected.

Python correlates `(message_id, recipient_peer_id, envelope_sha256)` with one
registered row. A missing row, a different digest, a stale time, or a conflicting
event id does not change state and does not produce a transport ack.

### Application ack versus transport dedup

| Outcome | Ledger | Bytes after the commit |
| --- | --- | --- |
| `applied` | `pending` or `unconfirmed` becomes `acknowledged` once | ack disposition `applied` |
| `duplicate` | unchanged; the same event id and tuple are already stored | ack disposition `duplicate` |
| mismatch, unknown row, conflict, bad frame, bad peer | unchanged, transaction rolled back | no ack frame |
| SQLite error, including an aborted insert | rolled back to the pre-event state | no ack frame |
| process stops after commit and before the ack write | row stays acknowledged | Rust keeps the spool row and retries; the retry is `duplicate` |

`duplicate` is transport deduplication. It is not a second receipt
acknowledgement and it does not authorize retransmission. `pending` and
`unconfirmed` remain different until an `applied` event. The outbox is not
consulted.

## Channel and supervisor setup

Linux only. The channel is a filesystem `AF_UNIX` `SOCK_STREAM` socket. Abstract
namespace sockets are rejected.

Both processes run as the same service user. The supervisor creates a directory
owned by that user and mode `0700`, then:

1. Starts the Python listener and binds
   `bind_verified_receipt_listener(socket_path)` inside that directory (mode
   `0600`). The listener PID is this process.
2. Starts the Rust node with that listener PID and the socket and spool paths.
   The Rust PID is known as soon as the process exists.
3. Passes the Rust PID into `serve_verified_receipts` or
   `accept_one_verified_receipt`. The PID is not read from event JSON.

Rust configuration, all three or none:

```bash
export OSIRIS_DELIVERY_RECEIPTS=1
export OSIRIS_VERIFIED_RECEIPT_SOCKET=/run/osiris/receipts/receipt.sock
export OSIRIS_VERIFIED_RECEIPT_SPOOL=/run/osiris/receipts/spool.sqlite3
export OSIRIS_VERIFIED_RECEIPT_LISTENER_PID=<python-listener-pid>
```

`OSIRIS_DELIVERY_RECEIPTS=1` still requires `OSIRIS_E2EE_ENABLED=1`. A partial
bridge setting refuses startup. With the bridge unset, the node keeps the
previous log-only behavior and does not open a socket.

On connect, Rust checks `SO_PEERCRED` and writes the frame only when the peer
PID is `OSIRIS_VERIFIED_RECEIPT_LISTENER_PID` and the peer UID is the node's
euid. Python checks `SO_PEERCRED` before parsing and continues only when the
peer PID and UID are the pinned Rust identity. The frame is a 4-byte big-endian
length plus at most 8192 bytes of JSON. The ack uses schema
`osiris.p2p.verified-receipt-ack.v1`.

## Failure, durability, and replay

Spool and ledger use SQLite with `synchronous=FULL`. A crash before commit
rolls the transaction back. A crash after commit keeps the row. The node flushes
up to eight unacked spool rows about every five seconds and once immediately
after verification. I/O on that path is bounded by a one-second timeout in the
node and does not mark a row acked on timeout, reset, mismatch, or a non-durable
disposition.

| Failure | What remains | What is not claimed |
| --- | --- | --- |
| Signature, peer, digest, time, replay, or policy check fails | in-memory attempt unchanged; nothing spooled | no ledger event and no socket write |
| Spool commit fails | pending attempt remains | `observation_delivery_acknowledged` is not logged |
| Spool commits, ledger is down or the ack is lost | spool row stays unacked and is retried | `verified_receipt_ledger_confirmed` is not logged |
| Event arrives before Python `register` | spool unacked; ledger has no acknowledged row | retry after register can `applied` |
| Same event delivered again after commit | one acknowledged row | transport result `duplicate` |
| Listener or Rust process restarts | spool file still holds unacked events | the old PID is rejected until the supervisor re-pins both PIDs |
| Corrupt spool row | flush stops on that row and leaves it | the row is not deleted and is not treated as acked |

`observation_delivery_unconfirmed` is still the in-memory timeout. It does not
mean the peer rejected the observation and it does not enqueue a retry.

## Threat limits

- A process with the service user's credentials can rewrite the spool or the
  ledger file. The socket mode and PID pin stop other local UIDs and other PIDs.
  They do not stop a compromised service user.
- `SO_PEERCRED` PIDs are reused by the kernel. After either process exits, the
  supervisor must pin the new PIDs. A stale pin fails closed; it does not accept
  the new process, and unacked spool rows stay unacked.
- Python does not verify the receipt signature again. The cryptographic check
  happened in Rust. The channel authenticates which process is allowed to
  deliver that conclusion.
- Provenance names the checks above. It does not mean the observation was stored,
  executed, or delivered exactly once. A compromised recipient can still sign a
  false admission; that limitation is unchanged from the GossipSub receipt.
- In-process Python that can import the ledger can read the SQLite file. The
  closed `acknowledge_verified` method rejects `verified=True` so application
  code and HTTP clients cannot use that flag. There is no receipt HTTP route.
- The bridge is Linux-specific. Other platforms refuse the configuration.

## Tests

Rust unit tests cover verifier policy, stable event ids, spool commit/rollback,
wrong listener PID, refused sockets, and non-durable acks. Python tests cover
correlation, duplicates, out-of-order registration, aborted inserts, a crash
between commit and ack, direct `verified=True`, and a different local PID.
`scripts/test_verified_receipt_rust_bridge.py` runs the real verifier binary
against the Python ledger.

```bash
cargo test --locked --manifest-path services/p2p-node/Cargo.toml --all-targets
python scripts/test_verified_receipt_rust_bridge.py
pytest -q services/quant-api/tests/test_verified_receipt_event.py \
  services/quant-api/tests/test_verified_receipt_consumer.py \
  services/quant-api/tests/test_verified_receipt_unix.py \
  services/quant-api/tests/test_recipient_receipt_ledger.py
```
