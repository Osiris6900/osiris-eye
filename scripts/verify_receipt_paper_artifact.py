#!/usr/bin/env python3
"""Verify retained local receipt/paper evidence against exact committed sources.

The archive digest and checkout SHA must come from a trusted caller. This
checks source completeness and trace consistency, not CI/reviewer identity,
truth of the reported run, independent hosts, vendor rights or release approval.
No artifact content is executed, extracted, or imported as Python code.
"""
from __future__ import annotations

import argparse
import io
import json
import subprocess
import zipfile
from datetime import datetime
from pathlib import Path

from verify_dependency_sbom import LIMIT, SHA40, SHA256, digest, document, git, read_artifact

ROOT = Path(__file__).resolve().parents[1]
FILES = {"receipt-paper.json", "receipt-paper.json.sha256"}
SCOPE = "two-local-rust-nodes-and-independent-local-python-processes"
CHECKS = {
    "actual_rust_e2ee_and_recipient_receipt", "kernel_pinned_event_persisted",
    "no_paper_execution_before_acknowledgement", "actual_child_loss_after_fill_commit",
    "new_process_idempotent_recovery", "receipt_to_risk_decision_to_fill_trace",
    "exact_fee_inclusive_round_trip_pnl", "canonical_admin_account_matches",
    "replay_and_changed_economics_guard", "current_kill_switch_and_revocation_guard",
}


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def fields(record, expected, context):
    require(isinstance(record, dict), context + " must be an object")
    for name, value in expected.items():
        require(type(record.get(name)) is type(value) and record[name] == value,
                context + "." + name + " mismatch")


def committed_source(repository: Path, commit: str) -> dict:
    """Hash every regular tracked file, using Git objects rather than the worktree."""
    require(isinstance(commit, str) and SHA40.fullmatch(commit), "exact checkout SHA required")
    entries = git(repository, "ls-tree", "-rz", "--full-tree", commit).split(b"\0")
    objects = []
    for entry in filter(None, entries):
        metadata, name = entry.split(b"\t", 1)
        mode, kind, oid = metadata.split()
        # source_snapshot deliberately excludes symlinks and submodules too.
        if mode not in (b"100644", b"100755"):
            continue
        require(kind == b"blob", "regular source must be a blob")
        objects.append((name.decode("utf-8"), oid))
    require(0 < len(objects) <= 10000, "invalid source inventory size")
    result = subprocess.run(["git", "cat-file", "--batch"], cwd=repository,
                            input=b"".join(oid + b"\n" for _, oid in objects),
                            capture_output=True, check=True, timeout=30)
    stream = io.BytesIO(result.stdout)
    hashes = {}
    for name, oid in objects:
        actual_oid, kind, size = stream.readline().split()
        require(actual_oid == oid and kind == b"blob", "source object mismatch")
        raw = stream.read(int(size))
        require(len(raw) == int(size) and stream.read(1) == b"\n", "incomplete source object")
        hashes[name] = digest(raw)
    require(not stream.read(), "unexpected source objects")
    return {"git_head": commit,
            "git_tree": git(repository, "rev-parse", f"{commit}^{{tree}}").decode().strip(),
            "dirty": False, "files_sha256": hashes,
            "worktree_sha256": digest(json.dumps(hashes, sort_keys=True).encode())}


def audit_rows(account: dict) -> dict:
    rows = account.get("audit")
    require(isinstance(rows, list) and rows, "account audit required")
    previous, by_hash = "GENESIS", {}
    for index, row in enumerate(rows, 1):
        require(isinstance(row, dict), "invalid audit row")
        require(type(row.get("seq")) is int and row["seq"] == index, "audit sequence mismatch")
        require(row.get("prev_hash") == previous, "audit previous hash mismatch")
        require(isinstance(row.get("event"), str) and isinstance(row.get("payload"), str),
                "invalid audit event/payload")
        require(row.get("client_order_id") is None or isinstance(row["client_order_id"], str),
                "invalid audit order identifier")
        material = {"prev": previous, "seq": index, "event": row["event"],
                    "client_order_id": row["client_order_id"], "payload": row["payload"]}
        calculated = digest(json.dumps(material, sort_keys=True, separators=(",", ":"),
                                       allow_nan=False).encode())
        require(row.get("row_hash") == calculated, "audit row hash mismatch")
        payload = document(row["payload"].encode())
        require(payload.get("live_execution") is False, "audit live execution claim")
        if "broker_order_submitted" in payload:
            require(payload["broker_order_submitted"] is False, "audit broker submission claim")
        by_hash[calculated] = {**row, "decoded_payload": payload}
        previous = calculated
    return by_hash


