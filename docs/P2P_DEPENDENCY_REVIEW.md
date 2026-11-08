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
including `cryptography>=42,<47`, `fastapi>=0.110,<1`, `pydantic>=2.6,<3`,
and `numpy>=1.26,<3`. Its development range includes `pytest>=8,<9`.
These are version constraints, not a resolved, hashed Python lockfile. Retain
the exact installed environment with each release's test evidence; results from
dependencies outside the declared ranges do not validate the supported set.

## CycloneDX dependency inventory

`scripts/generate_dependency_sbom.py` generates deterministic CycloneDX 1.5 JSON
from the exact Cargo and npm lockfiles. For the reviewed inputs, the SBOM has
523 components: three local applications, 388 crates.io libraries and 132 npm
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
provenance attestation, reproducible-build proof, or independent security
assessment. Exact-version license metadata still requires operator review
against the actual distributed artifacts.

## OSV advisory evidence

`scripts/scan_dependency_advisories.py` queries OSV for every exact registry
package in the Cargo and npm lockfiles and writes
`docs/P2P_DEPENDENCY_ADVISORIES.json`. The checked-in report records the source
commit, input SHA-256 hashes, query timestamp, package counts, matched advisory
IDs, aliases and selected OSV record fields. Its offline validation fails when
the report's source identity, timestamp, input hashes, package coverage, finding
IDs/classifications or derived summary counts are stale or internally
inconsistent. The checks derive expected counts from the current lock inputs;
they do not require a fixed number of advisories after remediation.

The live query at `2026-11-07T22:29:10Z` covered 520 packages at source commit
`974de9ce04585e1d38824136514325f231b2bdaf`: 388 crates.io and 132 npm packages.
One raw match was normalized into one alias-aware maintenance-only advisory group
affecting one Cargo package. No vulnerability group or npm match was returned in
this point-in-time query.

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

The historical OSV report below leaves Python `unresolved`. A subsequent local
resolved-environment pip-audit scan is retained in
[PYTHON_DEPENDENCY_AUDIT.json](PYTHON_DEPENDENCY_AUDIT.json): no known
advisories after upgrading cryptography, pytest and pip. It includes development
and scanner tooling, skips first-party source advisory matching and does not
establish Python 3.11 or optional-extra dependency resolution. OSV matching is point-in-time advisory evidence, not proof of
exploitation, a universal vulnerability-free result, or an independent audit.
The broader dependency gate also remains open for Python resolution,
license/redistribution and artifact-provenance review.

## CI action pinning ratchet

The 59 remote `uses` references across eight workflows are now pinned to full
commit SHAs resolved through GitHub on 2026-11-07. The ratchet baseline is empty,
so new mutable references fail closed. Reviewed reference resolution:

| Previous reference | Resolved commit |
|---|---|
| `actions/checkout@v4` | `11d5960a326750d5838078e36cf38b85af677262` |
| `actions/download-artifact@v4` | `d3f86a106a0bac45b974a628896c90dbdf5c8093` |
| `actions/setup-node@v4` | `49933ea5288caeca8642d1e84afbd3f7d6820020` |
| `actions/setup-python@v5` | `a26af69be951a213d495a4c3e4e4022e16d87065` |
| `actions/upload-artifact@v4` | `ea165f8d65b6e75b540449e92b4886f43607fa02` |
| `dtolnay/rust-toolchain@stable` | `b725eda525362e7cf34af9a7243cac1e3f01a7ec` |
| `tailscale/github-action@v3` | `6cae46e2d796f265265cfcf628b72a32b4d7cade` |

The offline checker validates workflow YAML, embedded shell syntax and immutable
references. Pinning prevents a moved tag from silently changing the selected
action; it does not audit action source, attest release artifacts or replace an
independent security review. Workflow permission is required to publish these
changes; local validation alone does not establish deployment.

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
  --source-commit 974de9ce04585e1d38824136514325f231b2bdaf \
  --output docs/P2P_DEPENDENCY_ADVISORIES.json
```

The preserved remediated files passed 165 Rust tests locally. Those same-host
results do not establish independent-host, cross-network, hostile-load,
production-capacity, external-audit, or exploitability evidence.

Before a public release, refresh the Cargo/npm advisory report, remediate its
current findings, triage reachability, lock and scan the supported Python
deployment environment, complete license/redistribution review, and record
toolchain and artifact hashes. Complete the actual third-party CI action
SHA/digest pinning and trust review before release; the ratchet alone does not
clear that gate. `--fail-on-vulnerability` returns exit status 2 after writing a
report when any vulnerability group remains, so it can gate a remediated tree.
The checked-in evidence does not support a “zero known vulnerabilities” claim.
