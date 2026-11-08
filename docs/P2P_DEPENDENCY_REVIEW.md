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

## Python deployment lock

The supported Python deployment environment is now resolved and hashed in
`services/quant-api/requirements-lock.txt` (schema
`osiris.python-deployment-lock.v1`). It was generated on Python 3.11 — the
supported deployment interpreter — by `scripts/resolve_python_lock.py`
(pip-tools backtracking resolver) from `services/quant-api/pyproject.toml`
with the supported `streams` extra, and contains 24 exactly pinned
distributions, each carrying `--hash=sha256` artifact digests from the package
index. The development ranges in `pyproject.toml` remain the compatibility
declaration; the lock is the deployment evidence. The optional `ibkr` extra is
deliberately outside the supported deployment set.

`scripts/check_python_lock.py` is the offline gate: it fails closed unless
every entry is pinned and hashed, the lock header matches the current
`pyproject.toml` byte hash (staleness), every direct deployment requirement
is present and in range, and every version is valid PEP 440. `pip install
--require-hashes -r services/quant-api/requirements-lock.txt` reproduces the
environment on Python 3.11. The CycloneDX SBOM does not yet ingest this lock;
extending `generate_dependency_sbom.py` with PyPI components is follow-up
work and the SBOM must still not be represented as a whole-environment
inventory until then.

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

The three local applications include `osiris-quant-api`. Its dependency
resolution is no longer `unresolved` — the exact deployment set lives in
`services/quant-api/requirements-lock.txt` — but this SBOM generator does not
ingest that lock yet, so version ranges from `pyproject.toml` are still
recorded only as declarations, not as installed packages. The SBOM must not be
represented as a whole-environment inventory until PyPI components from the
lock are incorporated (follow-up work).

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

The live query at `2026-11-08T11:20:57Z` covered 544 packages at source commit
`587dbc71e58de3e6dc88748814ab551641a359e0`: 388 crates.io, 132 npm and — for the
first time — 24 PyPI packages from the hashed deployment lock. One raw match
was normalized into one alias-aware maintenance-only advisory group affecting
one Cargo package. No vulnerability group was returned in any ecosystem in this
point-in-time query, and `--fail-on-vulnerability` exited 0. This is the
post-remediation re-scan retained for issue #171: the Hickory and Yamux
advisory groups from the pre-remediation scan no longer match the locked
graph, and the Python deployment set is now inside the scanned coverage rather
than reported as `unresolved`.

| Package | Classification | Advisory group IDs |
|---|---|---|
| paste 1.0.15 | maintenance-only | RUSTSEC-2024-0436 |

`config/osv_maintenance_only.json` explicitly classifies the `paste` record as
an unmaintained-package warning. It is retained as maintenance evidence rather
than counted as proof of an exploitable vulnerability. Unknown or unclassified
records default to the vulnerability classification; they are not suppressed.

The libp2p 0.57.1 remediation is already on `main` via PR #173
(`974de9ce04585e1d38824136514325f231b2bdaf`). At baseline
`5176eae8843a1e5144ac903f37843fe9bb46cdd9`, `services/p2p-node/Cargo.lock`
resolves hickory-proto, hickory-resolver, and hickory-net 0.26.3 and a single
yamux 0.14.1 through libp2p-yamux 0.48.0. A live OSV `querybatch` repeated at
`2026-11-08T16:09:48Z` against that same lock (544 packages: 388 crates.io,
132 npm, 24 PyPI) returned the same summary as the retained report: 0
vulnerability groups and 1 maintenance-only group. `--fail-on-vulnerability`
exited 0. Cargo.lock is unchanged by the regression gate below.

`cargo metadata --manifest-path services/p2p-node/Cargo.toml --locked
--format-version 1` records the enabled libp2p features `autonat`, `dcutr`,
`gossipsub`, `identify`, `kad`, `macros`, `mdns`, `noise`, `relay`, `tcp`,
`tokio`, `upnp`, and `yamux`. `cargo tree --locked` shows hickory-proto 0.26.3
required by libp2p-mdns 0.49.0 and yamux 0.14.1 required by libp2p-yamux 0.48.0.
OSIRIS call sites pass `yamux::Config::default()`. The `dns` feature is absent
from that feature list. Cargo still locks the optional libp2p-dns 0.45.0 /
hickory-resolver 0.26.3 edge, and libp2p-quic / libp2p-metrics, because the
lock records optional dependencies as well as enabled ones. Those locked
copies are already on the patched Hickory line. They were left in the lock.

