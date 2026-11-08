#!/usr/bin/env python3
"""Fail-closed validator for operator-provided P2P release evidence manifest.

This checks manifest structure only; it does not prove that tests actually ran.
"""
import argparse
import json
import re
from pathlib import Path

SHA = re.compile(r"^[0-9a-f]{64}$")
REQUIRED = ("commit", "binary_sha256", "two_host_e2ee", "three_host_e2ee",
            "hundred_peer_soak", "relay_partition_recovery", "security_review",
            "restore_drill", "operator_approval")

def validate(manifest):
    if not isinstance(manifest, dict):
        return ["manifest must be an object"]
    errors = []
    for key in REQUIRED:
        if key not in manifest:
            errors.append("missing " + key)
    if not SHA.fullmatch(str(manifest.get("binary_sha256", ""))):
        errors.append("binary_sha256 must be a lowercase SHA-256")
    if not re.fullmatch(r"[0-9a-f]{40}", str(manifest.get("commit", ""))):
        errors.append("commit must be a full lowercase git SHA")
    for key in REQUIRED[2:]:
        evidence = manifest.get(key)
        if not isinstance(evidence, dict) or evidence.get("passed") is not True:
            errors.append(key + " must have passed=true")
            continue
        if not isinstance(evidence.get("artifact"), str) or not evidence["artifact"].strip():
            errors.append(key + " missing artifact reference")
        if not SHA.fullmatch(str(evidence.get("sha256", ""))):
            errors.append(key + " missing valid artifact sha256")
    if manifest.get("live_execution_enabled") is not False:
        errors.append("live_execution_enabled must be false")
    return errors

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    try:
        errors = validate(json.loads(args.manifest.read_text()))
    except (OSError, ValueError) as exc:
        errors = [str(exc)]
    if errors:
        for error in errors:
            print("BLOCK:", error)
        raise SystemExit(1)
    print("Manifest structure accepted; independent evidence verification still required.")

if __name__ == "__main__":
    main()
