"""Validate retained P2P receipt -> ledger import -> paper execution chain.

Does not synthesize acceptance evidence or authorize live execution.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path

SHA = re.compile(r"^[0-9a-f]{40}$")
DIGEST = re.compile(r"^[0-9a-f]{64}$")
STAGES = ("rust_p2p_receipt", "python_ledger_import", "paper_execution")


def validate(report, root):
    errors = []
    if not isinstance(report, dict):
        return ["report must be object"]
    commit = report.get("commit_sha")
    if not SHA.fullmatch(str(commit)):
        errors.append("exact commit required")
    if report.get("live_execution_enabled") is not False:
        errors.append("live execution must be false")
    stages = report.get("stages")
    if not isinstance(stages, dict):
        return errors + ["stages object required"]
    if set(stages) != set(STAGES):
        errors.append("exact three stages required")
    seen = set()
    previous = None
    root = Path(root).resolve()
    for name in STAGES:
        stage = stages.get(name)
        if not isinstance(stage, dict):
            errors.append(f"{name}: missing stage")
            continue
        if stage.get("passed") is not True or stage.get("commit_sha") != commit:
            errors.append(f"{name}: failed or wrong commit")
        if not isinstance(stage.get("ci_run_url"), str) or not re.fullmatch(
            r"https://github\.com/Osiris6900/osiris-eye/actions/runs/[1-9][0-9]*",
            stage["ci_run_url"]
        ):
            errors.append(f"{name}: missing CI run URL")
        rel = stage.get("artifact")
        digest = stage.get("sha256")
        if not isinstance(rel, str) or not rel or Path(rel).is_absolute() or ".." in Path(rel).parts:
            errors.append(f"{name}: invalid path")
            continue
        if rel in seen:
            errors.append(f"{name}: reused artifact")
        seen.add(rel)
        if not DIGEST.fullmatch(str(digest)):
            errors.append(f"{name}: invalid digest")
            continue
        target = (root / rel).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            errors.append(f"{name}: artifact missing or escapes evidence directory")
        elif hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            errors.append(f"{name}: digest mismatch")
        if previous is not None and stage.get("input_receipt_id") != previous:
            errors.append(f"{name}: receipt chain mismatch")
        receipt = stage.get("output_receipt_id")
        if not isinstance(receipt, str) or not receipt.strip():
            errors.append(f"{name}: output receipt id missing")
        previous = receipt
    return errors


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    args = parser.parse_args()
    errors = validate(json.loads(args.manifest.read_text()), args.evidence_dir)
    print(json.dumps({"passed": not errors, "errors": errors}, indent=2))
    raise SystemExit(bool(errors))
