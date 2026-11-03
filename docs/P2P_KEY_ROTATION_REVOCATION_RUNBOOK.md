# P2P Key Rotation and Revocation Runbook

This runbook describes the current operator-controlled lifecycle. It is not an
automatic rotation service and does not make the P2P network production-ready.

## Identity and transport keys are different

- The libp2p node identity determines the stable PeerId. Keep its private file
  unchanged during an ordinary E2EE transport-key rotation.
- The X25519 transport private key is read from `OSIRIS_TRANSPORT_KEY`
  (default: `.osiris-transport-key`). It is exactly 32 raw bytes and must be
  mode `0600` or stricter on Unix.
- `OSIRIS_TRANSPORT_KEY_EPOCH` is a positive integer. It defaults to `1`.
  Peers persist the highest verified epoch they have accepted and reject lower
  epochs after restart.

## Planned transport-key rotation

1. Stop the node cleanly and back up the current transport-key file securely.
2. Generate a new 32-byte random transport private key in a new file. Do not
   overwrite the old file before the new file is complete.
3. Set the new file owner to the node service account and mode `0600`.
4. Atomically replace the configured `OSIRIS_TRANSPORT_KEY` file with the new
   file.
5. Increase `OSIRIS_TRANSPORT_KEY_EPOCH` by exactly one or more. Never reuse
   or decrease an epoch, including after restoring a backup.
6. Restart the node and confirm its startup log reports the intended epoch.
   Confirm peers accept the new signed binding and E2EE messages work.
7. Retain the old key backup only under the approved secret-retention policy;
   destroy it when recovery policy permits.

Example on a Unix host (run as the node service account; substitute the actual
configured path and epoch):

```sh
umask 077
openssl rand -out /secure/path/transport-key.next 32
chmod 600 /secure/path/transport-key.next
# Stop the service before this atomic replacement.
mv /secure/path/transport-key.next /secure/path/transport-key
# Set OSIRIS_TRANSPORT_KEY_EPOCH to the previous epoch + 1 in the service config.
```

Do not rotate the libp2p identity key as part of this procedure. Identity-key
rotation changes the PeerId and requires a separate trust enrollment and
revocation plan.

## Revocation state

- Configure `OSIRIS_REVOCATION_AUTHORITY_PUBLIC_KEY` with the trusted Ed25519
  authority public key (64 hexadecimal characters).
- Configure `OSIRIS_REVOCATION_REGISTRY` to a persistent local path. The
  default is `.osiris-revocations.json`.
- Revocation records are authority-signed and carry a strictly increasing
  per-peer sequence. The registry keeps the latest record as a durable
  tombstone; do not delete a tombstone to "unrevoke" a peer.
- A higher sequence is required to supersede an older record. The current
  protocol treats a record as a revocation, not a timed lease; expiry is not
  used to restore trust.
- Back up the registry as integrity-sensitive state. A missing or rolled-back
  registry can forget a previously observed revocation; restore only from a
  trusted backup and reconcile with the authority before bringing the node
  online.
- If durable persistence fails, the new revocation is rejected and is not
  published into the running policy. Fix storage and retry the signed record.

## Operational checks and limits

- Keep identity keys, transport private keys, and the revocation signing
  authority private key out of the peer-key SQLite database and out of P2P
  messages.
- A peer's locally stored transport-key epoch prevents rollback only on nodes
  that have already observed and persisted a higher epoch.
- Revocation sync distributes authority-signed records; it is not a global
  consensus service and does not remove the need for local trusted-peer policy.
- Cross-host key rotation and revocation acceptance still require independent
  host testing. Local CI is not evidence of public-network readiness.
- Do not enable live broker credentials, order routing, or production trading
  based solely on these P2P checks.
