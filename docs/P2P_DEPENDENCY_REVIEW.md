# P2P dependency inventory and verification boundary

This document combines a source and lockfile review with a point-in-time OSV
advisory scan. It is not license clearance, a complete software bill of
materials, reachability proof, or a cryptographic/independent security audit.

## Rust resolution

`services/p2p-node/Cargo.lock` uses lock format 4 and records 389 packages:
the local node plus 388 crates.io packages. Every registry entry has a checksum.
No Git dependencies, alternate registries, or manifest `[patch]` overrides were
found. These facts describe the configured sources; they do not establish that
the dependencies are free of vulnerabilities.

| Component | Locked version |
|---|---|
| libp2p | 0.57.1 |
| libp2p-noise | 0.47.0 |
| libp2p-identity | 0.3.1 |
| libp2p-yamux | 0.48.0 |
| hickory-proto / hickory-resolver | 0.26.3 |
| yamux | 0.14.1 |
| snow | 0.10.0 |
| ed25519-dalek (direct node dependency) | 2.2.0 |
| x25519-dalek (direct node dependency) | 2.0.1 |
| chacha20poly1305 | 0.10.1 |
| hkdf (direct node dependency) | 0.12.4 |
| sha2 (direct node dependency) | 0.10.9 |
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
including `cryptography>=50,<51`, `fastapi>=0.110,<1`, `pydantic>=2.6,<3`,
and `numpy>=1.26,<3`. Its development range includes `pytest>=8,<9`.

The supported Python deployment environment is resolved and hashed in
`services/quant-api/requirements-deploy.lock`, generated with
`pip-compile --generate-hashes` from `pyproject.toml` with the runtime extras
`[ibkr]` and `[streams]`; development-only tooling (the `[dev]` extra) is not
part of the deployment environment. Every locked entry is an exact
`name==version` pin with canonical PyPI SHA-256 artifact hashes. `numpy` is
held below 2.4 because numpy 2.4+ requires Python >= 3.12 while the project
declares `requires-python >= 3.11`.

The declared `cryptography` floor moved from `>=42,<47` to `>=50,<51` after the
first resolved PyPI scan flagged four advisory groups against the initially
resolved `cryptography 46.0.7` (PKCS#7 Bleichenbacher oracle, wildcard DNS
escape from `permittedSubtrees`, exponential path building, and a vulnerable
OpenSSL bundled in wheels; fixed in 48.0.1–50.0.0). OSIRIS call sites use only
the Ed25519/X25519/ChaCha20Poly1305/HKDF hazmat APIs, not the affected PKCS#7
or X.509 path-building code; that is local configuration evidence, not an
external exploitability audit. Results from dependencies outside the declared
ranges do not validate the supported set.

## CycloneDX dependency inventory

`scripts/generate_dependency_sbom.py` generates deterministic CycloneDX 1.5 JSON
from the exact Cargo, npm and Python lockfiles. For the reviewed inputs, the
SBOM has 551 components: three local applications, 388 crates.io libraries,
132 npm libraries and 28 PyPI libraries. Cargo SHA-256 checksums, npm integrity
hashes, PyPI artifact hashes, distribution URLs, license metadata and npm
development scope are included. Duplicate SBOM references, unsupported Cargo
sources, missing licenses/integrity data, crate checksum mismatches, unpinned
Python lines and Python pins without SHA-256 hashes fail closed.

Crates.io exact-version API responses supply crate license metadata and are
checked against the lockfile checksum. The local cache permits later offline
regeneration; delete or refresh that cache when performing a new metadata
review. Generation is deterministic across checkout paths and online/offline
runs with the same inputs and cache.

The three local applications include `osiris-quant-api`, whose dependency
resolution is now `resolved-hashed-lock`: the Python deployment libraries are
inventoried from `services/quant-api/requirements-deploy.lock` with their
canonical PyPI artifact hashes. Declared version ranges from `pyproject.toml`
are retained as declarations for drift detection.

