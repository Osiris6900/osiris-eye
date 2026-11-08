# Security dependency remediation

Tracking issue: #171. This page records residual boundaries. It does not close the issue.

## Already on main

PR #173 (`974de9ce04585e1d38824136514325f231b2bdaf`) is on `main`
(`5176eae8843a1e5144ac903f37843fe9bb46cdd9`). `services/p2p-node/Cargo.lock`
is unchanged by the follow-up work:

| Package | Locked version |
|---|---|
| libp2p | 0.57.1 |
| libp2p-yamux | 0.48.0 |
| hickory-proto / hickory-resolver / hickory-net | 0.26.3 |
| yamux | 0.14.1 (only copy) |
| paste | 1.0.15 |

No further supported bump of those crates is available. libp2p 0.57.1 and
yamux 0.14.1 are the newest crates.io releases.

## Regression gate

PR #276 adds `scripts/check_p2p_known_vulnerable_locks.py`. It fails when a
locked hickory-proto is below 0.26.1, hickory-resolver below 0.26.2,
hickory-net below 0.26.1, or yamux below 0.13.10. Pre-releases, build
metadata, leading zeros, and other non-stable strings do not meet those
floors. paste is printed as `REVIEW` and does not fail the command.
Dependency-security CI runs the gate.

## Retained advisory evidence

The retained report is `docs/P2P_DEPENDENCY_ADVISORIES.json`:

- query time `2026-11-08T16:20:16Z`
- source commit `58290743079880aa6c32d871e0d823851501becc`
- that commit contains the Cargo lock, the npm lock, `pyproject.toml`, and
  `services/quant-api/requirements-lock.txt` at the hashes stored in the report
- 544 packages (388 crates.io, 132 npm, 24 PyPI)
- 0 vulnerability groups, 1 maintenance-only group (`paste` 1.0.15 /
  `RUSTSEC-2024-0436`)
- PyPI coverage `scanned`

Commit `587dbc71e58de3e6dc88748814ab551641a359e0` does not contain
`requirements-lock.txt`. It is not the retained source identity.

## Enabled Linux paste path

`cargo tree --locked` shows paste 1.0.15 as a proc-macro of
netlink-packet-core 0.8.2, required by if-watch 3.2.2, required by
libp2p-tcp 0.45.0 and libp2p-mdns 0.49.0. `RUSTSEC-2024-0436` is an
unmaintained-package record and names no fixed release. paste 1.0.15 is still
the newest crates.io release. netlink-packet-core 0.9.0 removes paste by
deleting the `buffer!` macros and documents that removal as breaking.
if-watch 3.2.2 requires `netlink-packet-core` `^0.8.1` and is the newest
if-watch release. libp2p 0.57.1 depends on that release. No semver-compatible
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

The CycloneDX generator still does not ingest the Python lock, so the SBOM is
not a whole-environment inventory. Extending it is separate work and is not
done here.

## Local validation recorded with this note

- `python3 scripts/test_check_p2p_known_vulnerable_locks.py` — 6 passed
- `python3 scripts/test_scan_dependency_advisories.py` — 29 passed
- `python3 scripts/test_check_license_provenance.py` — 19 passed
- `python3 scripts/check_python_lock.py` and `check_license_provenance.py` — passed
- `cargo test --locked --manifest-path services/p2p-node/Cargo.toml --all-targets` on Rust 1.99.1 — exit 0, 185 passed, 0 failed

Same-host tests are not independent-host, production-capacity, or exploitability evidence. No live trading is enabled.
