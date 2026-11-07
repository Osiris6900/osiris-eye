# P2P dependency inventory and verification boundary

This is a source and lockfile review of the hardening branch, not a vulnerability
scan, license clearance, software bill of materials, or cryptographic audit.

## Rust resolution

`services/p2p-node/Cargo.lock` uses lock format 4 and records 362 packages:
the local node plus 361 crates.io packages. Every registry entry has a checksum.
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

## CycloneDX dependency inventory

`scripts/generate_dependency_sbom.py` generates deterministic CycloneDX 1.5 JSON
from the exact Cargo and npm lockfiles. For the reviewed inputs, the SBOM has
496 components: three local applications, 361 crates.io libraries and 132 npm
libraries. Cargo SHA-256 checksums, npm integrity hashes, distribution URLs,
license metadata and npm development scope are included. Duplicate SBOM
references, unsupported Cargo sources, missing licenses/integrity data and
crate checksum mismatches fail closed.

Crates.io exact-version API responses supply crate license metadata and are
checked against the lockfile checksum. The local cache permits later offline
regeneration; delete or refresh that cache when performing a new metadata
review. Generation is deterministic across checkout paths and online/offline
runs with the same inputs and cache.

The three local applications include `osiris-quant-api`, but its dependency
resolution is explicitly `unresolved`. Version ranges from `pyproject.toml` are
recorded only as declarations, not as installed packages. Therefore this SBOM
does not complete the Python deployment inventory and must not be represented
as a whole-environment SBOM until a supported resolved and hashed Python lock
is incorporated.

This inventory is not a license-compatibility or redistribution decision,
advisory scan, provenance attestation, reproducible-build proof, or independent
security assessment. Exact-version license metadata still requires operator
review against the actual distributed artifacts.

## CI action pinning ratchet

The workflow review found 59 remote `uses` references across eight workflow
files, all using mutable tags instead of immutable commit SHAs. The checked-in
`config/ci_action_pin_baseline.json` records this existing debt; it is not an
approval of the referenced actions or evidence that their tags are trustworthy.

| Mutable reference | Existing occurrences |
|---|---:|
| `actions/checkout@v4` | 14 |
| `actions/upload-artifact@v4` | 14 |
| `dtolnay/rust-toolchain@stable` | 11 |
| `actions/download-artifact@v4` | 8 |
| `tailscale/github-action@v3` | 7 |
| `actions/setup-python@v5` | 4 |
| `actions/setup-node@v4` | 1 |

The existing Python CI entry point runs `scripts/check_p2p_workflows.py` and its
regression tests. The offline audit covers step-level actions and job-level
reusable workflows. Repository-local actions are allowed, remote GitHub actions
require a full 40-character commit SHA, and Docker actions require a SHA-256
image digest. A new mutable reference, a changed tag, or an occurrence count
above its per-reference baseline fails closed. Reducing or eliminating baseline
debt passes without requiring a baseline update.

Actual pinning and review of all 59 references remain outstanding. The current
GitHub token cannot modify workflow files; this limitation must not be bypassed.
The ratchet is change control, not an advisory scan, provenance check, action
trust review, or independent security assessment.

## Reproduce and remaining checks

```bash
cargo metadata --manifest-path services/p2p-node/Cargo.toml --locked --offline --no-deps --format-version 1
cargo test --manifest-path services/p2p-node/Cargo.toml --locked --all-targets
cargo build --manifest-path services/p2p-node/Cargo.toml --locked --release
python -m pip check
python -m pip freeze
python scripts/check_p2p_workflows.py
python scripts/test_check_p2p_workflows.py
python scripts/test_generate_dependency_sbom.py
python scripts/generate_dependency_sbom.py --output /tmp/osiris-eye-sbom.json
python scripts/generate_dependency_sbom.py --offline --output /tmp/osiris-eye-sbom-offline.json
cmp /tmp/osiris-eye-sbom.json /tmp/osiris-eye-sbom-offline.json
```

The offline `--no-deps` metadata command passed during this review. Full offline
metadata could not complete because a platform dependency (`async-io 2.6.0`)
was absent from the local cache; this is a cache limitation, not a successful
full dependency inventory. Build/test evidence is recorded separately.

Before a public release, record current Rust/Python/JavaScript advisory scans,
triage reachable findings, lock and incorporate the supported Python deployment
environment, complete license/redistribution review, and record toolchain and
artifact hashes. Complete the actual third-party CI action SHA/digest pinning
and trust review before release; the ratchet alone does not clear that gate.
No advisory scanner was run in this task, so no “zero known vulnerabilities”
conclusion is supported.
