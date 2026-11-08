"""Fail-closed release evidence gate for P2P, security and paper execution."""
from __future__ import annotations
import json
import sys
from pathlib import Path

REQUIRED = ("p2p_multi_host_soak", "p2p_outage_recovery", "ci_action_pinning", "dependency_audit", "paper_ledger_reconciliation")

def assess(evidence: dict) -> dict:
    if not isinstance(evidence, dict) or evidence.get("schema") != "osiris.release_evidence.v1":
        raise ValueError("invalid release evidence schema")
    checks = evidence.get("checks")
    if not isinstance(checks, dict):
        raise ValueError("checks must be an object")
    missing = [name for name in REQUIRED if checks.get(name) is not True]
    return {"ready": not missing, "missing": missing, "scope": "paper_and_network_only"}

if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python scripts/check_release_readiness.py evidence.json")
    report = assess(json.loads(Path(sys.argv[1]).read_text()))
    print(json.dumps(report, sort_keys=True))
    raise SystemExit(0 if report["ready"] else 1)
