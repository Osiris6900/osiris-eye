#!/usr/bin/env python3
"""One-command fail-closed verification for Osiris Eye.

The default is a dry run. Pass --execute to run the checks. This does not
upgrade dependencies, download artifacts, submit broker orders, commit, or merge.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECKS = [
    ("python-tests", ["python", "-m", "pytest", "-q", "services/quant-api/tests"], None),
    ("release-inventory", ["python", "scripts/check_release_gate_status.py"], None),
    ("release-strict", ["python", "scripts/check_release_gate_status.py", "--strict"], None),
    ("p2p-workflows", ["python", "scripts/check_p2p_workflows.py"], None),
    ("rust-risk", ["cargo", "test", "--locked", "--manifest-path", "crates/risk-kernel/Cargo.toml"], None),
    ("rust-p2p", ["cargo", "test", "--locked", "--manifest-path", "services/p2p-node/Cargo.toml", "--all-targets"], None),
    ("frontend-typecheck", ["npm", "run", "typecheck"], "apps/web"),
    ("frontend-tests", ["npm", "test", "--", "--run"], "apps/web"),
    ("frontend-build", ["npm", "run", "build"], "apps/web"),
]

# This profile exercises local implementations. It never substitutes for the
# default strict release profile, vendor rights or independent-host acceptance.
ENGINEERING_CHECKS = [
    ("release-inventory", ["python", "scripts/check_release_gate_status.py"], None),
    ("supply-chain-sbom", ["python", "scripts/generate_dependency_sbom.py", "--offline", "--crate-cache", "docs/CRATE_LICENSE_PROVENANCE.json", "--output", "@artifacts@/dependency-sbom.json", "--provenance-output", "@artifacts@/dependency-sbom-provenance.json"], None),
    ("p2p-workflows", ["python", "scripts/check_p2p_workflows.py"], None),
    ("rust-risk", ["cargo", "test", "--locked", "--manifest-path", "crates/risk-kernel/Cargo.toml"], None),
    ("rust-p2p", ["cargo", "test", "--locked", "--manifest-path", "services/p2p-node/Cargo.toml", "--all-targets"], None),
    ("rust-native", ["cargo", "test", "--locked", "--manifest-path", "crates/native-feed/Cargo.toml"], None),
    ("native-build", ["cargo", "build", "--locked", "--manifest-path", "crates/native-feed/Cargo.toml"], None),
    ("p2p-build", ["cargo", "build", "--locked", "--manifest-path", "services/p2p-node/Cargo.toml", "--bins"], None),
    ("python-tests", ["python", "-m", "pytest", "-q", "services/quant-api/tests", "tests/test_ultra_fast_feed.py"], None),
    ("native-process", ["python", "scripts/test_native_feed_process.py"], None),
    ("peer-receipts", ["python", "scripts/test_p2p_delivery_receipts.py"], None),
    ("receipt-paper", ["python", "scripts/run_receipt_paper_acceptance.py", "--output", "@artifacts@/receipt-paper.json"], None),
    ("market-paper", ["python", "scripts/run_market_paper_acceptance.py", "--output", "@artifacts@/market-paper.json"], None),
    ("local-paper-dr", ["python", "scripts/run_paper_dr_exercise.py", "--output-dir", "@artifacts@/paper-dr"], None),
    ("local-dr-integrity", ["python", "scripts/verify_paper_dr_artifacts.py", "@artifacts@/paper-dr"], None),
    ("portable-recovery", ["python", "-m", "unittest", "discover", "-s", "scripts", "-p", "test_check_paper_cross_runner_restore.py"], None),
    ("paper-failures", ["python", "-m", "unittest", "discover", "-s", "scripts", "-p", "test_run_paper_failure_exercise.py"], None),
    ("model-archive", ["python", "scripts/record_model_research_evidence.py", "--output-dir", "@artifacts@/model-research", "--commit-sha", "@commit@"], None),
    ("recovery-intakes", ["python", "-m", "unittest", "discover", "-s", "scripts", "-p", "test_verify_cross_runner_dr_artifacts.py"], None),
    ("security-intake", ["python", "-m", "unittest", "discover", "-s", "scripts", "-p", "test_check_external_crypto_review_intake.py"], None),
    ("vendor-intake", ["python", "-m", "unittest", "discover", "-s", "scripts", "-p", "test_check_vendor_entitlement_evidence.py"], None),
    ("frontend-typecheck", ["npm", "run", "typecheck"], "apps/web"),
    ("frontend-tests", ["npm", "test"], "apps/web"),
    ("frontend-build", ["npm", "run", "build"], "apps/web"),
    ("browser", ["bash", "scripts/run_platform20_browser.sh"], None),
]
WORKSTREAM_CHECKS = {
    "W01": ["python-tests", "vendor-intake"], "W02": ["python-tests", "rust-risk", "market-paper", "receipt-paper"],
    "W03": ["python-tests", "frontend-tests", "frontend-build", "browser"],
    "W04": ["python-tests", "frontend-tests", "browser"], "W05": ["python-tests", "security-intake"],
    "W06": ["python-tests", "recovery-intakes"], "W07": ["python-tests"],
    "W08": ["rust-p2p", "peer-receipts", "receipt-paper", "python-tests"], "W09": ["python-tests"],
    "W10": ["python-tests"], "W11": ["python-tests"], "W12": ["python-tests"],
    "W13": ["python-tests", "market-paper", "paper-failures"], "W14": ["python-tests", "browser"],
    "W15": ["python-tests", "browser"], "W16": ["python-tests", "market-paper", "receipt-paper"],
    "W17": ["python-tests"], "W18": ["python-tests", "recovery-intakes", "local-paper-dr", "local-dr-integrity", "portable-recovery"],
    "W19": ["python-tests", "security-intake"], "W20": ["rust-native", "native-process", "python-tests"],
}

# These checks exercise implementations; inventory acceptance is a separate
# historical record and must never be inferred from a green local check.
RELEASE_GATE_CHECKS = {
    "T01": ["release-inventory", "p2p-workflows", "supply-chain-sbom"],
    "T02": ["p2p-workflows"], "T03": ["rust-p2p", "peer-receipts"],
    "T04": ["rust-p2p", "peer-receipts"], "T05": ["rust-p2p"],
    "T06": ["security-intake"], "T07": ["python-tests", "vendor-intake"],
    "T08": ["python-tests"], "T09": ["vendor-intake"], "T10": ["python-tests"],
    "T11": ["python-tests", "model-archive"], "T12": ["python-tests", "model-archive"],
    "T13": ["python-tests", "rust-risk", "receipt-paper"],
    "T14": ["python-tests", "market-paper", "receipt-paper"],
    "T15": ["python-tests", "market-paper", "receipt-paper"],
    "T16": ["python-tests", "paper-failures", "receipt-paper"],
    "T17": ["browser", "peer-receipts", "python-tests"],
    "T18": ["local-paper-dr", "local-dr-integrity", "portable-recovery", "recovery-intakes"],
    "T19": ["receipt-paper", "market-paper", "peer-receipts", "paper-failures"],
    "T20": ["release-inventory", "security-intake", "vendor-intake"],
}


def run_check(name, command, cwd, timeout, extra_env=None):
    executable = shutil.which(command[0])
    if executable is None:
        return {"name": name, "status": "blocked", "reason": "tool unavailable: " + command[0]}
    try:
        result = subprocess.run(command, cwd=ROOT / cwd if cwd else ROOT,
                                capture_output=True, text=True, timeout=timeout,
                                check=False, env=None if extra_env is None else {**os.environ, **extra_env})
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"name": name, "status": "blocked", "reason": str(exc)}
    return {"name": name, "status": "passed" if result.returncode == 0 else "failed",
            "exit_code": result.returncode,
            "output_tail": (result.stdout + "\n" + result.stderr)[-4000:]}


def source_snapshot():
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=ROOT, timeout=10)
    names = git("ls-files", "-z", "--cached", "--others", "--exclude-standard").decode().split("\0")
    hashes = {}
    for name in sorted(set(names) - {""}):
        path = ROOT / name
        if path.is_file() and not path.is_symlink():
            value = hashlib.sha256()
            with path.open("rb") as file:
                for chunk in iter(lambda: file.read(1024 * 1024), b""):
                    value.update(chunk)
            hashes[name] = value.hexdigest()
    return {"git_head": git("rev-parse", "HEAD").decode().strip(),
            "git_tree": git("rev-parse", "HEAD^{tree}").decode().strip(),
            "dirty": bool(git("status", "--porcelain")), "files_sha256": hashes,
            "worktree_sha256": hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()}


def engineering_report(results, before, after):
    report = build_report(results)
    by_name = {item["name"]: item["status"] for item in results}
    workstreams = {name: {"checks": checks, "engineering_checks_passed": all(by_name.get(check) == "passed" for check in checks),
                         "independent_acceptance": "pending"} for name, checks in WORKSTREAM_CHECKS.items()}
    stable = before == after
    inventory = json.loads((ROOT / "docs/release-gate-status.json").read_text())
    release_gates = {
        row["id"]: {
            "checks": RELEASE_GATE_CHECKS[row["id"]],
            "local_checks_passed": all(by_name.get(check) == "passed"
                                       for check in RELEASE_GATE_CHECKS[row["id"]]),
            "inventory_status": row["status"],
            "remaining_requirement": row["blocker"],
            "current_candidate_acceptance_inferred": False,
        } for row in inventory["items"]
    }
    inventory_complete = set(by_name) == {name for name, _, _ in ENGINEERING_CHECKS} and len(results) == len(ENGINEERING_CHECKS)
    report.update(profile="engineering", source=after, source_unchanged=stable, workstreams=workstreams,
                  check_inventory_complete=inventory_complete,
                  release_gates=release_gates,
                  remaining_release_gates=[row["id"] for row in inventory["items"] if not row["acceptance_satisfied"]],
                  all_passed=report["all_passed"] and stable and inventory_complete and all(row["engineering_checks_passed"] for row in workstreams.values()),
                  release_accepted=False, completion_scope="local_implementation_checks_only")
    return report


def build_report(results):
    """A local verification report never authorizes live trading or a merge."""
    return {
        "schema": "osiris.verification.v1",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "live_trading_authorized": False,
        "merge_authorized": False,
        "checks": results,
        "all_passed": bool(results) and all(item["status"] == "passed" for item in results),
        "external_evidence_required": [
            "independent-host 100-peer soak", "cryptographic security audit",
            "deployment and recovery evidence", "GitHub CI and required reviews",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Show checks without executing")
    parser.add_argument("--execute", action="store_true", help="Run the checks. Omit to dry-run.")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--profile", choices=["release", "engineering"], default="release")
    parser.add_argument("--report", type=Path, help="Optional JSON report path")
    args = parser.parse_args()
    if args.timeout < 1:
        parser.error("--timeout must be positive")
    if args.execute and args.dry_run:
        parser.error("pass only one of --execute and --dry-run")
    if args.report and args.report.exists():
        parser.error("report path exists; refusing overwrite")
    checks = CHECKS if args.profile == "release" else ENGINEERING_CHECKS
    if not (ROOT / "crates/risk-kernel/Cargo.toml").is_file():
        parser.error("Run from an Osiris Eye checkout containing the risk kernel")
    if not (ROOT / "services/quant-api/tests").is_dir():
        parser.error("paper and API tests are missing")
    if not args.execute:
        for name, command, cwd in checks:
            print(f"PLAN {name}: {command} (cwd={cwd or '.'})")
        return 0

    before = source_snapshot() if args.profile == "engineering" else None
    artifacts = Path(tempfile.mkdtemp(prefix="osiris-engineering-evidence-")) if before else None
    results = []
    for name, command, cwd in checks:
        print("RUN " + name, file=sys.stderr, flush=True)
        environment = None
        if args.profile == "engineering":
            command = [part.replace("@artifacts@", str(artifacts)).replace("@commit@", before["git_head"]) for part in command]
            environment = {
                "OSIRIS_TEST_PYTHON": sys.executable,
                "OSIRIS_P2P_TEST_BINARY": str(ROOT / "services/p2p-node/target/debug/osiris-p2p-node"),
                "OSIRIS_NATIVE_TEST_BINARY": str(ROOT / "crates/native-feed/target/debug/osiris-native-feed"),
                "OSIRIS_BROWSER_ARTIFACTS": str(artifacts / "browser"),
            }
        results.append(run_check(name, command, cwd, args.timeout, environment))
    report = build_report(results) if before is None else engineering_report(results, before, source_snapshot())
    if artifacts:
        report["artifact_directory"] = str(artifacts)
        report["artifacts_sha256"] = {str(path.relative_to(artifacts)): hashlib.sha256(path.read_bytes()).hexdigest()
                                      for path in sorted(artifacts.rglob("*")) if path.is_file() and not path.is_symlink()}
    print(json.dumps(report, indent=2))
    if args.report:
        path = args.report.resolve()
        if path.exists():
            parser.error("report path exists; refusing overwrite")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
