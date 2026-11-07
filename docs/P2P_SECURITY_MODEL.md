# OSIRIS EYE P2P Security Model

## Scope and evidence

The first release carries market-data observations for research and paper
trading. Peer messages cannot enable live trading or change local risk limits.
Broker credentials, account data, orders and execution commands must remain
outside this protocol. Encryption does not establish provider truth, market-data
rights or trading suitability.

This document describes implemented controls. Commit `ce16dbde` passed
[integrated CI](https://github.com/Osiris6900/osiris-eye/actions/runs/42423590055),
including local recipient E2EE, non-recipient rejection, core relay E2EE,
freshness, revocation/disconnect and signed receipt checks. At `974de9ce`,
[Tailscale cross-host observation delivery](https://github.com/Osiris6900/osiris-eye/actions/runs/42421052643)
also passed; that workflow does not enable recipient E2EE. Subsequent changes
require their own test results. Two-host and three-host recipient E2EE harnesses
and workflows exist; those independent-host E2EE gates remain open. See
[release gates](P2P_RELEASE_GATES.md) for evidence boundaries. No
independent cryptographic audit, public-fleet uptime or production certification
is claimed.

## Implemented controls

| Area | Implemented behavior | Boundary |
|---|---|---|
| Identity | Persistent Ed25519 identity, signed GossipSub and operator-triggered rotation | PeerId ownership is not a trust grant |
| Admission | Optional explicit trusted-PeerId allowlist; local and authority-signed revocations override trust | Configure allowlists and the authority key out of band; open mode exists |
| Transport | TCP, authenticated Noise and Yamux; explicitly configured Circuit Relay v2 support in the core node | Relay selection and reservation are operator configured; no automatic NAT discovery or hole punching |
| Application encryption | Opt-in per-recipient X25519/HKDF-SHA256/ChaCha20-Poly1305 envelopes signed with Ed25519 | Every participating node must enable E2EE; metadata remains visible |
| Key bindings | Signed PeerId/identity/key/epoch/expiry binding; verified remote bindings persisted in SQLite | Local epoch floors survive restart, not privileged rollback of all node state |
| Replay | Durable node-local journal before acceptance; malformed/full state fails closed | No network-wide deduplication or consensus |
| Observation integrity | Schema/metadata bounds, 1 MiB UTF-8 payload cap, recomputed SHA-256 and strict UTC timestamp syntax | Correct hashes and signatures do not prove observation truth |
| Freshness | Optional maximum observation age on send/receive, with 30-second future tolerance | Enable for operational paper-trading inputs; historical research mode does not reject old observations |
| Resource limits | Bounded messages, key store and revocation registry; finite core connections and relay reservations/circuits | Sustained hostile load and quota denial behavior still require acceptance evidence |

The local replay journal retains message IDs across restart and fails closed at
64 MiB. Do not delete or truncate it to recover capacity without an operator
retention/recovery design: doing so discards replay history. SQLite key bindings
reject lower epochs, conflicting keys at an existing epoch and corrupt indexed
records. These controls assume protected persistent local storage.

## Recipient E2EE acceptance path

1. libp2p authenticates the signed publishing identity.
2. Local trust and revocation policy admits the publisher before encrypted
   envelope processing.
3. The envelope must name this recipient and match the signed sender binding,
   key epochs and bounded validity window.
4. Verify the envelope signature and authenticated decryption.
5. Validate the observation schema, bounds, strict UTC timestamp and payload
   digest. With `OSIRIS_MAX_OBSERVATION_AGE_SECS` set to `1`–`86400`, reject
   observations older than that window or more than 30 seconds in the future.
   Without it, historical research timestamps remain permitted.
6. Persist the sender-scoped replay ID before accepting the validated
   observation. Rejected plaintext must not consume replay-journal capacity.
7. Keep received observations in the research/paper-trading data plane. They are
   not orders or execution authorization.

Provider provenance, independent corroboration, quarantine and downstream
point-in-time data handling remain necessary. Observation receipt logs are not a
durable research database or an independently retained audit trail.

## Relay security boundary

The separate relay server is opt-in using
`OSIRIS_RELAY_SERVER_ENABLED=true`; the core node requests a reservation only
when `OSIRIS_RELAY_ADDRESS` is set. Direct and relayed core connections share the
same Noise, peer-policy and application E2EE paths. Core logs identify relayed
connections; health includes reservation state.

The server applies finite reservation/circuit counts, per-peer limits, byte and
duration caps and libp2p rate limiters. A reservation is not evidence of public
reachability. The local relay/E2EE acceptance harness exercises core nodes, but
cross-network routing and relay abuse/denial paths need separate tests. Do not
operate a public relay on ephemeral CI infrastructure or serverless handlers.

## Key and revocation lifecycle

Keep Ed25519 identity files, X25519 transport keys and the revocation-authority
private signing key separate. Private keys and backups must never enter logs,
artifacts, messages or source control. Newly created node key files use owner-only
permissions on Unix; protect the containing directory and backups too.

For identity rotation, stop the node, preserve its `OSIRIS_NODE_KEY`, then start
once with `OSIRIS_ROTATE_NODE_KEY=1`. The old key is moved to a timestamped private
backup and a fresh identity is created. Remove the rotation flag before normal
restart and update allowlists/bootstrap configuration with the new PeerId.
Rotation does not automatically grant the new identity trust.

For X25519 rotation, replace the protected transport key and increment
`OSIRIS_TRANSPORT_KEY_EPOCH`; do not reuse an epoch for different key material.
Signed bindings refresh periodically and are verified against durable peer
history. See [key rotation and revocation runbook](P2P_KEY_ROTATION_REVOCATION_RUNBOOK.md).

`OSIRIS_REVOKED_PEERS` remains a local emergency denylist. Provision
`OSIRIS_REVOCATION_AUTHORITY_PUBLIC_KEY` to enable authority-signed revocation
records and the durable registry at `OSIRIS_REVOCATION_REGISTRY`. Accepted records
use monotonic per-peer sequences, persist atomically, propagate through bounded
GossipSub synchronization and disconnect revoked peers. This is eventual
operator-authorized synchronization, not consensus, quorum authorization or
threshold authority rotation. See [revocation protocol](GLOBAL_REVOCATION_PROTOCOL.md).

## Remaining release gates

- Retain passing two-host and three-host recipient-only E2EE evidence for the
  exact release commit, with public identifiers and sanitized logs.
- Verify relay delivery across separate networks with direct dialing blocked;
  test reservation expiry, reconnect, quotas and rejection paths.
- Verify key rotation/revocation recovery after restart, partitions and offline
  node return; test sustained load and bounded resource behavior.
- Enforce provider provenance and suitable freshness rules before paper-trading
  decisions; independently validate downstream risk and audit handling.
- Complete independent security review and cryptographic audit before public
  production claims.

The v1 ephemeral-static construction has no ratchet and does not provide forward
secrecy against later compromise of a recipient's long-term X25519 key. Sender and
recipient identifiers, timing, sizes and topic membership remain observable.
