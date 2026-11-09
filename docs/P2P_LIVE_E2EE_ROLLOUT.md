# Live P2P E2EE Integration (Opt-in)

## Implemented status

The Rust/libp2p core node contains a live, opt-in recipient-addressed encryption
path. It is **disabled by default**; Noise transport encryption operates
independently. The release scope is market-data research and paper trading.

E2EE uses ephemeral-static X25519 agreement, HKDF-SHA256,
ChaCha20-Poly1305 and the sender's Ed25519 signature. Each recipient gets a
separate envelope on `osiris/e2ee/messages/1`. Signed public X25519 bindings on
`osiris/e2ee/key-bindings/1` bind the identity, PeerId, key epoch and expiry.
Verified remote bindings are stored durably in SQLite; lower epochs and
conflicting same-epoch keys remain rejected across process restarts.

With `OSIRIS_E2EE_ENABLED=1`:

- outbound observations are encrypted separately for configured recipients;
- each recipient needs a valid signed key binding and local peer-policy approval;
- incoming envelopes must target this node, match the authenticated sender and
  stored key binding, and pass epoch, time, signature, AEAD and durable replay
  checks;
- legacy plaintext observation messages are rejected.

When the flag is absent or `0`, legacy observations remain plaintext at the
application layer inside Noise transport connections. Other values fail startup.
Do not assume network-wide E2EE without verifying every participating node's
configuration. Relayed core connections use this same application path when
E2EE is enabled.

## Configuration

Configure each node before sending observations:

- `OSIRIS_E2EE_ENABLED=1`.
- `OSIRIS_E2EE_RECIPIENTS=<comma-separated PeerIds>` or
  `OSIRIS_E2EE_RECIPIENTS_FILE=<path>` on publishers. The file is reread for each
  observation; protect it from unauthorized changes.
- `OSIRIS_TRANSPORT_KEY=<private key file>`; default `.osiris-transport-key`.
- `OSIRIS_TRANSPORT_KEY_EPOCH=<positive integer>`; default `1`. Increment it when
  changing the transport key.
- `OSIRIS_PEER_KEY_STORE=<persistent SQLite path>`; default
  `.osiris-peer-keys.sqlite3`.
- `OSIRIS_REPLAY_CACHE=<persistent journal path>` for durable replay history.
- `OSIRIS_MAX_OBSERVATION_AGE_SECS=<1..86400>` to enforce observation freshness
  on send/receive, with a 30-second future tolerance. Without this setting, strict
  UTC timestamp syntax still applies but historical research data is allowed.
- `OSIRIS_NODE_KEY`, explicit `OSIRIS_TRUSTED_PEERS`, local
  `OSIRIS_REVOKED_PEERS` and an out-of-band verified revocation authority where
  signed synchronization is required.

New transport private keys use owner-only permissions on Unix. Keep private
keys, replay history and peer-key epochs on protected persistent storage and
securely back them up. Private key material never belongs in test artifacts or
source control. See [peer key storage](P2P_PEER_KEY_STORAGE.md) and
[key rotation/revocation](P2P_KEY_ROTATION_REVOCATION_RUNBOOK.md).

## Validation and rollout gates

At baseline `48755ef3`, local process tests passed recipient delivery,
non-recipient discard, replay/security cases and live revocation enforcement.
The hardening branch adds core relay support, stricter validation and workflow
repair; it requires fresh test evidence for its exact commit.

Two-host and three-host E2EE SSH harnesses and manual GitHub workflows are
implemented. Passing runs exist only for older commits, and those runs did not
retain node binary SHA-256. See the evidence table in
[P2P_SECURITY_MODEL.md](P2P_SECURITY_MODEL.md). `main` at
`2036888bbada42213a221526914b09d50fc1f1df` has no successful recipient-only
two-host or three-host run. The harness now records binary SHA-256, boot-id
fingerprints, commit, payload digest, and a measurement class.
`local_or_same_host` must not be described as independent-host evidence.
`distinct_boot_id_hosts` means the boot-id fingerprints differed. Workflow
repair, a green compile, or a same-host process test does not complete the
gate. The local relay/E2EE harness does not prove public Internet/NAT
reachability.

An independent cryptographic audit has not been done. Operational
load/recovery review remains outstanding. Do not describe the network as
production-ready, independently audited, or globally deployed from source code
or local CI alone.

## Security boundaries

- No ratchet or forward secrecy against later recipient X25519-key compromise is
  claimed by v1.
- Envelope contents are protected; identity/recipient metadata, timing, sizes and
  topic membership are not hidden.
- Key ownership is not a trust grant or proof of provider truth.
- Local replay/epoch state is not a global consensus database or protection from
  a privileged rollback of all node state.
- Broker credentials, account data, orders, execution commands and other secrets
  remain outside this observation protocol. Live trading is disabled.
