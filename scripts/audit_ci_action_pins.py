#!/usr/bin/env python3
"""Offline inventory of GitHub Actions references; no network calls."""
from __future__ import annotations
import argparse
import json
import re
from pathlib import Path

USES = re.compile(r"^\s*(?:-\s*)?uses\s*:\s*['\"]?([^\s'\"#]+)", re.MULTILINE)
SHA = re.compile(r"^[0-9a-fA-F]{40}$")
DIGEST = re.compile(r"^sha256:[0-9a-fA-F]{64}$")


def classify(ref: str) -> str:
    if ref.startswith("./"):
        return "local"
    if ref.startswith("docker://"):
        return "pinned" if "@"+ "sha256:" in ref and DIGEST.fullmatch(ref.rsplit("@", 1)[1]) else "mutable"
    if "@" not in ref:
        return "invalid"
    return "pinned" if SHA.fullmatch(ref.rsplit("@", 1)[1]) else "mutable"


def audit(root: Path) -> dict:
    records = []
    for path in sorted(root.glob("*.y*ml")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            # Intentionally only static uses values; expressions are rejected.
            match = USES.match(line)
            if match:
                ref = match.group(1)
                records.append({"file": path.name, "line": number, "reference": ref, "status": classify(ref)})
    return {"schema": "osiris.ci_action_pinning_audit.v1", "references": records,
            "counts": {status: sum(x["status"] == status for x in records)
                       for status in ("pinned", "mutable", "invalid", "local")}}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workflow-dir", type=Path, default=Path(".github/workflows"))
    parser.add_argument("--strict", action="store_true", help="fail if any mutable or invalid reference remains")
    args = parser.parse_args()
    if not args.workflow_dir.is_dir():
        parser.error("workflow directory does not exist")
    report = audit(args.workflow_dir)
    print(json.dumps(report, indent=2, sort_keys=True))
    return int(args.strict and (report["counts"]["mutable"] or report["counts"]["invalid"]))


if __name__ == "__main__":
    raise SystemExit(main())
