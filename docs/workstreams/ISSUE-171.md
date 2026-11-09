# Security dependency remediation

Tracking issue: #171. This page records residual boundaries. It does not close the issue.

## Already on main

PR #173 (`974de9ce04585e1d38824136514325f231b2bdaf`) put the Hickory and
Yamux upgrades on `main`. PR #302 (`8ef21a84846a0e525978d6589b458caf3afc75e4`,
head `d47d14d0c9b429fbc5c6a7d9c8df6fc75bec961c`) put the SQLite upgrade on
`main`. This follow-up does not change `services/p2p-node/Cargo.lock` from
that main:

| Package | Locked version |
|---|---|
| libp2p | 0.57.1 |
| libp2p-yamux | 0.48.0 |
| hickory-proto / hickory-resolver / hickory-net | 0.26.3 |
| yamux | 0.14.1 (only copy) |
| rusqlite | 0.41.1 |
| libsqlite3-sys | 0.39.1 |
| paste | 1.0.15 |

No further supported bump of hickory, yamux, libp2p, or paste is available.
libp2p 0.57.1 and yamux 0.14.1 are the newest crates.io releases.

## SQLite advisory, remediated on main

`RUSTSEC-2026-0459` / `CVE-2026-119375` / `GHSA-72w2-rjgg-q729` affected
`libsqlite3-sys` from 0.6.0 until 0.39.0. The previous lock had `rusqlite`
0.32.1 and `libsqlite3-sys` 0.30.1. OSV records the fix at 0.39.0 (bundled
SQLite 3.54.0). Main now resolves `rusqlite` 0.41.1 and `libsqlite3-sys`
0.39.1 (SQLite 3.54.1) with the `bundled` feature. The temporary remediation
workflow was removed before merge.

On head `d47d14d0c9b429fbc5c6a7d9c8df6fc75bec961c`, both required workflows
succeeded:

- [CI](https://github.com/Osiris6900/osiris-eye/actions/runs/42677260186): python, rust, web
- [Dependency Security Audit](https://github.com/Osiris6900/osiris-eye/actions/runs/42677259943): dependency-inventory, python-audit, npm-audit, rust-audit

`cargo audit` on that lock exited 0. The earlier failure on run `42659492678`
is historical and is not the current main result.

## Regression gate

`scripts/check_p2p_known_vulnerable_locks.py` fails when a locked
hickory-proto is below 0.26.1, hickory-resolver below 0.26.2, hickory-net
below 0.26.1, yamux below 0.13.10, or libsqlite3-sys below 0.39.0.
Pre-releases, build metadata, leading zeros, and other non-stable strings do
not meet those floors. paste is printed as `REVIEW` and does not fail the
command. Dependency-security CI runs the gate.

## Retained advisory evidence

The retained report is `docs/P2P_DEPENDENCY_ADVISORIES.json`:

- query time `2026-11-09T18:47:33Z`
- source commit `2501a07441c7a3e124a7494f2384903c85f5b6ca`
- that commit contains the Cargo lock, the npm lock, `pyproject.toml`, and
  `services/quant-api/requirements-lock.txt` at the hashes stored in the report
- 607 packages (451 crates.io, 132 npm, 24 PyPI)
- 0 vulnerability groups, 1 maintenance-only group (`paste` 1.0.15 /
  `RUSTSEC-2024-0436`)
- PyPI coverage `scanned`
- `--fail-on-vulnerability` exit 0

`libsqlite3-sys` 0.39.1 and `rusqlite` 0.41.1 were queried and were not
matched. Commit `58290743079880aa6c32d871e0d823851501becc` does not contain
this Cargo lock. It is not the retained source identity.

## Enabled Linux paste path

`cargo tree --locked` shows paste 1.0.15 as a proc-macro of
netlink-packet-core 0.8.2, required by if-watch 3.2.2, required by
libp2p-tcp 0.45.0 and libp2p-mdns 0.49.0. `RUSTSEC-2024-0436` is an
unmaintained-package record and names no fixed release. Rechecked on
2026-11-09: paste 1.0.15, if-watch 3.2.2, and libp2p 0.57.1 are still the
newest crates.io releases. netlink-packet-core 0.9.0 removes paste by
deleting the `buffer!` macros and documents that removal as breaking.
if-watch 3.2.2 requires `netlink-packet-core` `^0.8.1`. No semver-compatible
lockfile edit removes paste. The safe next step is an upstream if-watch
release on netlink-packet-core 0.9, then a libp2p release that depends on it.

## Python deployment scan

`services/quant-api/requirements-lock.txt` is the supported deployment lock:
24 exact pins with `--hash=sha256`, Python 3.11, `streams` extra. The
retained OSV report queries those pins. `scripts/check_python_lock.py` is the
offline pin, hash, staleness, and range gate. Development installs from
`pyproject.toml` ranges are outside that scan.

## License and provenance, still outstanding

`docs/PYTHON_LICENSE_PROVENANCE.json` was refreshed at
`2026-11-08T16:20:14Z` against commit
`58290743079880aa6c32d871e0d823851501becc`. All 24 locked distributions pass
`config/license_policy.json` with zero overrides. Dependency-security CI runs
`scripts/check_license_provenance.py`. That gate checks maintainer-declared
PyPI license metadata and the lock's sha256 set. It is not a source-text
audit, a redistribution clearance, or legal advice. Those reviews remain
outstanding.

PR #286 taught `scripts/generate_dependency_sbom.py` to ingest the hashed
Python lock. The SBOM is still not a whole-environment inventory: the optional
`ibkr` extra and development installs from version ranges stay outside the
lock.

## Local validation recorded with this note

- `python3 scripts/test_check_p2p_known_vulnerable_locks.py` — 6 passed
- `python3 scripts/test_scan_dependency_advisories.py` — 29 passed
- `python3 scripts/test_check_license_provenance.py` — 19 passed
- `python3 scripts/check_python_lock.py` and `check_license_provenance.py` — passed
- `cargo test --locked --manifest-path services/p2p-node/Cargo.toml --all-targets` on Rust 1.99.1 at `d47d14d0c9b429fbc5c6a7d9c8df6fc75bec961c` — exit 0, 210 passed, 0 failed. This follow-up does not change that lock or the Rust sources.

Same-host tests are not independent-host, production-capacity, or exploitability evidence. No live trading is enabled.
