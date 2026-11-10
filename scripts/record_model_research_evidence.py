#!/usr/bin/env python3
"""Reproducible, synthetic-only model research evidence bundle (T11/T12).

This is a research regression fixture. It is not a vendor-data backtest,
model certification, investment recommendation or live execution approval.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re

from osiris_api.authorized_feeds import (
    SyntheticAuthorizedAdapter, ingest_adapter_ticks, ticks_digest,
)
from osiris_api.walk_forward import (
    MomentumReferenceModel, governance_record, run_walk_forward,
)

SHA40 = re.compile(r"[0-9a-f]{40}\Z")
NOW = datetime(2026, 11, 8, 12, 0, 0, tzinfo=timezone.utc)
START = NOW - timedelta(hours=2)
DATA_FILE = "synthetic-input-ticks.json"
REPORT_FILE = "walk-forward-report.json"
GOVERNANCE_FILE = "research-governance.json"
MANIFEST_FILE = "research-evidence-manifest.json"


def canonical_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_bundle(directory: Path, *, commit_sha: str) -> dict:
    if not isinstance(commit_sha, str) or not SHA40.fullmatch(commit_sha):
        raise ValueError("exact_commit_sha_required")
    directory.mkdir(parents=True, exist_ok=True)
    adapter = SyntheticAuthorizedAdapter(
        seed="t11-t12-ci-reference-v1", symbols=["BTC-USD"], start=START,
        count=32, step_seconds=60,
    )
    admitted = ingest_adapter_ticks(
        adapter, instruments=["BTC-USD"], now=NOW,
    )
    if admitted["counts"] != {"fetched": 32, "admitted": 32, "rejected": 0}:
        raise RuntimeError("synthetic_fixture_admission_failed")
    ticks = admitted["admitted"]
    report = run_walk_forward(
        ticks,
        symbol="BTC-USD",
        model_factory=lambda: MomentumReferenceModel(window=4, scale=0.5),
        now=NOW,
        train_min=8,
        test_size=4,
        step=4,
        cost_bps_per_unit_turnover=10.0,
    )
    record = governance_record(
        model_id="momentum-reference-synthetic-ci",
        model_version="0.1.0",
        params={"window": 4, "scale": 0.5},
        report=report,
    )
    if (
        record.get("status") != "research_only"
        or record.get("approvals") != []
        or record.get("live_trading_authorized") is not False
        or report.get("execution") != "not_simulated"
        or report.get("live_trading_authorized") is not False
    ):
        raise RuntimeError("paper_research_only_invariant_failed")

    records = {
        DATA_FILE: ticks,
        REPORT_FILE: report,
        GOVERNANCE_FILE: record,
    }
    for filename, payload in records.items():
        (directory / filename).write_bytes(canonical_bytes(payload))
    manifest = {
        "schema": "osiris.synthetic_model_research_archive.v1",
        "commit_sha": commit_sha,
        "source": "synthetic-deterministic-fixture",
        "vendor_data_used": False,
        "model_id": record["model_id"],
        "model_version": record["model_version"],
        "model_approval": "PENDING",
        "license_signoff": "PENDING",
        "independent_leakage_review": "PENDING",
        "independent_model_risk_review": "PENDING",
        "t11_t12_accepted": False,
        "live_trading_authorized": False,
        "paper_research_only": True,
        "observations": len(ticks),
        "folds": report["aggregate"]["folds"],
        "transaction_cost_bps_per_unit_turnover": report["config"][
            "cost_bps_per_unit_turnover"
        ],
        "data_sha256": ticks_digest(ticks),
        "report_sha256": report["report_sha256"],
        "files_sha256": {
            filename: sha256(directory / filename)
            for filename in records
        },
        "limitations": [
            "synthetic deterministic input only; no licensed real market history",
            "reference research model only; no independent review or approval",
            "no market impact, exchange fees, latency distribution or live fills",
            "in-run file hashes do not authenticate a remote CI run or artifact",
        ],
    }
    (directory / MANIFEST_FILE).write_bytes(canonical_bytes(manifest))
    verify_bundle(directory, expected_commit=commit_sha)
    (directory / "sha256sum.txt").write_text(
        "".join(
            f"{sha256(directory / filename)}  {filename}\n"
            for filename in sorted([*records, MANIFEST_FILE])
        ), encoding="utf-8",
    )
    return manifest


def verify_bundle(directory: Path, *, expected_commit: str) -> dict:
    if not isinstance(expected_commit, str) or not SHA40.fullmatch(expected_commit):
        raise ValueError("exact_commit_sha_required")
    manifest = json.loads((directory / MANIFEST_FILE).read_text(encoding="utf-8"))
    if (
        manifest.get("schema") != "osiris.synthetic_model_research_archive.v1"
        or manifest.get("commit_sha") != expected_commit
        or manifest.get("source") != "synthetic-deterministic-fixture"
        or manifest.get("vendor_data_used") is not False
        or manifest.get("t11_t12_accepted") is not False
        or manifest.get("live_trading_authorized") is not False
        or manifest.get("paper_research_only") is not True
    ):
        raise ValueError("model_archive_contract_invalid")
    expected_files = {DATA_FILE, REPORT_FILE, GOVERNANCE_FILE}
    hashes = manifest.get("files_sha256")
    if not isinstance(hashes, dict) or set(hashes) != expected_files:
        raise ValueError("model_archive_files_invalid")
    for filename, expected_sha in hashes.items():
        if not isinstance(expected_sha, str) or len(expected_sha) != 64:
            raise ValueError("model_archive_digest_invalid")
        if sha256(directory / filename) != expected_sha:
            raise ValueError("model_archive_file_hash_mismatch")

    ticks = json.loads((directory / DATA_FILE).read_text(encoding="utf-8"))
    report = json.loads((directory / REPORT_FILE).read_text(encoding="utf-8"))
    record = json.loads((directory / GOVERNANCE_FILE).read_text(encoding="utf-8"))
    if ticks_digest(ticks) != manifest["data_sha256"]:
        raise ValueError("model_archive_data_hash_mismatch")
    raw_report = {key: value for key, value in report.items() if key != "report_sha256"}
    actual_report_sha = hashlib.sha256(
        json.dumps(raw_report, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if (
        report.get("report_sha256") != actual_report_sha
        or record.get("report_sha256") != actual_report_sha
        or record.get("data_sha256") != manifest["data_sha256"]
        or record.get("approvals") != []
        or record.get("live_trading_authorized") is not False
        or record.get("status") != "research_only"
        or report.get("execution") != "not_simulated"
        or report.get("live_trading_authorized") is not False
    ):
        raise ValueError("model_archive_lineage_or_safety_invalid")
    return {
        "consistent": True,
        "commit_sha": expected_commit,
        "t11_t12_accepted": False,
        "independent_model_reviewed": False,
        "live_trading_authorized": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--commit-sha", required=True)
    args = parser.parse_args()
    manifest = make_bundle(args.output_dir, commit_sha=args.commit_sha)
    print(json.dumps({
        "schema": manifest["schema"],
        "commit_sha": manifest["commit_sha"],
        "observations": manifest["observations"],
        "folds": manifest["folds"],
        "t11_t12_accepted": False,
        "live_trading_authorized": False,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
