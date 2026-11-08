"""Verify offline release manifest completeness; does not attest external audit.

Required: commit_sha, clean_checkout, ci_passed, security_review_signed,
paper_reconciliation_passed, kill_switch_test_passed, p2p_evidence_sha256,
artifact_sha256, operator_approval. This gate never enables live trading.
"""
import argparse
import json
import re
from pathlib import Path

SHA40 = re.compile(r"^[a-f0-9]{40}$")
SHA256 = re.compile(r"^[a-f0-9]{64}$")
BOOL_GATES = ("clean_checkout", "ci_passed", "security_review_signed",
              "paper_reconciliation_passed", "kill_switch_test_passed",
              "operator_approval")

def validate(manifest):
    if not isinstance(manifest, dict):
        return ["manifest must be an object"]
    errors = []
    if not SHA40.fullmatch(str(manifest.get("commit_sha", ""))):
        errors.append("valid commit_sha required")
    for field in BOOL_GATES:
        if manifest.get(field) is not True:
            errors.append(f"{field} must be true")
    for field in ("p2p_evidence_sha256", "artifact_sha256"):
        if not SHA256.fullmatch(str(manifest.get(field, ""))):
            errors.append(f"{field} must be SHA-256")
    if manifest.get("live_trading_enabled") is not False:
        errors.append("live_trading_enabled must explicitly be false")
    return errors

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("manifest", type=Path)
    a = p.parse_args()
    errors = validate(json.loads(a.manifest.read_text()))
    print(json.dumps({"passed": not errors, "errors": errors}))
    raise SystemExit(bool(errors))
