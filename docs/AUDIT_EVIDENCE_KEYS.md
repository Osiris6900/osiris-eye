# Audit evidence keys and anchors

Package 57 signs audit checkpoints and phase 18 evidence bundles with Ed25519.
The private key is not created by this repository and must not be committed.

## What is signed

- A checkpoint is the chain head hash, the sequence, the event count, and the
  time. It is written to a file outside the SQLite database.
- A phase 18 bundle signature covers the bundle hash. The hash covers every
  event.
- The paper book and the phase 16 journal are hash-chained on the server. Phase
  16 also stamps the actor and the timestamp. Caller sequence, actor, and hash
  fields are not the chain.
- `build_audit_chain` is still a caller-supplied calculator. Its
  `durable_storage` and `actor_authentication_verified` flags stay false. The
  fail-closed verifier reports that chain as `unverifiable`, never `ok`.

`ok` requires an intact chain, a server actor provisioned in
`OSIRIS_AUDIT_ACTOR`, and a signature that verifies against an external public
key. Anything missing is `unverifiable`. A broken link, a bad signature, or a
checkpoint that does not match the chain is `tampered`.

## Provision a key

Run this on the operator workstation. Leave the private key outside the
repository and off hosts that only verify.

```bash
python -c "from pathlib import Path; from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey; from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat; key = Ed25519PrivateKey.generate(); Path('audit-signing-key.pem').write_bytes(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())); Path('audit-verify-key.pem').write_bytes(key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo))"
chmod 600 audit-signing-key.pem
```

Point the process at those files. Both variables are unset by default. With
neither file set, checkpoints and bundles say `unsigned` and verification
returns `unverifiable`. The process does not invent a key and does not exit
because the variable is empty.

```bash
export OSIRIS_AUDIT_SIGNING_KEY_FILE=/var/lib/osiris/keys/audit-signing-key.pem
export OSIRIS_AUDIT_VERIFYING_KEY_FILE=/var/lib/osiris/keys/audit-verify-key.pem
export OSIRIS_AUDIT_ACTOR=osiris-quant-api
export OSIRIS_AUDIT_ANCHOR_PATH=/var/lib/osiris/anchors/phase16.json
```

`OSIRIS_AUDIT_ACTOR` is the service identity stamped into new phase 16 events
and into checkpoints. It is not read from the request body. If it is unset, the
actor is `service:quant-api` and `actor_authentication_verified` stays false.

The signing file may be a PKCS8 PEM or a 32-byte seed (raw or standard base64).
The verifying file may be a SubjectPublicKeyInfo PEM or the raw 32-byte public
key. A verifier does not need the private key. Do not put the private key on
the command line.

Accepted key paths are regular files. Symlinks are refused.

## Write and check an anchor

The anchor is a local JSON file. It can also be a copy of that file committed
in git. Commit only the public checkpoint. Never commit the signing key.

```bash
python scripts/verify_audit_evidence.py checkpoint \
  --chain paper \
  --database "$OSIRIS_PAPER_EXECUTION_DB" \
  --anchor /var/lib/osiris/anchors/paper.json

python scripts/verify_audit_evidence.py verify \
  --chain paper \
  --database "$OSIRIS_PAPER_EXECUTION_DB" \
  --anchor /var/lib/osiris/anchors/paper.json \
  --public-key /var/lib/osiris/keys/audit-verify-key.pem
```

Phase 16 appends refresh `OSIRIS_AUDIT_ANCHOR_PATH` when that variable is set.
`OSIRIS_AUDIT_CHECKPOINT_EVERY` is an optional event interval. Unset means
every committed append. `0` disables the automatic refresh. The paper book does
not refresh an anchor from inside its write path; run the checkpoint command
on a schedule. That keeps this package independent of the open paper-book
changes.

Exit status is `0` for `ok`, `1` for `tampered`, and `2` for `unverifiable`
or an unsigned checkpoint.

A rewrite that rebuilds a consistent chain from genesis still matches the
in-database hashes. It does not match a checkpoint that was signed earlier and
kept outside the database. Replacing the anchor file as well fails the
signature unless the attacker has the private key. A git history of the public
checkpoint shows that replacement even when the working copy was overwritten.

## RFC 3161

Each checkpoint carries an RFC 3161 stub: the SHA-256 imprint of the checkpoint
body, `tsa_contacted: false`, and no token. No timestamp authority is
contacted, and `trusted_timestamp_verified` stays false. A checkpoint that
claims a token or a contacted authority is not accepted as time evidence.

## Limits

This is not a hardware security module, a WORM volume, or a human identity
provider. Custody of the signing key is package 58. Hash integrity is not proof
that an event was true. `live_trading_authorized` and live execution stay false.
