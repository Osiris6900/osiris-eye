#!/usr/bin/env python3
"""Fail closed when the P2P lock resolves a version below a reviewed fix line.

The patched Hickory and Yamux versions are already resolved on main. This
ratchet stops a later lock refresh from bringing a vulnerable version back.
It is not an OSV scan, a reachability proof, or a paste remediation.
"""
import argparse
from pathlib import Path
import re
import sys
import tomllib

# Minimum versions OSV records as fixed, checked against api.osv.dev on 2026-11-08.
FLOORS = (
    ("hickory-proto", "0.26.1", "RUSTSEC-2026-0118 / RUSTSEC-2026-0119"),
    ("hickory-resolver", "0.26.2", "GHSA-5j98-2g5x-46v6 / GHSA-6f2x-v7q7-m7m5"),
    ("hickory-net", "0.26.1", "RUSTSEC-2026-0120 / GHSA-3v94-mw7p-v465"),
    ("yamux", "0.13.10", "GHSA-vxx9-2994-q338 / CVE-2026-32314"),
    ("libsqlite3-sys", "0.39.0", "RUSTSEC-2026-0459"),
)
# RUSTSEC-2024-0436 covers every paste release and names no fixed version.
MAINTENANCE = {"paste": "RUSTSEC-2024-0436 (unmaintained; no fixed crates.io release)"}


# Stable dotted releases only. Pre-releases, build metadata, leading zeros,
# and any other string are not treated as meeting a fix line.
_STABLE_VERSION = re.compile(r"^(0|[1-9][0-9]*)(\.(0|[1-9][0-9]*))*$")


def stable_key(version):
    if isinstance(version, str) and _STABLE_VERSION.fullmatch(version):
        return tuple(int(part) for part in version.split("."))
    return None


def below_fix_line(version, floor):
    got = stable_key(version)
    if got is None:
        return True
    return got < stable_key(floor)


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
        if name == crate and below_fix_line(version, floor)
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
        if stable_key(version) is None:
            reason = f"not a stable release; fix line is {floor}"
        else:
            reason = f"fixed in {floor}"
        print(f"BLOCK: {name} {version}: {advisory} ({reason})")
    for name, version, advisory in maintenance:
        print(f"REVIEW: {name} {version}: {advisory}")
    if blocked:
        return 1
    print("Known-version ratchet passed; full security scan and review still required")
    return 0


if __name__ == "__main__":
    sys.exit(main())
