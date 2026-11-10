"""Validate retained Rust receipt -> Python ledger -> paper execution proof.

Content hashes and JSON lineage are checked, but this offline validator
does NOT execute Rust, run a live multi-host test or independently attest
provenance. A passing shape check is never a T19/T20 release signoff.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

SHA = re.compile(r"^[0-9a-f]{40}$")
DIGEST = re.compile(r"^[0-9a-f]{64}$")
RUN_URL = re.compile(
    r"^https://github\.com/Osiris6900/osiris-eye/actions/runs/[1-9][0-9]*$"
)
STAGES = ("rust_p2p_receipt", "python_ledger_import", "paper_execution")
STAGE_SCHEMA = "osiris.cross_module_stage.v1"
MAX_EVIDENCE_BYTES = 1_048_576


def validate(report, root):
    errors = []
    if not isinstance(report, dict):
        return ["report must be object"]
    commit = report.get("commit_sha")
    if not isinstance(commit, str) or not SHA.fullmatch(commit):
        errors.append("exact commit required")
    if report.get("live_execution_enabled") is not False:
        errors.append("live execution must be false")
    if report.get("t19_gate_accepted") is not False:
        errors.append("release gate acceptance must remain false")
    stages = report.get("stages")
    if not isinstance(stages, dict):
        return errors + ["stages object required"]
    if set(stages) != set(STAGES):
        errors.append("exact three stages required")
    seen = set()
    previous = None
    run_url = None
    root = Path(root).resolve()
    for name in STAGES:
        stage = stages.get(name)
        if not isinstance(stage, dict):
            errors.append(f"{name}: missing stage")
            continue
        if stage.get("passed") is not True or stage.get("commit_sha") != commit:
            errors.append(f"{name}: failed or wrong commit")
        url = stage.get("ci_run_url")
        if not isinstance(url, str) or not RUN_URL.fullmatch(url):
            errors.append(f"{name}: missing CI run URL")
        elif run_url is None:
            run_url = url
        elif run_url != url:
            errors.append(f"{name}: CI run URL differs across stages")
        rel = stage.get("artifact")
        digest = stage.get("sha256")
        if not isinstance(rel, str) or not rel or Path(rel).is_absolute() or ".." in Path(rel).parts:
            errors.append(f"{name}: invalid path")
            continue
        if rel in seen:
            errors.append(f"{name}: reused artifact")
        seen.add(rel)
        if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
            errors.append(f"{name}: invalid digest")
            continue
        target = (root / rel).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            errors.append(f"{name}: artifact missing or escapes evidence directory")
        elif target.stat().st_size > MAX_EVIDENCE_BYTES or target.stat().st_size == 0:
            errors.append(f"{name}: evidence exceeds size bound or is empty")
        else:
            raw = target.read_bytes()
            if hashlib.sha256(raw).hexdigest() != digest:
                errors.append(f"{name}: digest mismatch")
            try:
                payload = json.loads(raw)
            except (UnicodeError, ValueError):
                errors.append(f"{name}: invalid stage JSON")
                payload = None
            if not isinstance(payload, dict):
                errors.append(f"{name}: stage evidence must be JSON object")
            else:
                # Do not trust stage ids/receipt lineage only from a top-level
                # manifest. Tie them to the actual digest-bound JSON payload.
                for field, expected in (
                    ("schema", STAGE_SCHEMA),
                    ("stage", name),
                    ("commit_sha", commit),
                    ("ci_run_url", url),
                    ("passed", True),
                    ("output_receipt_id", stage.get("output_receipt_id")),
                    ("input_receipt_id", stage.get("input_receipt_id")),
                    ("live_execution_enabled", False),
                    ("broker_order_submitted", False),
                ):
                    if payload.get(field) != expected:
                        errors.append(f"{name}: payload {field} does not match manifest")
                if name == "paper_execution":
                    if payload.get("mode") != "paper_only":
                        errors.append("paper_execution: paper_only mode required")
                    if payload.get("paper_fill_simulated") is not True:
                        errors.append("paper_execution: paper fill simulation unproven")
        if previous is not None and stage.get("input_receipt_id") != previous:
            errors.append(f"{name}: receipt chain mismatch")
        if previous is None and stage.get("input_receipt_id") is not None:
            errors.append(f"{name}: initial stage cannot claim input receipt")
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
    print(json.dumps({
        "passed_offline_contract": not errors,
        "t19_gate_accepted": False,
        "external_artifact_provenance_verified": False,
        "errors": errors,
    }, indent=2))
    raise SystemExit(bool(errors))
