# Durable P2P Peer Key Binding Store

The Rust P2P node persists **verified remote public key bindings** in SQLite so
that a restart does not reset the last accepted key epoch.

## Configuration

- `OSIRIS_PEER_KEY_STORE`: optional path to the SQLite database.
- Default: `.osiris-peer-keys.sqlite3` in the node working directory.
- Keep the database on persistent local storage and include it in the node's
  protected backup policy. The database contains public identity/key-binding
  records, not private transport keys.
- The node transport private key remains in the separate
  `OSIRIS_TRANSPORT_KEY` file and must retain restrictive file permissions.

## Acceptance rules

A key-binding announcement is stored only after the node verifies its signed
binding, derived PeerId, validity window, configured peer allowlist, and local
revocation policy. The store then applies an atomic SQLite transaction:

- first valid binding for a peer is inserted;
- a strictly higher key epoch rotates the stored public transport key;
- a lower epoch is rejected, including after process restart;
- a same-epoch refresh is accepted only when the identity and transport public
  keys are unchanged;
- a conflicting key at the same epoch is rejected;
- capacity is bounded at 4,096 peers;
- database open/read/write failures fail closed for the affected operation.

SQLite uses WAL mode, FULL synchronous durability, a five-second busy timeout,
and an immediate transaction for epoch check-and-update. On Unix, the database
file is restricted to mode 0600. Concurrent writers are serialized by SQLite.

## Security boundary and remaining work

This store prevents an observed epoch rollback from being forgotten on process
restart. It does **not** establish peer trust by itself, distribute a global
trust root, protect against a compromised node identity key, or provide secure
private-key custody. The caller must verify the signed key binding and local
authorization before persisting it.

The next security step is a complete key-rotation/revocation lifecycle, followed
by cross-host acceptance tests using independent hosts. Local multi-process CI
is not proof of public-internet reachability or production readiness.
