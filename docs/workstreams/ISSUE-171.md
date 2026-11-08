# Security dependency remediation

Tracking issue: #171

## Implementation status: Python gate implemented (this branch)

The remaining items from the issue's required work are implemented here:

- **Resolved + hashed Python deployment lock**: `services/quant-api/requirements-lock.txt`
  (schema `osiris.python-deployment-lock.v1`), 24 exactly pinned distributions with
  `--hash=sha256` artifact digests, generated on Python 3.11 by
  `scripts/resolve_python_lock.py` (pip-tools backtracking resolver) from
  `pyproject.toml` core dependencies plus the supported `streams` extra.
- **Offline lock gate**: `scripts/check_python_lock.py` — fail-closed pinning/hash
  checks, header staleness against the current `pyproject.toml` byte hash,
  declared-range conformance, PEP 440 validity. Shared parser:
  `scripts/python_lock.py`.
- **Python advisory scan**: `scripts/scan_dependency_advisories.py` now queries the
  exact PyPI pins when the lock is present (PyPI coverage `scanned`, 24 packages).
  Combined live report `docs/P2P_DEPENDENCY_ADVISORIES.json` regenerated
  2026-11-08T11:20:57Z at source commit `587dbc71e58de3e6dc88748814ab551641a359e0`:
  544 packages, 0 vulnerability groups, 1 maintenance-only group (paste,
  RUSTSEC-2024-0436), `--fail-on-vulnerability` exit 0.
- **License/provenance review**: `scripts/check_license_provenance.py` (offline gate)
  + `config/license_policy.json` + retained evidence
  `docs/PYTHON_LICENSE_PROVENANCE.json` (PyPI JSON API, refreshed 2026-11-08 at the
  same source commit). All 24 distributions pass the policy with zero overrides;
  artifact provenance is the lock's sha256 pin set.

## Acceptance evidence

- `python scripts/test_check_python_lock.py` — 21 tests
- `python scripts/test_scan_dependency_advisories.py` — 29 tests (12 pre-existing
  unchanged in behavior, PyPI coverage tests added)
- `python scripts/test_check_license_provenance.py` — 19 tests
- `python scripts/check_python_lock.py` / `check_license_provenance.py` — gates pass
  on the checked-in lock and evidence

## Honest boundaries

OSV matching is point-in-time advisory evidence, not reachability analysis or an
independent audit. License metadata is maintainer-declared, not a source-text
audit or legal advice. The CycloneDX SBOM does not yet ingest the Python lock
(follow-up). CI workflow wiring for the new gates requires the workflow scope and
is left to the repository owner; snippets are in the pull request body.
No live trading is enabled.
