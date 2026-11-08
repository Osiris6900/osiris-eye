#!/usr/bin/env python3
"""Offline verification gate for the hashed Python deployment lock.

Checks, all fail-closed:
  1. every entry is exactly pinned and carries sha256 artifact hashes
     (delegated to python_lock.parse_hashed_lock),
  2. the lock header carries generator metadata and matches the current
     pyproject.toml byte hash (staleness gate),
  3. every direct deployment requirement (project dependencies plus the
     supported `streams` extra) is present in the lock and its locked
     version satisfies the declared range,
  4. every locked version is a valid PEP 440 version.

This gate verifies resolution hygiene and freshness. It is not a
vulnerability scan (scripts/scan_dependency_advisories.py) and not a
license review (scripts/check_license_provenance.py).
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import sys
import tomllib

try:  # packaging is not guaranteed importable; pip always vendors it
    from packaging.requirements import Requirement
    from packaging.version import InvalidVersion, Version
except ImportError:  # pragma: no cover - exercised only where packaging is absent
    from pip._vendor.packaging.requirements import Requirement
    from pip._vendor.packaging.version import InvalidVersion, Version

sys.path.insert(0, str(Path(__file__).resolve().parent))
from python_lock import (  # noqa: E402
    LOCK_HEADER_SCHEMA,
    LOCK_RELATIVE_PATH,
    normalize_name,
    parse_hashed_lock,
    parse_lock_header,
)

DEPLOYMENT_EXTRAS = ("streams",)
REQUIRED_HEADER_KEYS = (
    "schema",
    "generated_by",
    "generator",
    "python_version",
    "pyproject_sha256",
    "generated_at",
)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def deployment_requirements(pyproject: Path) -> list[Requirement]:
    document = tomllib.loads(pyproject.read_text()).get("project", {})
    declared = list(document.get("dependencies", []))
    extras = document.get("optional-dependencies", {})
    for extra in DEPLOYMENT_EXTRAS:
        declared.extend(extras.get(extra, []))
    requirements = []
    for raw in declared:
        requirement = Requirement(raw)
        if requirement.marker is not None:
            raise ValueError(f"deployment requirement must not carry an environment marker: {raw}")
        requirements.append(requirement)
    return requirements


def check_lock(lock_path: Path, pyproject: Path) -> list[str]:
    """Return a list of human-readable checks performed; raise on failure."""
    entries = parse_hashed_lock(lock_path)
    by_name = {entry.normalized_name: entry for entry in entries}

    header = parse_lock_header(lock_path)
    for key in REQUIRED_HEADER_KEYS:
        if key not in header:
            raise ValueError(f"{lock_path}: missing header metadata key {key!r}; regenerate the lock")
    if header["schema"] != LOCK_HEADER_SCHEMA:
        raise ValueError(f"{lock_path}: unsupported lock schema {header['schema']!r}")
    expected_digest = sha256_file(pyproject)
    if header["pyproject_sha256"] != expected_digest:
        raise ValueError(
            f"{lock_path}: stale lock — header pyproject_sha256 {header['pyproject_sha256']} "
            f"!= current {expected_digest}; re-run scripts/resolve_python_lock.py"
        )

    for entry in entries:
        try:
            Version(entry.version)
        except InvalidVersion as error:
            raise ValueError(f"{lock_path}: {entry.name} has invalid PEP 440 version {entry.version!r}") from error

    for requirement in deployment_requirements(pyproject):
        entry = by_name.get(normalize_name(requirement.name))
        if entry is None:
            raise ValueError(f"{lock_path}: direct deployment requirement {requirement.name} is not locked")
        if requirement.specifier and not requirement.specifier.contains(entry.version, prereleases=False):
            raise ValueError(
                f"{lock_path}: locked {entry.name}=={entry.version} violates declared range "
                f"{requirement.specifier}"
            )

    return [
        f"parsed {len(entries)} pinned+hashed distributions",
        f"header metadata complete (schema {LOCK_HEADER_SCHEMA}, generator {header['generator']})",
        "pyproject_sha256 matches current pyproject.toml",
        "all locked versions are valid PEP 440",
        f"all direct deployment requirements (extras: {', '.join(DEPLOYMENT_EXTRAS)}) present and in range",
    ]


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, default=root / LOCK_RELATIVE_PATH)
    parser.add_argument("--pyproject", type=Path, default=root / "services/quant-api/pyproject.toml")
    args = parser.parse_args()
    try:
        performed = check_lock(args.lock, args.pyproject)
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise SystemExit(str(error)) from error
    for line in performed:
        print(f"ok: {line}")
    print(f"Python deployment lock verified: {args.lock}")


if __name__ == "__main__":
    main()