This inventory is not a license-compatibility or redistribution decision,
provenance attestation, reproducible-build proof, or independent security
assessment. Exact-version license metadata still requires operator review
against the actual distributed artifacts.

## OSV advisory evidence

`scripts/scan_dependency_advisories.py` queries OSV for every exact registry
package in the Cargo, npm and Python deployment lockfiles and writes
`docs/P2P_DEPENDENCY_ADVISORIES.json`. The checked-in report records the source
commit, input SHA-256 hashes, query timestamp, package counts, matched advisory
IDs, aliases and selected OSV record fields. Its offline validation fails when
the report's source identity, timestamp, input hashes, package coverage, finding
IDs/classifications or derived summary counts are stale or internally
inconsistent. Validation also fails closed when the Python deployment lock no
longer covers every declared deployment requirement or a locked version falls
outside its declared range. The checks derive expected counts from the current
lock inputs; they do not require a fixed number of advisories after remediation.

The live query at `2026-11-07T23:43:30Z` covered 548 packages at source commit
`ce16dbde1e7b2377c82e5f14622238e60c04ff07`: 388 crates.io, 132 npm and 28 PyPI
packages. One raw match was normalized into one alias-aware maintenance-only
advisory group affecting one Cargo package. No vulnerability group, npm match
or PyPI match was returned in this point-in-time query.

| Package | Classification | Advisory group IDs |
|---|---|---|
| paste 1.0.15 | maintenance-only | RUSTSEC-2024-0436 |

`config/osv_maintenance_only.json` explicitly classifies the `paste` record as
an unmaintained-package warning. It is retained as maintenance evidence rather
than counted as proof of an exploitable vulnerability. Unknown or unclassified
records default to the vulnerability classification; they are not suppressed.

The coordinated libp2p 0.57.1 remediation resolves to Hickory 0.26.3 and Yamux
0.14.1 and removes Yamux 0.12.1 and 0.13.10 from the lock graph. The five
vulnerability groups in the preceding report no longer match this remediated
graph. That result does not prove the old Yamux compatibility path was
reachable: OSIRIS call sites use `yamux::Config::default()`, and the reviewed
libp2p-yamux 0.47 default selected Yamux 0.13 rather than its customized 0.12
compatibility path. This is local configuration evidence, not an external
exploitability audit.

Python deployment dependencies are scanned as a resolved PyPI environment from
the exact hashed lock; development-only tooling stays out of scope. OSV matching
is point-in-time advisory evidence, not proof of exploitation, a universal
vulnerability-free result, or an independent audit. The broader dependency gate
remains open for license/redistribution and artifact-provenance review.

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
python scripts/test_scan_dependency_advisories.py
python scripts/scan_dependency_advisories.py \
  --source-commit ce16dbde1e7b2377c82e5f14622238e60c04ff07 \
  --output docs/P2P_DEPENDENCY_ADVISORIES.json
```

Regenerate the Python deployment lock after changing `pyproject.toml`
dependencies (run on CPython 3.11 or constrain for it):

```bash
pip-compile --extra=ibkr --extra=streams --generate-hashes \
  --output-file services/quant-api/requirements-deploy.lock \
  services/quant-api/pyproject.toml
```

The preserved remediated files passed 165 Rust tests locally. Those same-host
results do not establish independent-host, cross-network, hostile-load,
production-capacity, external-audit, or exploitability evidence.

Before a public release, refresh the Cargo/npm/PyPI advisory report, remediate
its current findings, triage reachability, complete license/redistribution
review, and record toolchain and artifact hashes. Complete the actual third-party CI action
SHA/digest pinning and trust review before release; the ratchet alone does not
clear that gate. `--fail-on-vulnerability` returns exit status 2 after writing a
report when any vulnerability group remains, so it can gate a remediated tree.
The checked-in evidence does not support a “zero known vulnerabilities” claim.
