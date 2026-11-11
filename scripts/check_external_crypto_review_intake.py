#!/usr/bin/env python3
"""Fail-closed validation of an independent cryptography review intake.

Intake completeness is NOT proof of independence, a signed audit, or T06 signoff.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from urllib.parse import urlparse

SHA = re.compile(r"[0-9a-f]{40}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
FIELDS = {
    "schema", "candidate_commit", "reviewer", "scope", "artifacts",
    "findings", "decision", "paper_only",
}
REVIEWER_FIELDS = {"organization", "lead", "independence_attested", "conflicts_disclosed"}
SCOPE_FIELDS = {"p2p_e2ee", "identity_key_lifecycle", "relay_bootstrap", "replay_protection", "threat_model"}
DECISIONS = {"pending", "rejected", "approved_with_remediation", "approved"}


def check(data: object) -> dict:
    failures: list[str] = []
    if not isinstance(data, dict) or set(data) != FIELDS:
        return {"intake_valid": False, "failures": ["invalid_top_level_shape"],
                "independent_review_verified": False, "t06_accepted": False}
    if data["schema"] != "osiris.external_crypto_review_intake.v1":
        failures.append("invalid_schema")
    if not isinstance(data["candidate_commit"], str) or not SHA.fullmatch(data["candidate_commit"]):
        failures.append("invalid_candidate_commit")
    reviewer = data["reviewer"]
    if not isinstance(reviewer, dict) or set(reviewer) != REVIEWER_FIELDS:
        failures.append("invalid_reviewer_shape")
    else:
        for field in ("organization", "lead"):
            if not isinstance(reviewer[field], str) or not reviewer[field].strip():
                failures.append("missing_reviewer_" + field)
        for field in ("independence_attested", "conflicts_disclosed"):
            if type(reviewer[field]) is not bool:
                failures.append("invalid_" + field)
    scope = data["scope"]
    if not isinstance(scope, dict) or set(scope) != SCOPE_FIELDS or any(
        type(value) is not bool for value in scope.values()
    ):
        failures.append("invalid_scope")
    elif not all(scope.values()):
        failures.append("incomplete_scope")
    artifacts = data["artifacts"]
    if not isinstance(artifacts, list) or not artifacts:
        failures.append("missing_artifacts")
    else:
        for item in artifacts:
            if not isinstance(item, dict) or set(item) != {"url", "sha256", "kind"}:
                failures.append("invalid_artifact_shape")
                continue
            url = item["url"]
            parsed = urlparse(url) if isinstance(url, str) else None
            if (not parsed or parsed.scheme != "https" or not parsed.hostname
                or parsed.username or parsed.password or len(url) > 2048):
                failures.append("invalid_artifact_url")
            if not isinstance(item["sha256"], str) or not DIGEST.fullmatch(item["sha256"]):
                failures.append("invalid_artifact_digest")
            if item["kind"] not in ("signed_report", "finding_register", "reviewer_attestation"):
                failures.append("invalid_artifact_kind")
    findings = data["findings"]
    if not isinstance(findings, list) or any(
        not isinstance(f, dict) or set(f) != {"id", "severity", "status"}
        or not isinstance(f["id"], str) or not f["id"].strip()
        or f["severity"] not in ("critical", "high", "medium", "low", "informational")
        or f["status"] not in ("open", "mitigated", "accepted")
        for f in findings
    ):
        failures.append("invalid_findings")
    if data["decision"] not in DECISIONS:
        failures.append("invalid_decision")
    if data["paper_only"] is not True:
        failures.append("paper_only_required")
    return {
        "intake_valid": not failures,
        "failures": failures,
        "independent_review_verified": False,
        "signed_report_cryptographically_verified": False,
        "t06_accepted": False,
        "live_trading_authorized": False,
        "boundary": "schema_only_no_independent_verification",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("intake", type=Path)
    args = parser.parse_args()
    try:
        result = check(json.loads(args.intake.read_text(encoding="utf-8")))
    except (OSError, ValueError) as exc:
        result = {"intake_valid": False, "failures": [str(exc)],
                  "independent_review_verified": False, "t06_accepted": False}
    print(json.dumps(result, sort_keys=True))
    return 0 if result["intake_valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
