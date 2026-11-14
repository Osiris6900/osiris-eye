#!/usr/bin/env python3
"""Fail-closed, offline OSIRIS release evidence inventory.

This checks evidence references, not whether a deployment or security audit passed.
``ready`` means reference preflight completeness only. Review declarations are
required for the independent decision gates, but are not verified approvals.
Run: python scripts/check_release_evidence.py path/to/release-evidence.json
"""
import json
import re
import sys
from pathlib import Path

GATES = (
    "dependency_security", "ci_immutable_actions", "p2p_final_host_e2ee",
    "p2p_outage_load", "p2p_public_operations", "independent_crypto_audit",
    "authorized_market_data", "streaming_data_quality", "feed_entitlements",
    "event_provenance", "model_lifecycle", "strategy_oos_validation",
    "portfolio_risk", "broker_sandbox_oms", "paper_fill_accounting",
    "kill_switch_failure_tests", "operator_auth_dashboard",
    "observability_backups_dr", "capacity_release_ci", "compliance_signoff",
)
# These decisions are required by the release inventory. A caller cannot make
# them optional by omitting or clearing independent_review_required.
INDEPENDENT_REVIEW_GATES = frozenset({
    "independent_crypto_audit", "feed_entitlements", "model_lifecycle",
    "strategy_oos_validation", "portfolio_risk", "compliance_signoff",
})
SHA = re.compile(r"^[0-9a-f]{40}$")
DIGEST = re.compile(r"^[0-9a-f]{64}$")
URL = re.compile(r"^https://[^\s]+$")


def report(errors, verified):
    return {"ready": not errors, "errors": errors, "verified_gates": verified,
            "gate_count": len(GATES), "evidence_only": True,
            "independent_review_verified": False, "release_accepted": False,
            "live_trading_authorized": False,
            "warning": "References and hashes are syntax-checked, not fetched or independently verified."}


def evaluate(payload):
    errors = []
    if not isinstance(payload, dict):
        return report(["manifest must be an object"], [])
    candidate = payload.get("candidate_commit")
    if not isinstance(candidate, str) or not SHA.fullmatch(candidate):
        errors.append("candidate_commit must be a full lowercase Git commit SHA")
    gates = payload.get("gates")
    if not isinstance(gates, dict):
        return report(errors + ["gates must be an object"], [])
    if any(not isinstance(name, str) for name in gates):
        return report(errors + ["gate names must be strings"], [])
    unknown = sorted(set(gates) - set(GATES))
    if unknown:
        errors.append("unknown gates: " + ", ".join(unknown))
    verified = []
    for name in GATES:
        item = gates.get(name)
        if not isinstance(item, dict):
            errors.append(name + ": missing evidence object")
            continue
        if item.get("status") != "passed":
            errors.append(name + ": status is not passed")
            continue
        if item.get("candidate_commit") != candidate:
            errors.append(name + ": evidence candidate differs from release")
            continue
        malformed_review = False
        for field in ("independent_review_required", "independent_reviewed"):
            if field in item and type(item[field]) is not bool:
                errors.append(name + ": " + field + " must be boolean")
                malformed_review = True
        if malformed_review:
            continue
        review_required = (name in INDEPENDENT_REVIEW_GATES
                           or item.get("independent_review_required") is True)
        if review_required and item.get("independent_reviewed") is not True:
            errors.append(name + ": independent review missing")
            continue
        evidence = item.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            errors.append(name + ": evidence list empty")
            continue
        valid = all(
            isinstance(e, dict)
            and isinstance(e.get("url"), str) and URL.fullmatch(e["url"])
            and isinstance(e.get("sha256"), str) and DIGEST.fullmatch(e["sha256"])
            for e in evidence
        )
        if not valid:
            errors.append(name + ": each evidence item needs HTTPS URL and SHA-256")
            continue
        verified.append(name)
    return report(errors, verified)


def main(argv):
    if len(argv) != 2:
        print("Usage: check_release_evidence.py manifest.json", file=sys.stderr)
        return 2
    try:
        data = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(json.dumps(report([str(exc)], [])))
        return 2
    result = evaluate(data)
    print(json.dumps(result, indent=2))
    return 0 if result["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
