#!/usr/bin/env python3
"""Fail closed when the P2P lock resolves a version below a reviewed fix line.

The patched Hickory and Yamux versions are already resolved on main. This
ratchet stops a later lock refresh from bringing a vulnerable version back.
It is not an OSV scan, a reachability proof, or a paste remediation.
"""
import argparse
from pathlib import Path
import sys
import tomllib

# Minimum versions OSV records as fixed, checked against api.osv.dev on 2026-11-08.
FLOORS = (
    ("hickory-proto", "0.26.1", "RUSTSEC-2026-0118 / RUSTSEC-2026-0119"),
    ("hickory-resolver", "0.26.2", "GHSA-5j98-2g5x-46v6 / GHSA-6f2x-v7q7-m7m5"),
    ("hickory-net", "0.26.1", "RUSTSEC-2026-0120 / GHSA-3v94-mw7p-v465"),
    ("yamux", "0.13.10", "GHSA-vxx9-2994-q338 / CVE-2026-32314"),
)
# RUSTSEC-2024-0436 covers every paste release and names no fixed version.
MAINTENANCE = {"paste": "RUSTSEC-2024-0436 (unmaintained; no fixed crates.io release)"}


def version_key(version):
    if not isinstance(version, str) or not version or version != version.strip():
        raise ValueError(f"invalid crate version {version!r}")
    release, separator, _prerelease = version.partition("-")
    numbers = release.split(".")
    if not numbers or any(not part.isdigit() for part in numbers):
        raise ValueError(f"invalid crate version {version!r}")
    # A pre-release is older than the same numbered release.
    return (tuple(int(part) for part in numbers), 0 if separator else 1)


def older_than(version, floor):
    return version_key(version) < version_key(floor)


def inspect(lock):
    packages = lock.get("package")
    if not isinstance(packages, list):
        raise ValueError("Cargo lock missing package list")
    found = []
    for package in packages:
        if not isinstance(package, dict):
            raise ValueError("Cargo lock package entry is not a table")
        name = package.get("name")
        version = package.get("version")
        if not isinstance(name, str) or not isinstance(version, str):
            raise ValueError("Cargo lock package is missing a name or version")
        found.append((name, version))
    blocked = [
        (name, version, floor, advisory)
        for name, version in found
        for crate, floor, advisory in FLOORS
        if name == crate and older_than(version, floor)
    ]
    maintenance = [
        (name, version, advisory)
        for name, version in found
        if (advisory := MAINTENANCE.get(name))
    ]
    return blocked, maintenance


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("lockfile", type=Path)
    args = parser.parse_args()
    try:
        blocked, maintenance = inspect(tomllib.loads(args.lockfile.read_text()))
    except (OSError, ValueError) as exc:
        print("BLOCK: cannot inspect lock:", exc)
        return 2
    for name, version, floor, advisory in blocked:
        print(f"BLOCK: {name} {version}: {advisory} (fixed in {floor})")
    for name, version, advisory in maintenance:
        print(f"REVIEW: {name} {version}: {advisory}")
    if blocked:
        return 1
    print("Known-version ratchet passed; full security scan and review still required")
    return 0


if __name__ == "__main__":
    sys.exit(main())