`paste` 1.0.15 remains on an enabled Linux path. `cargo tree --locked` shows
the proc-macro required by netlink-packet-core 0.8.2, required by if-watch
3.2.2, required by both libp2p-tcp 0.45.0 and libp2p-mdns 0.49.0. The OSV
record for `RUSTSEC-2024-0436` is informational (`unmaintained`), covers every
paste release, and names no fixed version. crates.io still lists paste 1.0.15
as the newest release. netlink-packet-core 0.9.0 (published 2026-08-18) drops
paste by removing the `buffer!` macros, and that release notes the removal as
breaking. if-watch 3.2.2 requires `netlink-packet-core` `^0.8.1`; 3.2.2 is the
newest if-watch release, and libp2p 0.57.1 (also the newest libp2p release)
depends on it. No semver-compatible bump removes paste. The minimal next step
is an upstream if-watch release on the netlink-packet-core 0.9 line, then a
libp2p release that consumes it. Until that exists, the maintenance finding
stays, separate from the vulnerability gate.

`scripts/check_p2p_known_vulnerable_locks.py` rejects any locked
hickory-proto below 0.26.1, hickory-resolver below 0.26.2, hickory-net below
0.26.1, or yamux below 0.13.10, including when a patched copy is locked beside
an older one. paste is printed as `REVIEW` and does not fail the command.
Dependency-security CI runs the gate on every pull request. The gate is
regression protection for versions already on main. It is not a new upgrade
and it does not clear license, provenance, or a universal vulnerability claim.

A historical local resolved-environment pip-audit scan is retained in
[PYTHON_DEPENDENCY_AUDIT.json](PYTHON_DEPENDENCY_AUDIT.json): no known
advisories after upgrading cryptography, pytest and pip. It covered a
development environment rather than the supported deployment lock and is
superseded as advisory evidence by the combined OSV report above, which now
queries the exact locked PyPI pins. OSV matching is point-in-time advisory
evidence, not proof of exploitation, a universal vulnerability-free result,
or an independent audit.

## Python license and artifact provenance

`scripts/check_license_provenance.py` gates license/redistribution metadata
and artifact provenance for the Python lock, offline by default. Evidence
lives in `docs/PYTHON_LICENSE_PROVENANCE.json` (schema
`osiris.python-license-provenance.v1`): per distribution, the PyPI JSON API
license expression, legacy field and license classifiers, refreshed
2026-11-08 at source commit `587dbc71e58de3e6dc88748814ab551641a359e0`.
The policy in `config/license_policy.json` allows permissive expressions
(MIT, MIT-0, Apache-2.0, BSD-2/3-Clause, 0BSD, ISC, Zlib, CC0-1.0, PSF-2.0,
Python-2.0, MPL-2.0); `OR` expressions pass when any branch is allowed, `AND`
expressions (bundled subcomponents, e.g. numpy's
`BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0`) require every branch.
All 24 locked distributions pass with zero policy overrides. Copyleft and
proprietary expressions are recognized explicitly so they are denied by name
rather than as "unknown".

Artifact provenance for Python is the lock itself: every distribution is
pinned to an exact version with `--hash=sha256` digests recorded from the
package index at resolution time, and installation with `--require-hashes`
fails on any artifact substitution. The gate fails closed on missing
evidence, version mismatch, stale evidence (lock byte hash), disallowed or
unusable expressions, and evidence records absent from the lock. License
metadata is maintainer-declared, not a source-text audit or legal advice.

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
python scripts/check_p2p_known_vulnerable_locks.py services/p2p-node/Cargo.lock
python scripts/test_check_p2p_known_vulnerable_locks.py
python scripts/test_scan_dependency_advisories.py
python scripts/scan_dependency_advisories.py \
  --source-commit 587dbc71e58de3e6dc88748814ab551641a359e0 \
  --output docs/P2P_DEPENDENCY_ADVISORIES.json
python3.11 -m venv /tmp/lockenv && /tmp/lockenv/bin/pip install pip-tools
/tmp/lockenv/bin/python scripts/resolve_python_lock.py
python scripts/check_python_lock.py
python scripts/test_check_python_lock.py
python scripts/check_license_provenance.py
python scripts/check_license_provenance.py --refresh --source-commit <full-sha>
python scripts/test_check_license_provenance.py
```

The preserved remediated files passed 165 Rust tests locally. Those same-host
results do not establish independent-host, cross-network, hostile-load,
production-capacity, external-audit, or exploitability evidence.

Before a public release, refresh the combined advisory report, remediate its
current findings, triage reachability, re-resolve and re-scan the Python lock
after any `pyproject.toml` change, extend the SBOM with PyPI components, and
record toolchain and artifact hashes. Complete the actual third-party CI action
SHA/digest pinning and trust review before release; the ratchet alone does not
clear that gate. `--fail-on-vulnerability` returns exit status 2 after writing a
report when any vulnerability group remains, so it can gate a remediated tree.
The checked-in evidence does not support a “zero known vulnerabilities” claim.
