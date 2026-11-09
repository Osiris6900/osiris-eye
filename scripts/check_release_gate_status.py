#!/usr/bin/env python3
"""Offline inventory check for the #207 / #182 release-gate status.

Default mode exits 0 when the checked-in inventory agrees with repository
facts this process can see. A blocked gate does not fail this mode.

``--strict`` exits 1 unless every tracker item is ``Implemented with evidence``.
CI uses the default mode. This script does not authorize live trading.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "docs" / "release-gate-status.json"
DOC_PATH = ROOT / "docs" / "INTEGRATED_DELIVERY_GATES_2026_11_08.md"
ADVISORY_PATH = ROOT / "docs" / "P2P_DEPENDENCY_ADVISORIES.json"
SBOM_PATH = ROOT / "scripts" / "generate_dependency_sbom.py"
LOCK_PATH = ROOT / "services" / "quant-api" / "requirements-lock.txt"
CARGO_LOCK = ROOT / "services" / "p2p-node" / "Cargo.lock"
STATUSES = {"Implemented with evidence", "Partial", "Remaining"}
SHA40 = re.compile(r"^[0-9a-f]{40}$")
CI_RUN_URL = re.compile(r"^https://github\.com/Osiris6900/osiris-eye/actions/runs/[1-9][0-9]*$")
USE = re.compile(r"^\s*(?:-\s*)?uses:\s*['\"]?([^\s#'\"]+)", re.MULTILINE)
CARGO_PACKAGES = ("hickory-proto", "hickory-resolver", "yamux", "paste")


def action_counts(root: Path) -> tuple[int, int, int]:
    files = sorted((root / ".github" / "workflows").glob("*.yml"))
    pinned = mutable = 0
    for path in files:
        for action in USE.findall(path.read_text(encoding="utf-8")):
            if action.startswith("./"):
                continue
            revision = action.rsplit("@", 1)[-1] if "@" in action else ""
            if len(revision) == 40 and all(c in "0123456789abcdefABCDEF" for c in revision):
                pinned += 1
            else:
                mutable += 1
    return len(files), pinned, mutable


def cargo_versions(path: Path) -> dict[str, list[str]]:
    found = {name: [] for name in CARGO_PACKAGES}
    current = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("name = "):
            current = line.split('"', 2)[1]
        elif line.startswith("version = ") and current in found:
            found[current].append(line.split('"', 2)[1])
            current = None
    return found


def collect_facts(root: Path) -> dict:
    files, pinned, mutable = action_counts(root)
    advisory = json.loads((root / "docs" / "P2P_DEPENDENCY_ADVISORIES.json").read_text(encoding="utf-8"))
    inputs = advisory["source"]["inputs"]
    match = True
    for relative, meta in inputs.items():
        digest = hashlib.sha256((root / relative).read_bytes()).hexdigest()
        match = match and digest == meta["sha256"]
    versions = cargo_versions(root / "services" / "p2p-node" / "Cargo.lock")
    sbom = (root / "scripts" / "generate_dependency_sbom.py").read_text(encoding="utf-8")
    lock = (root / "services" / "quant-api" / "requirements-lock.txt").read_text(encoding="utf-8")
    return {
        "workflow_files": files,
        "pinned_action_uses": pinned,
        "mutable_action_uses": mutable,
        "advisory_inputs_match_worktree": match,
        "advisory_source_commit": advisory["source"]["commit"],
        "sbom_marks_python_unresolved": (
            '{"name": "osiris:python-deployment-resolution", "value": "unresolved"}' in sbom
        ),
        "python_lock_schema": lock.splitlines()[0].removeprefix("# schema: ").strip(),
        "cargo_versions": {name: versions[name] for name in CARGO_PACKAGES},
    }


def validate(manifest: dict, facts: dict) -> list[str]:
    errors = []
    if manifest.get("schema") != "osiris.release-gate-inventory.v1":
        errors.append("schema mismatch")
    if manifest.get("live_trading_authorized") is not False:
        errors.append("live_trading_authorized must be false")
    if not SHA40.fullmatch(str(manifest.get("baseline_commit", ""))):
        errors.append("baseline_commit must be a 40-character sha")
    declared = manifest.get("facts")
    if not isinstance(declared, dict):
        return errors + ["facts object required"]
    for key in (
        "mutable_action_uses",
        "pinned_action_uses",
        "workflow_files",
        "advisory_inputs_match_worktree",
        "advisory_source_commit",
        "sbom_marks_python_unresolved",
        "python_lock_schema",
    ):
        if declared.get(key) != facts.get(key):
            errors.append(f"facts.{key} does not match the worktree (declared={declared.get(key)!r}, actual={facts.get(key)!r})")
    expected_versions = {}
    for name in CARGO_PACKAGES:
        seen = facts["cargo_versions"][name]
        expected_versions[name] = seen[0] if len(seen) == 1 else seen
    if declared.get("cargo_versions") != expected_versions:
        errors.append("facts.cargo_versions do not match Cargo.lock")
    items = manifest.get("items")
    if not isinstance(items, list) or [item.get("id") for item in items] != [f"T{i:02d}" for i in range(1, 21)]:
        return errors + ["items must be T01 through T20 in order"]
    for item in items:
        status = item.get("status")
        if status not in STATUSES:
            errors.append(f"{item.get('id')}: invalid status")
            continue
        if item.get("acceptance_satisfied") is not (status == "Implemented with evidence"):
            errors.append(f"{item.get('id')}: acceptance_satisfied must match implemented status")
        if status == "Implemented with evidence":
            evidence = item.get("evidence")
            commits = item.get("evidence_commits")
            if not isinstance(evidence, list) or not any(
                isinstance(entry, str) and CI_RUN_URL.fullmatch(entry)
                for entry in evidence
            ):
                errors.append(f"{item.get('id')}: implemented status requires a passing CI run URL")
            if not isinstance(commits, list) or not any(isinstance(entry, str) and SHA40.fullmatch(entry) for entry in commits):
                errors.append(f"{item.get('id')}: implemented status requires a merged commit SHA")
        elif not isinstance(item.get("blocker"), str) or not item["blocker"].strip():
            errors.append(f"{item.get('id')}: blocker required")
        if not isinstance(item.get("open_prs"), list) or any(not isinstance(number, int) for number in item["open_prs"]):
            errors.append(f"{item.get('id')}: open_prs must be integers")
    return errors


def validate_document(manifest: dict, document: str) -> list[str]:
    errors = []
    for item in manifest["items"]:
        matched = False
        for line in document.splitlines():
            if item["id"] in line and item["status"] in line and item["tracker_item"] in line:
                matched = True
                break
        if not matched:
            errors.append(f"{item['id']}: status table row missing from the gate doc")
    return errors


def commit_exists(sha: str) -> bool:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--refresh-facts", action="store_true", help="Update checked-in inventory facts from this worktree; preserve gate statuses and evidence")
    parser.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    args = parser.parse_args(argv)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    facts = collect_facts(ROOT)
    if args.refresh_facts:
        manifest["facts"] = {
            "mutable_action_uses": facts["mutable_action_uses"],
            "pinned_action_uses": facts["pinned_action_uses"],
            "workflow_files": facts["workflow_files"],
            "advisory_inputs_match_worktree": facts["advisory_inputs_match_worktree"],
            "advisory_source_commit": facts["advisory_source_commit"],
            "sbom_marks_python_unresolved": facts["sbom_marks_python_unresolved"],
            "python_lock_schema": facts["python_lock_schema"],
            "cargo_versions": {name: values[0] if len(values) == 1 else values for name, values in facts["cargo_versions"].items()},
        }
        if not commit_exists(manifest.get("baseline_commit", "")):
            baseline = subprocess.run(["git", "rev-parse", "--verify", "HEAD^{commit}"], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()
            manifest["baseline_commit"] = baseline
        args.manifest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        print(f"Refreshed inventory facts in {args.manifest}; review and commit this change before CI.")
    errors = validate(manifest, facts)
    errors.extend(validate_document(manifest, DOC_PATH.read_text(encoding="utf-8")))
    if not commit_exists(manifest.get("baseline_commit", "")):
        errors.append("baseline_commit is not in this repository")
    if args.strict:
        pending = [item["id"] for item in manifest.get("items", []) if item.get("status") != "Implemented with evidence"]
        if pending:
            errors.append("strict mode blocked by " + ", ".join(pending))
    counts = {}
    for item in manifest.get("items", []):
        counts[item.get("status")] = counts.get(item.get("status"), 0) + 1
    print(json.dumps({
        "passed": not errors,
        "baseline_commit": manifest.get("baseline_commit"),
        "live_trading_authorized": manifest.get("live_trading_authorized"),
        "counts": counts,
        "errors": errors,
    }, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