def verify(files: dict[str, bytes], repository: Path, commit: str) -> dict:
    require(set(files) == FILES and all(isinstance(v, bytes) and len(v) <= LIMIT for v in files.values()),
            "two bounded receipt evidence files required")
    raw = files["receipt-paper.json"]
    require(files["receipt-paper.json.sha256"] == (digest(raw) + "  receipt-paper.json\n").encode(),
            "receipt digest sidecar mismatch")
    report = document(raw)
    fields(report, {"schema": "osiris.local-receipt-paper-acceptance.v1", "passed": True,
                    "source_unchanged": True, "scope": SCOPE,
                    "synthetic_market_and_strategy": True, "kernel_credentials_checked": True,
                    "independent_hosts_verified": False, "independent_review_verified": False,
                    "release_gate_accepted": False, "live_execution": False,
                    "broker_order_submitted": False}, "report")
    checks = report.get("checks")
    require(isinstance(checks, list) and all(isinstance(v, str) for v in checks)
            and len(checks) == len(CHECKS) and set(checks) == CHECKS, "incomplete or duplicate checks")
    started, completed = (datetime.fromisoformat(report[name]) for name in ("started_at", "completed_at"))
    require(started.utcoffset() is not None and completed.utcoffset() is not None
            and completed > started, "invalid acceptance timestamps")
    require(isinstance(report.get("binary_sha256"), str) and SHA256.fullmatch(report["binary_sha256"]),
            "binary digest required")
    expected_source = committed_source(repository, commit)
    fields(report.get("source"), expected_source, "source")

    account = report.get("final_account")
    fields(account, {"mode": "paper_only", "live_execution": False, "broker_order_submitted": False,
                     "initialized": True, "kill_switch_active": True, "initial_cash": "1000",
                     "cash": "1018", "realized_pnl": "18", "unrealized_pnl": "0",
                     "fill_count": 2, "order_count": 2, "positions": [], "open_orders": []}, "final_account")
    rows = audit_rows(account)
    decided = {row["client_order_id"]: row for row in rows.values() if row["event"] == "order_decided"}
    fills = {row["client_order_id"]: row for row in rows.values() if row["event"] == "fill_recorded"}
    require(len(decided) == len(fills) == 2
            and sum(r["event"] == "order_decided" for r in rows.values()) == 2
            and sum(r["event"] == "fill_recorded" for r in rows.values()) == 2, "audit order/fill count mismatch")
    traces = report.get("traces")
    require(isinstance(traces, list) and len(traces) == 2, "two receipt/order traces required")
    identifiers, receipts = set(), set()
    for index, trace in enumerate(traces):
        side, price, cash, pnl = ("BUY", "100", "799", "0") if index == 0 else ("SELL", "110", "1018", "18")
        fields(trace, {"schema": "osiris.receipt-paper-execution.v1", "mode": "paper_only",
                       "source_mode": "synthetic", "live_execution": False, "broker_order_submitted": False,
                       "receipt_is_order_approval": False, "new_execution_authorized": False,
                       "independent_model_approval": False, "independent_vendor_rights_verified": False,
                       "replayed": index == 0}, "trace")
        order = trace.get("order")
        fields(order, {"mode": "paper_only", "live_execution": False, "broker_order_submitted": False,
                       "limits_evaluated": True, "side": side, "price": price, "quantity": "2", "fee": "1",
                       "state": "filled", "status": "idempotent_replay" if index == 0 else "filled",
                       "original_status": "filled", "filled_quantity": "2", "leaves_quantity": "0"}, "order")
        identifier = order.get("client_order_id")
        require(isinstance(identifier, str) and identifier not in identifiers
                and trace.get("trace_id") == trace.get("pipeline_run_id") == identifier,
                "trace/order identifier mismatch")
        identifiers.add(identifier)
        decision, fill = decided.get(identifier), fills.get(identifier)
        require(decision is not None and fill is not None, "trace missing audited decision/fill")
        require(order.get("risk_decision_id") == decision["row_hash"], "risk decision mismatch")
        origin = order.get("origin_reference")
        require(isinstance(origin, str) and SHA256.fullmatch(origin), "order origin digest missing")
        fields(decision["decoded_payload"], {name: order[name] for name in
               ("origin_reference", "side", "price", "quantity", "fee", "limits_evaluated")}, "audited decision")
        fields(fill["decoded_payload"], {"risk_decision_id": order["risk_decision_id"], "side": side,
               "price": price, "quantity": "2", "fee": "1", "realized_pnl": pnl}, "audited fill")
        fields(order.get("fill"), {"client_fill_id": identifier, "price": price, "quantity": "2",
               "realized_pnl": pnl, "seq": decision["seq"]}, "order fill")
        fields(trace.get("current_risk"), {"approved_for_paper": True, "limits_evaluated": True,
               "live_execution": False, "order_notional": 200.0 if index == 0 else 220.0}, "current risk")
        fields(trace.get("account"), {"cash": cash, "realized_pnl": pnl, "fill_count": index + 1}, "trace account")
        trace_rows = audit_rows(trace["account"])
        require(all(key in rows and rows[key] == row for key, row in trace_rows.items()), "trace audit not in final account")
        fields(trace.get("reconciliation"), {"passed": True, "breaks": [], "mismatches": [],
               "live_execution": False, "broker_order_submitted": False}, "reconciliation")
        receipt = trace.get("receipt")
        require(isinstance(receipt, dict), "receipt required")
        for name in ("event_id", "envelope_sha256"):
            require(isinstance(receipt.get(name), str) and SHA256.fullmatch(receipt[name]), "receipt digest missing")
        require(receipt["event_id"] not in receipts, "receipt reused across traces")
        receipts.add(receipt["event_id"])
        for name in ("message_id", "recipient_peer_id", "sender_peer_id"):
            require(isinstance(receipt.get(name), str) and receipt[name].strip(), "receipt identity missing")
        for name, minimum in (("rust_pid", 1), ("rust_uid", 0), ("accepted_at", 1)):
            require(type(receipt.get(name)) is int and receipt[name] >= minimum, "receipt process provenance missing")
    require(identifiers == set(decided) == set(fills), "untraced order or fill")
    return {"checkout_commit": commit, "checkout_tree": expected_source["git_tree"],
            "source_files_verified": len(expected_source["files_sha256"]),
            "worktree_sha256": expected_source["worktree_sha256"], "binary_sha256": report["binary_sha256"],
            "checks": sorted(checks), "traces_verified": len(traces), "audit_rows_verified": len(rows),
            "files_sha256": {name: digest(value) for name, value in sorted(files.items())}, "scope": SCOPE}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--archive", type=Path)
    source.add_argument("--artifact-dir", type=Path)
    parser.add_argument("--archive-sha256")
    parser.add_argument("--repository", type=Path, default=ROOT)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    if args.report and args.report.exists():
        parser.error("report exists; refusing overwrite")
    if args.artifact_dir and args.archive_sha256:
        parser.error("--archive-sha256 requires --archive")
    result = {"schema": "osiris.receipt-paper-artifact-verification.v1", "passed": False,
              "release_gate_accepted": False, "live_trading_authorized": False,
              "independent_hosts_verified": False, "independent_review_verified": False,
              "ci_identity_authenticated": False, "binary_reproduced": False,
              "receipt_signature_reverified": False, "receipt_origin_recomputed": False,
              "run_truth_independently_verified": False}
    try:
        files, archive_digest = read_artifact(args.archive or args.artifact_dir, archive=bool(args.archive),
            expected_digest=args.archive_sha256, expected_files=FILES)
        result.update(verify(files, args.repository, args.commit), passed=True, archive_sha256=archive_digest)
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, RecursionError,
            subprocess.SubprocessError, zipfile.BadZipFile) as error:
        result["error"] = str(error)
    raw = json.dumps(result, indent=2, sort_keys=True) + "\n"
    print(raw, end="")
    if args.report:
        with args.report.open("x", encoding="utf-8") as output:
            output.write(raw)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
