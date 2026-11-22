# Secrets and key management

Package 58 adds versioning, rotation, and startup checks to the existing
control-plane secrets module. It does not create a key, place an order, or
enable live trading. `live_trading_authorized` and `LIVE_EXECUTION_ENABLED`
stay false.

No private key belongs in this repository. The commands below write a key ring
on the operator host. Do not commit that file, paste it into chat, or pass the
key material as a command-line argument.

## What is loaded

One interface reads a key ring from either source:

- `env:OSIRIS_KEY_RING`, the JSON document in that variable
- `file:` plus the path in `OSIRIS_KEY_RING_FILE`

Each entry has a purpose, an explicit key id, a positive version, and a
status of `active` or `retired`. Setting both sources, or setting either
source together with `OSIRIS_CONTROL_MASTER_KEY`, is refused.

Purposes:

| Purpose | Algorithm | Use |
| --- | --- | --- |
| `identity-master` | AES-256-GCM, 32 bytes | Encrypts control-plane secrets, including authenticator seeds |
| `audit-signing` | Ed25519 seed, 32 bytes | Signs evidence. Verification accepts retired versions |
| `api-token` | Opaque text | Active token is the one to issue. Retired tokens still match |
| `broker-credential` | Opaque text | Same read rules as `api-token` |

`api-token` and `broker-credential` are stored and checked by this layer. The
paper API middleware and the broker adapters still read their existing
environment variables, so paper mode does not require this ring.

A single identity master key can still be supplied without a file:

```bash
export OSIRIS_CONTROL_MASTER_KEY_ID=identity-master-1
export OSIRIS_CONTROL_MASTER_KEY_VERSION=1
export OSIRIS_CONTROL_MASTER_KEY=<standard-base64-of-32-random-bytes>
```

The value is not a key id. A strong key without an id and version is refused
with `key_id_required`. This path cannot rotate. Use a key ring file for that.

## Provision

Run these on the operator host. The CLI generates the key material, writes a
new file with mode `0600`, and prints key ids and versions only.

```bash
install -d -m 0700 /var/lib/osiris/keys

python -m osiris_api.control_plane.cli keys --action init \
  --purpose identity-master \
  --key-id identity-master-1 \
  --output /var/lib/osiris/keys/ring.json

python -m osiris_api.control_plane.cli keys --action init \
  --purpose audit-signing \
  --key-id audit-signing-1 \
  --from-ring /var/lib/osiris/keys/ring.json \
  --output /var/lib/osiris/keys/ring-with-audit.json
```

Point the process at the file that contains every purpose you need:

```bash
export OSIRIS_KEY_RING_FILE=/var/lib/osiris/keys/ring-with-audit.json
python -m osiris_api.control_plane.cli keys --action validate
```

`validate` exits 0 when no key source is configured. That is normal for a
paper-only process. It exits 2 when a configured source is invalid. The error
text is a code such as `weak_key` or `key_file_world_readable`, not the key.

Accepted files are regular files. Symlinks are refused. World-readable and
world-writable files are refused. A key that is short, low-entropy, a
placeholder (`changeme`, `placeholder`, `password`, and similar samples), or
reused for two purposes is refused.

## Rotate the identity master key

Rotation writes a new ring. The previous active key stays in the file as
`retired`. New ciphertext uses the new active key. Ciphertext sealed by the
retired key still opens. Then re-encrypt so the database no longer needs the
retired key.

```bash
python -m osiris_api.control_plane.cli keys --action rotate \
  --purpose identity-master \
  --key-id identity-master-2 \
  --from-ring /var/lib/osiris/keys/ring-with-audit.json \
  --output /var/lib/osiris/keys/ring-rotated.json

export OSIRIS_KEY_RING_FILE=/var/lib/osiris/keys/ring-rotated.json
python -m osiris_api.control_plane.cli keys --action reencrypt \
  --database /var/lib/osiris/control.sqlite3
```

Keep the retired key until `reencrypt` has succeeded. After that, a later ring
may omit it. Omitting it makes older ciphertext undecryptable. `reencrypt`
stops rather than skipping rows when 1000 secrets are stored. It prints counts
and key ids, not plaintext.

## Rotate the audit signing key

Use the same `rotate` action with `--purpose audit-signing` and a new key id.
Signatures name the key id and version that produced them. Verification uses
that version, including a retired one. New signatures are produced only with
the active version. Keep every retired signing key that still has signatures
to verify.

Package 57's single-file signing key is a separate, unmerged change. This
package does not read that file. Callers on this branch use `sign_evidence`
and `verify_evidence_signature`.

## Startup and paper mode

`validate_startup_keys` is not called from paper execution. An unset ring, and
a ring the paper path never reads, does not stop paper marks or paper orders.
Control-plane identity reads the ring when an enroll, login, or secret write
needs it, and fails that request when the configured key is invalid.

## What is not enabled

`ExternalKmsAdapter` always raises `external_kms_disabled`. It does not open a
socket. `VaultSecrets` remains an explicit HTTPS client that the caller must
construct. Startup does not contact Vault, a KMS, or any other network
service.

## Committed-secret check

```bash
python scripts/scan_committed_secrets.py
```

The command fails when a tracked file contains a private-key body, a known
token prefix (`ghp_`, `github_pat_`, `AKIA`, Slack `xox*`, `sk_live_`), or a
dotenv filename other than `*.example`. CI runs this command. The report lists
the kind and path and does not print the secret.
