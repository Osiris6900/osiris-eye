#!/usr/bin/env python3
"""Fail-closed evidence gate for nine OSIRIS integration workstreams.

Only checks declared artifacts and their SHA-256 digests. Does NOT independently
validate that tests actually ran, hosts are independent, or reviews are genuine.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path

WORKSTREAMS = {
    "dashboard": 363, "paper_account": 364, "p2p_telemetry": 365,
    "network_acceptance": 366, "receipt_to_order": 367,
    "market_data": 368, "model_risk": 369,
    "paper_realism": 370, "recovery_signoff": 371,
}


def evaluate(manifest: object, root: Path) -> dict:
    failures = []
    if not isinstance(manifest, dict) or set(manifest) != {"commit_sha", "workstreams"}:
        return {"status": "blocked", "failures": ["invalid_manifest_shape"], "paper_only": True}
    sha = manifest["commit_sha"]
    if not isinstance(sha, str) or len(sha) != 40 or any(c not in "0123456789abcdef" for c in sha):
        failures.append("invalid_commit_sha")
    entries = manifest["workstreams"]
    if not isinstance(entries, dict) or set(entries) != set(WORKSTREAMS):
        return {"status": "blocked", "failures": failures + ["missing_or_extra_workstreams"], "paper_only": True}
    for name in WORKSTREAMS:
        item = entries[name]
        if not isinstance(item, dict) or set(item) != {"issue", "artifact", "sha256", "reviewed"}:
            failures.append(f"{name}:invalid_entry")
            continue
        if type(item["issue"]) is not int or item["issue"] != WORKSTREAMS[name]:
            failures.append(f"{name}:issue_mismatch")
        if item["reviewed"] is not True:
            failures.append(f"{name}:review_pending")
        rel = item["artifact"]
        digest = item["sha256"]
        if (not isinstance(rel, str) or not rel or Path(rel).is_absolute()
                or any(part in ("..", "") for part in Path(rel).parts)
                or not isinstance(digest, str) or len(digest) != 64
                or any(c not in "0123456789abcdef" for c in digest)):
            failures.append(f"{name}:invalid_artifact_reference")
            continue
        path = root / rel
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 10_000_000:
                raise ValueError("unsafe_or_missing_artifact")
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                failures.append(f"{name}:digest_mismatch")
        except (OSError, ValueError):
            failures.append(f"{name}:artifact_unavailable")
    return {"status": "evidence_present_not_independently_verified" if not failures else "blocked",
            "failures": failures, "paper_only": True,
            "live_trading_authorized": False,
            "independent_acceptance_verified": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args()
    try:
        result = evaluate(json.loads(args.manifest.read_text()), args.root)
    except (OSError, ValueError) as exc:
        result = {"status": "blocked", "failures": [str(exc)], "paper_only": True}
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "evidence_present_not_independently_verified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
