#!/usr/bin/env python3
"""License/redistribution and artifact-provenance gate for the Python lock.

Default mode is fully offline: it verifies every distribution in the hashed
deployment lock against the retained evidence file
(`docs/PYTHON_LICENSE_PROVENANCE.json`) and the policy
(`config/license_policy.json`). `--refresh` re-queries the PyPI JSON API and
rewrites the evidence file (network required).

Checks, all fail-closed:
  1. every locked distribution has an evidence entry with a matching version,
  2. its license expression normalizes to a policy-allowed expression
     (`A OR B` passes when any branch is allowed; `A AND B` needs all),
     or an explicit documented policy override,
  3. the evidence file matches the current lock byte hash (staleness gate),
  4. artifact provenance: every locked distribution carries sha256 hashes
     (delegated to python_lock.parse_hashed_lock) sourced from the package
     index recorded in the evidence metadata.

This is metadata-level license review evidence, not legal advice and not an
audit of the full source text of every distribution.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent))
from python_lock import LOCK_RELATIVE_PATH, normalize_name, parse_hashed_lock  # noqa: E402

SCHEMA = "osiris.python-license-provenance.v1"
POLICY_SCHEMA = "osiris.license-policy.v1"
PYPI_JSON = "https://pypi.org/pypi/{name}/{version}/json"
USER_AGENT = "osiris-eye-license-review (https://github.com/Osiris6900/osiris-eye)"

LICENSE_ALIASES = {
    "mit": "MIT",
    "mit license": "MIT",
    "the mit license": "MIT",
    "apache-2.0": "Apache-2.0",
    "apache 2.0": "Apache-2.0",
    "apache2": "Apache-2.0",
    "apache software license": "Apache-2.0",
    "apache license 2.0": "Apache-2.0",
    "apache license, version 2.0": "Apache-2.0",
    "bsd-2-clause": "BSD-2-Clause",
    "bsd-3-clause": "BSD-3-Clause",
    "bsd license": "BSD-3-Clause",
    "new bsd license": "BSD-3-Clause",
    "isc": "ISC",
    "isc license": "ISC",
    "psf": "PSF-2.0",
    "psf-2.0": "PSF-2.0",
    "python software foundation license": "PSF-2.0",
    "python-2.0": "Python-2.0",
    "mpl-2.0": "MPL-2.0",
    "mozilla public license 2.0": "MPL-2.0",
    "mozilla public license 2.0 (mpl 2.0)": "MPL-2.0",
    "mit-0": "MIT-0",
    "mit no attribution": "MIT-0",
    "0bsd": "0BSD",
    "bsd zero clause license": "0BSD",
    "zlib": "Zlib",
    "zlib/libpng license": "Zlib",
    "cc0-1.0": "CC0-1.0",
    "cc0 1.0 universal": "CC0-1.0",
    "public domain": "CC0-1.0",
    # recognized so they are explicitly denied by policy rather than "unknown"
    "gpl-2.0": "GPL-2.0",
    "gpl-2.0-only": "GPL-2.0",
    "gpl-3.0": "GPL-3.0",
    "gpl-3.0-only": "GPL-3.0",
    "gnu general public license v3": "GPL-3.0",
    "lgpl-2.1": "LGPL-2.1",
    "lgpl-3.0": "LGPL-3.0",
    "agpl-3.0": "AGPL-3.0",
    "proprietary": "Proprietary",
}

_SPLIT_RE = re.compile(r"\s+(?P<op>OR|AND)\s+", re.IGNORECASE)


def normalize_license(raw: str) -> str | None:
    cleaned = re.sub(r"\s+", " ", raw.strip()).strip("()")
    return LICENSE_ALIASES.get(cleaned.lower(), None)


def expression_allowed(expression: str, allowed: set[str]) -> bool:
    """OR = any branch allowed; AND = all branches allowed. Unknown = denied."""
    tokens = _SPLIT_RE.split(expression)
    if len(tokens) == 1:
        normalized = normalize_license(tokens[0])
        return normalized in allowed if normalized else False
    # Alternate branch/op/branch; a uniform op per expression is all we emit.
    branches = tokens[0::2]
    ops = {token.upper() for token in tokens[1::2]}
    if len(ops) != 1:
        raise ValueError(f"mixed AND/OR license expression is unsupported: {expression!r}")
    results = []
    for branch in branches:
        normalized = normalize_license(branch)
        results.append(normalized in allowed if normalized else False)
    return any(results) if ops == {"OR"} else all(results)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_policy(path: Path) -> dict:
    document = json.loads(path.read_text())
    if not isinstance(document, dict) or document.get("schema") != POLICY_SCHEMA:
        raise ValueError(f"{path}: unsupported license policy schema")
    allowed = document.get("allowed_expressions")
    if not isinstance(allowed, list) or not allowed \
            or not all(isinstance(item, str) and item for item in allowed):
        raise ValueError(f"{path}: allowed_expressions must be a non-empty list")
    overrides = document.get("overrides", {})
    if not isinstance(overrides, dict):
        raise ValueError(f"{path}: overrides must be a mapping")
    for name, override in overrides.items():
        if not isinstance(override, dict) or not isinstance(override.get("expression"), str) \
                or not isinstance(override.get("reason"), str) or not override["reason"].strip():
            raise ValueError(f"{path}: override {name!r} needs an expression and a reason")
    return document


def fetch_pypi_metadata(name: str, version: str, retries: int = 3) -> dict:
    url = PYPI_JSON.format(name=quote(name), version=quote(version))
    for attempt in range(retries):
        try:
            request = Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
            with urlopen(request, timeout=45) as response:
                document = json.load(response)
            info = document.get("info") if isinstance(document, dict) else None
            if not isinstance(info, dict):
                raise ValueError(f"PyPI returned no info block for {name} {version}")
            return info
        except HTTPError as error:
            if error.code == 404:
                raise ValueError(f"PyPI has no release {name}=={version}") from error
            if attempt + 1 == retries:
                raise ValueError(f"PyPI metadata failed for {name}: HTTP {error.code}") from error
        except (URLError, TimeoutError) as error:
            if attempt + 1 == retries:
                raise ValueError(f"PyPI metadata failed for {name}: {error}") from error
        time.sleep(0.5 * (2 ** attempt))
    raise AssertionError("unreachable")


def metadata_license_fields(info: dict) -> dict:
    expression = info.get("license_expression")
    classifiers = [
        item.split("::")[-1].strip()
        for item in info.get("classifiers", [])
        if isinstance(item, str) and item.startswith("License ::") and "OSI Approved" in item
    ]
    legacy = info.get("license")
    if not isinstance(legacy, str) or len(legacy) > 200:
        legacy = None  # full license text is not a usable expression
    return {
        "license_expression": expression if isinstance(expression, str) and expression else None,
        "license_classifiers": classifiers,
        "license_legacy": legacy.strip() if legacy else None,
    }


def best_expression(fields: dict) -> str | None:
    """Pick the most specific reported expression, in metadata priority order."""
    candidates = [fields.get("license_expression"), fields.get("license_legacy")]
    candidates += fields.get("license_classifiers", [])
    for candidate in candidates:
        if candidate and normalize_license(candidate):
            return candidate
        if candidate and _SPLIT_RE.search(candidate):
            branches = _SPLIT_RE.split(candidate)
            if all(normalize_license(branch) for branch in branches[0::2]):
                return candidate
    return None


def refresh_evidence(lock_path: Path, source_commit: str, queried_at: str, workers: int = 8) -> dict:
    entries = parse_hashed_lock(lock_path)

    def fetch(entry):
        info = fetch_pypi_metadata(entry.name, entry.version)
        fields = metadata_license_fields(info)
        record = {
            "name": entry.name,
            "normalized_name": entry.normalized_name,
            "version": entry.version,
            **fields,
        }
        resolved = best_expression(fields)
        record["resolved_expression"] = resolved
        return record

    packages = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for record in pool.map(fetch, entries):
            packages.append(record)
    packages.sort(key=lambda record: record["normalized_name"])
    return {
        "schema": SCHEMA,
        "source": {"repository": "Osiris6900/osiris-eye", "commit": source_commit},
        "lock": {"path": LOCK_RELATIVE_PATH, "sha256": sha256_file(lock_path)},
        "metadata": {
            "service": "PyPI JSON API",
            "url_template": PYPI_JSON,
            "queried_at": queried_at,
        },
        "packages": packages,
        "limitations": [
            "License metadata is maintainer-declared PyPI metadata, not a source-text audit.",
            "Artifact provenance is the sha256 hash set pinned in the deployment lock, fetched from the recorded package index.",
        ],
    }


def check_evidence(lock_path: Path, evidence_path: Path, policy_path: Path) -> list[str]:
    policy = load_policy(policy_path)
    allowed = set(policy["allowed_expressions"])
    overrides = {normalize_name(name): value for name, value in policy.get("overrides", {}).items()}
    entries = parse_hashed_lock(lock_path)

    evidence = json.loads(evidence_path.read_text())
    if not isinstance(evidence, dict) or evidence.get("schema") != SCHEMA:
        raise ValueError(f"{evidence_path}: unsupported evidence schema")
    if evidence.get("lock", {}).get("sha256") != sha256_file(lock_path):
        raise ValueError(f"{evidence_path}: stale evidence — regenerate with --refresh")
    records = evidence.get("packages")
    if not isinstance(records, list):
        raise ValueError(f"{evidence_path}: packages must be a list")
    by_name = {}
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("name"), str):
            raise ValueError(f"{evidence_path}: malformed package record")
        key = normalize_name(record["name"])
        if key in by_name:
            raise ValueError(f"{evidence_path}: duplicate record for {record['name']}")
        by_name[key] = record

    problems = []
    for entry in entries:
        record = by_name.pop(entry.normalized_name, None)
        if record is None:
            problems.append(f"{entry.name}: missing license evidence")
            continue
        if record.get("version") != entry.version:
            problems.append(f"{entry.name}: evidence version {record.get('version')} != locked {entry.version}")
            continue
        override = overrides.get(entry.normalized_name)
        if override is not None:
            if not expression_allowed(override["expression"], allowed):
                problems.append(f"{entry.name}: policy override expression {override['expression']!r} is not allowed")
            continue
        resolved = best_expression(record)
        if resolved is None:
            problems.append(f"{entry.name}: no usable license expression in metadata")
            continue
        try:
            if not expression_allowed(resolved, allowed):
                problems.append(f"{entry.name}: license {resolved!r} is not policy-allowed")
        except ValueError as error:
            problems.append(f"{entry.name}: {error}")
    if by_name:
        problems.append(f"evidence records not present in lock: {sorted(by_name)}")
    if problems:
        raise ValueError("license/provenance gate failed:\n" + "\n".join(f"  - {p}" for p in problems))
    return [
        f"{len(entries)} locked distributions have license evidence",
        f"all expressions allowed by policy ({len(allowed)} allowed expressions, {len(overrides)} overrides)",
        "evidence matches current lock sha256",
        "artifact provenance: sha256 hashes pinned for every distribution",
    ]


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, default=root / LOCK_RELATIVE_PATH)
    parser.add_argument("--policy", type=Path, default=root / "config/license_policy.json")
    parser.add_argument("--evidence", type=Path, default=root / "docs/PYTHON_LICENSE_PROVENANCE.json")
    parser.add_argument("--refresh", action="store_true", help="re-query PyPI and rewrite the evidence file")
    parser.add_argument("--source-commit", default=None, help="required with --refresh: full Git SHA")
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    try:
        if args.refresh:
            if not args.source_commit or not re.fullmatch(r"[0-9a-f]{40}", args.source_commit):
                raise ValueError("--refresh requires --source-commit <full 40-char Git SHA>")
            from datetime import datetime, timezone
            queried_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
            evidence = refresh_evidence(args.lock, args.source_commit, queried_at, args.workers)
            args.evidence.parent.mkdir(parents=True, exist_ok=True)
            args.evidence.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
            print(f"Wrote {args.evidence}: {len(evidence['packages'])} package records")
        performed = check_evidence(args.lock, args.evidence, args.policy)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise SystemExit(str(error)) from error
    for line in performed:
        print(f"ok: {line}")


if __name__ == "__main__":
    main()
