#!/usr/bin/env python3
"""Fail-closed temporary lockfile ratchet for known P2P dependency advisories.

This is not a full OSV scan, reachability analysis or dependency remediation.
Remove/update the blocklist only with reviewed advisory evidence.
"""
import argparse
from pathlib import Path
import sys
import tomllib

BLOCKED = {
    ("hickory-proto", "0.25.2"): "RUSTSEC-2026-0118 / RUSTSEC-2026-0119",
    ("hickory-resolver", "0.25.2"): "GHSA-5j98-2g5x-46v6 / GHSA-6f2x-v7q7-m7m5",
    ("yamux", "0.12.1"): "CVE-2026-32314",
}
MAINTENANCE = {("paste", "1.0.15"): "RUSTSEC-2024-0436 (unmaintained)"}

def inspect(lock):
    packages = lock.get("package")
    if not isinstance(packages, list):
        raise ValueError("Cargo lock missing package list")
    found = {(p.get("name"), p.get("version")) for p in packages if isinstance(p, dict)}
    return [(name, version, advisory) for (name, version), advisory in BLOCKED.items()
            if (name, version) in found], [
        (name, version, advisory) for (name, version), advisory in MAINTENANCE.items()
        if (name, version) in found]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("lockfile", type=Path)
    args = parser.parse_args()
    try:
        data = tomllib.loads(args.lockfile.read_text())
        blocked, maintenance = inspect(data)
    except (OSError, ValueError) as exc:
        print("BLOCK: cannot inspect lock:", exc)
        return 2
    for name, version, advisory in blocked:
        print(f"BLOCK: {name} {version}: {advisory}")
    for name, version, advisory in maintenance:
        print(f"REVIEW: {name} {version}: {advisory}")
    if blocked:
        return 1
    print("Known-version ratchet passed; full security scan and review still required")
    return 0

if __name__ == "__main__":
    sys.exit(main())
