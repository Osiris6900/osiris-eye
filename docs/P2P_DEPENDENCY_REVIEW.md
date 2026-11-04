# P2P dependency inventory and verification boundary

This is a source and lockfile review of the hardening branch, not a vulnerability
scan, license clearance, software bill of materials, or cryptographic audit.

## Rust resolution

`services/p2p-node/Cargo.lock` uses lock format 4 and records 356 packages:
the local node plus 355 crates.io packages. Every registry entry has a checksum.
No Git dependencies, alternate registries, or manifest `[patch]` overrides were
found. These facts describe the configured sources; they do not establish that
the dependencies are free of vulnerabilities.

| Component | Locked version |
|---|---|
| libp2p | 0.56.0 |
| libp2p-noise | 0.46.1 |
| libp2p-identity | 0.2.14 |
| snow | 0.9.6 |
| ed25519-dalek | 2.2.0 |
| x25519-dalek | 2.0.1 |
| chacha20poly1305 | 0.10.1 |
| hkdf | 0.12.4 |
| sha2 | 0.10.9 |
| rusqlite | 0.32.1 |

The CI node build and tests use `--locked`, so dependency resolution cannot
silently update this lockfile during those commands. Commit the lockfile with
the implementation. A floating Rust `stable` toolchain and platform/native
build inputs still prevent a claim of byte-for-byte reproducible binaries.
The node package declares `UNLICENSED`; dependency licensing and redistribution
terms require a separate inventory and operator review before distribution.

The current hardening work does not require a cryptographic dependency migration.
Any future primitive or library migration needs compatibility vectors, adverse
input tests and explicit protocol/version review before rollout.

## Python resolution

The API declares Python 3.11+ and bounded major versions in `pyproject.toml`,
including `cryptography>=42,<47`, `fastapi>=0.110,<1`, `pydantic>=2.6,<3`,
and `numpy>=1.26,<3`. Its development range includes `pytest>=8,<9`.
These are version constraints, not a resolved, hashed Python lockfile. Retain
the exact installed environment with each release's test evidence; results from
dependencies outside the declared ranges do not validate the supported set.

## Reproduce and remaining checks

```bash
cargo metadata --manifest-path services/p2p-node/Cargo.toml --locked --offline --no-deps --format-version 1
cargo test --manifest-path services/p2p-node/Cargo.toml --locked --all-targets
cargo build --manifest-path services/p2p-node/Cargo.toml --locked --release
python -m pip check
python -m pip freeze
```

The offline `--no-deps` metadata command passed during this review. Full offline
metadata could not complete because a platform dependency (`async-io 2.6.0`)
was absent from the local cache; this is a cache limitation, not a successful
full dependency inventory. Build/test evidence is recorded separately.

Before a public release, record a current advisory scan for Rust and Python,
triage reachable findings, generate a complete dependency/license inventory,
lock the supported Python deployment environment, and record toolchain and
artifact hashes. Pin and review third-party CI action revisions as part of the
release process. No advisory scanner was run in this task, so no “zero known
vulnerabilities” conclusion is supported.
