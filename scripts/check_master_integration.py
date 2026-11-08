"""Fail-closed, offline master integration evidence validator.

Validates independently supplied artifact hashes and release gate decisions.
It cannot certify that external tests ran; no live orders are authorized.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path

SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
GATES = (
    "p2p_cross_host", "crypto_negative_tests", "crypto_replay_restart",
    "market_data_authorized", "events_data_authorized", "ai_backtest",
    "risk_limits", "paper_reconciliation", "kill_switch", "operator_rbac",
    "ci_exact_commit", "backup_restore", "security_review",
)
def validate(record, evidence_dir):
    errors = []
    if not isinstance(record, dict):
        return ["manifest must be an object"]
    sha = record.get("commit_sha")
    if not isinstance(sha, str) or not SHA40.fullmatch(sha):
        errors.append("valid exact commit_sha required")
    if record.get("live_trading_enabled") is not False:
        errors.append("live trading must explicitly be disabled")
    gates = record.get("gates")
    if not isinstance(gates, dict):
        return errors + ["gates must be an object"]
    if set(gates) != set(GATES):
        errors.append("gate set must match required master acceptance gates")
    root = Path(evidence_dir).resolve()
    used_artifacts = set()
    for name in GATES:
        gate = gates.get(name)
        if not isinstance(gate, dict):
            errors.append(f"{name}: evidence object required")
            continue
        if gate.get("passed") is not True:
            errors.append(f"{name}: must be passed")
        if gate.get("commit_sha") != sha:
            errors.append(f"{name}: commit mismatch")
        relative = gate.get("artifact")
        digest = gate.get("sha256")
        if not isinstance(relative, str) or not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
            errors.append(f"{name}: invalid artifact path")
            continue
        if not isinstance(digest, str) or not SHA256.fullmatch(digest):
            errors.append(f"{name}: invalid artifact digest")
            continue
        if relative in used_artifacts:
            errors.append(f"{name}: evidence artifact reused across gates")
        used_artifacts.add(relative)
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file() or path.is_symlink():
            errors.append(f"{name}: artifact missing or unsafe")
            continue
        if path.stat().st_size > 10 * 1024 * 1024:
            errors.append(f"{name}: artifact exceeds 10 MiB limit")
            continue
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            errors.append(f"{name}: artifact digest mismatch")
    return errors

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    args = parser.parse_args()
    errors = validate(json.loads(args.manifest.read_text(encoding="utf-8")), args.evidence_dir)
    print(json.dumps({"passed": not errors, "errors": errors}))
    return int(bool(errors))

if __name__ == "__main__":
    raise SystemExit(main())
