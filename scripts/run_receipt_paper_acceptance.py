#!/usr/bin/env python3
"""Real local Rust E2EE receipt -> Python durable risk/paper/accounting trace.

All market inputs and research proposals are synthetic. Runs only disposable
accounts, never submits broker orders and never accepts an external gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services/quant-api/src"))
sys.path.insert(0, str(ROOT / "scripts"))

from osiris_api import paper_execution as execution
from osiris_api.admin_centre import paper_status
from osiris_api.data_store import ResearchStore
from osiris_api.paper_receipt_execution import execute_receipted_observation
from osiris_api.receipt_gated_retry import AdmissionDecision, ReceiptRetryScheduler
from osiris_api.recipient_receipt_ledger import RecipientReceiptLedger
from osiris_api.verified_receipt_consumer import VerifiedReceiptConsumer
from osiris_api.verified_receipt_unix import (
    accept_one_verified_receipt,
    bind_verified_receipt_listener,
)
from test_p2p_delivery_receipts import Nodes, fields
from verify_all import source_snapshot


def require(value, reason):
    if not value:
        raise RuntimeError(reason)


def child(path: Path, *, crash: bool):
    data = json.loads(path.read_text())
    ledger = RecipientReceiptLedger(data["ledger"])
    consumer = VerifiedReceiptConsumer(ledger, sender_peer_id=data["sender"])
    scheduler = ReceiptRetryScheduler(data["scheduler"], ledger, enabled=True)
    args = data["arguments"]
    args["policy"] = AdmissionDecision(set(data["trusted"]), set())
    args["allowed_providers"] = set(args["allowed_providers"])
    args["now"] = datetime.now(UTC)
    store = ResearchStore(data["research"])
    import osiris_api.paper_receipt_execution as integration

    original = integration._decision

    def persist(run_id, fingerprint, result=None):
        if crash and result is not None:
            os._exit(73)  # Actual process loss after the canonical fill committed.
        return original(run_id, fingerprint, result)

    with patch.object(integration, "_decision", persist):
        result = execute_receipted_observation(scheduler, consumer, store, **args)
    print(json.dumps(result, sort_keys=True))


def find_binary():
    configured = os.getenv("OSIRIS_P2P_TEST_BINARY")
    choices = (
        [Path(configured)]
        if configured
        else [
            ROOT / "services/p2p-node/target/debug/osiris-p2p-node",
            ROOT / "services/p2p-node/target/release/osiris-p2p-node",
        ]
    )
    for path in choices:
        if path.is_file():
            return path.resolve()
    raise RuntimeError("built_p2p_binary_required")


def run() -> dict:
    if os.getenv("OSIRIS_ENABLE_LIVE_EXECUTION"):
        raise RuntimeError("live_execution_environment_refused")
    before = source_snapshot()
    binary = find_binary()
    checks, traces = [], []
    started = datetime.now(UTC).isoformat()
    with tempfile.TemporaryDirectory(prefix="osiris-receipt-paper-") as folder:
        directory = Path(folder)
        ledger = RecipientReceiptLedger(str(directory / "receipts.db"))
        scheduler = ReceiptRetryScheduler(
            str(directory / "retry.db"), ledger, enabled=True
        )
        store = ResearchStore(str(directory / "research.db"))
        socket_path = directory / "receipt.sock"
        listener = bind_verified_receipt_listener(str(socket_path))
        listener.settimeout(8)
        nodes = Nodes(binary, directory)
        environment = {
            "OSIRIS_PAPER_EXECUTION_DB": str(directory / "paper.db"),
            "OSIRIS_P2P_INGEST_ENABLED": "1",
            "OSIRIS_RECEIPT_PAPER_ENABLED": "1",
        }
        with patch.dict(os.environ, environment):
            execution.initialize_paper_account("1000")
            try:
                recipients = directory / "recipients.txt"
                recipients.write_text("")
                sender = nodes.start(
                    "sender",
                    recipients_file=recipients,
                    receipt_bridge={
                        "socket": socket_path,
                        "spool": directory / "rust-spool.db",
                        "listener_pid": os.getpid(),
                    },
                )
                identity = fields(
                    nodes.wait_for(
                        lambda name, line: (
                            name == "sender" and line.startswith("peer_id=")
                        ),
                        "sender identity",
                        timeout=20,
                    )
                )["peer_id"]
                address = fields(
                    nodes.wait_for(
                        lambda name, line: (
                            name == "sender" and line.startswith("listener_ready ")
                        ),
                        "sender listener",
                        timeout=20,
                    )
                )["address"]
                nodes.start("recipient", bootstrap=address)
                recipient = fields(
                    nodes.wait_for(
                        lambda name, line: (
                            name == "recipient" and line.startswith("peer_id=")
                        ),
                        "recipient identity",
                        timeout=20,
                    )
                )["peer_id"]
                nodes.wait_for(
                    lambda name, line: (
                        name == "sender"
                        and line.startswith(f"key_binding_accepted peer={recipient} ")
                    ),
                    "recipient key binding",
                    timeout=30,
                )
                nodes.capture_for(2)
                recipients.write_text(recipient + "\n")
                consumer = VerifiedReceiptConsumer(ledger, sender_peer_id=identity)
                policy = AdmissionDecision({recipient}, set())
                provider = "synthetic-receipt-paper"
                seen_messages = set()

                def publish(line, peer):
                    sender.stdin.write(line + ("" if line.endswith("\n") else "\n"))
                    sender.stdin.flush()
                    event = fields(
                        nodes.wait_for(
                            lambda name, output: (
                                name == "sender"
                                and output.startswith(
                                    f"encrypted_observation_published recipient={peer} "
                                )
                                and fields(output).get("message_id")
                                not in seen_messages
                            ),
                            "tracked encrypted publication",
                            timeout=20,
                        )
                    )
                    require(
                        event.get("receipts_tracked") == "true",
                        "receipt_tracking_required",
                    )
                    seen_messages.add(event["message_id"])
                    return {
                        "status": "tracked",
                        "message_id": event["message_id"],
                        "envelope_sha256": event["envelope_sha256"],
                    }

                def receipted(price, sequence, signal):
                    now = datetime.now(UTC)
                    raw = json.dumps(
                        {
                            "last": price,
                            "sequence": sequence,
                            "source_mode": "synthetic",
                        }
                    )
                    observation = {
                        "schema": "osiris.observation.v1",
                        "instrument": "PAPER-TEST",
                        "provider": provider,
                        "observed_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "payload": raw,
                        "payload_sha256": hashlib.sha256(raw.encode()).hexdigest(),
                    }
                    key = ":".join(
                        (provider, "PAPER-TEST", observation["payload_sha256"])
                    )
                    scheduler.schedule(observation, recipient, now=int(now.timestamp()))
                    outcomes = scheduler.advance(
                        publish=publish,
                        allowed_providers={provider},
                        policy=policy,
                        now=int(now.timestamp()),
                    )
                    require(
                        len(outcomes) == 1 and outcomes[0]["state"] == "published",
                        "publication_failed",
                    )
                    args = {
                        "observation_key": key,
                        "recipient": recipient,
                        "allowed_providers": {provider},
                        "policy": policy,
                        "strategy_id": "synthetic-trace-v1",
                        "signals": [0, signal],
                        "asset_returns": [0, 0.01],
                        "quantity": "2",
                        "fee": "1",
                        "risk_service_available": True,
                        "source_mode": "synthetic",
                        "now": datetime.now(UTC),
                        "feed_policy": {
                            provider: {
                                "authorized_source": "synthetic-local-fixture",
                                "entitlement_reference": "no-vendor-rights-claimed",
                                "redistribution_rights": "permitted",
                                "availability_timestamps_required": True,
                                "instruments": ["PAPER-TEST"],
                                "quality_review": {
                                    "status": "passed",
                                    "reviewer": "synthetic-fixture-only",
                                    "reviewed_at": now.isoformat(),
                                },
                            }
                        },
                    }
                    prior_count = execution.snapshot()["fill_count"]
                    try:
                        execute_receipted_observation(
                            scheduler, consumer, store, **args
                        )
                    except ValueError as exc:
                        require(
                            str(exc) == "verified_receipt_required",
                            "unexpected_prereceipt_refusal",
                        )
                    else:
                        raise RuntimeError("paper_execution_preceded_verified_receipt")
                    require(
                        execution.snapshot()["fill_count"] == prior_count,
                        "prereceipt_fill_created",
                    )
                    row = scheduler.snapshot(key, recipient)
                    deadline = time.monotonic() + 30
                    while (
                        ledger.status(
                            row["message_id"], recipient, row["envelope_sha256"]
                        )
                        != "acknowledged"
                    ):
                        require(time.monotonic() < deadline, "verified_receipt_timeout")
                        accept_one_verified_receipt(
                            listener,
                            consumer,
                            expected_rust_pid=sender.pid,
                            expected_rust_uid=os.getuid(),
                            now=int(time.time()),
                        )
                    scheduler.advance(
                        publish=publish,
                        allowed_providers={provider},
                        policy=policy,
                        now=int(time.time()),
                    )
                    proof = consumer.verified_event(
                        row["message_id"], recipient, row["envelope_sha256"]
                    )
                    require(
                        proof is not None
                        and proof["rust_pid"] == sender.pid
                        and proof["sender_peer_id"] == identity,
                        "receipt_process_provenance_mismatch",
                    )
                    args["now"] = datetime.now(UTC)
                    return args

                buy = receipted("100", 1, 1)
                checks.extend(
                    [
                        "actual_rust_e2ee_and_recipient_receipt",
                        "kernel_pinned_event_persisted",
                        "no_paper_execution_before_acknowledgement",
                    ]
                )
                child_input = directory / "child.json"
                data = {
                    "ledger": ledger.path,
                    "scheduler": scheduler.path,
                    "research": store.database_path,
                    "sender": identity,
                    "trusted": [recipient],
                    "arguments": {
                        **buy,
                        "policy": None,
                        "allowed_providers": [provider],
                        "now": None,
                    },
                }
                child_input.write_text(json.dumps(data))
                crashed = subprocess.run(
                    [
                        sys.executable,
                        str(__file__),
                        "--child",
                        str(child_input),
                        "--crash-after-order",
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                require(
                    crashed.returncode == 73,
                    "child_did_not_crash_after_committed_order: "
                    + crashed.stderr[-1000:],
                )
                require(
                    execution.snapshot()["fill_count"] == 1,
                    "crash_did_not_leave_one_durable_fill",
                )
                resumed = subprocess.run(
                    [sys.executable, str(__file__), "--child", str(child_input)],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                require(
                    resumed.returncode == 0,
                    "fresh_process_recovery_failed: " + resumed.stderr[-1000:],
                )
                recovered = json.loads(resumed.stdout)
                require(
                    recovered["replayed"]
                    and recovered["account"]["cash"] == "799"
                    and recovered["account"]["unrealized_pnl"] == "-1",
                    "recovered_account_mismatch",
                )
                require(
                    execution.snapshot()["fill_count"] == 1, "recovery_duplicated_fill"
                )
                traces.append(recovered)
                checks.extend(
                    [
                        "actual_child_loss_after_fill_commit",
                        "new_process_idempotent_recovery",
                        "receipt_to_risk_decision_to_fill_trace",
                    ]
                )
                sell = receipted("110", 2, -1)
                sold = execute_receipted_observation(scheduler, consumer, store, **sell)
                require(
                    sold["order"]["status"] == "filled"
                    and sold["account"]["cash"] == "1018"
                    and sold["account"]["realized_pnl"] == "18",
                    "round_trip_accounting_mismatch",
                )
                require(
                    sold["reconciliation"]["passed"], "round_trip_does_not_reconcile"
                )
                traces.append(sold)
                replay = execute_receipted_observation(
                    scheduler, consumer, store, **sell
                )
                require(
                    replay["replayed"] and execution.snapshot()["fill_count"] == 2,
                    "replay_duplicated_fill",
                )
                try:
                    execute_receipted_observation(
                        scheduler, consumer, store, **{**sell, "quantity": "1"}
                    )
                except ValueError as exc:
                    require(
                        str(exc) == "pipeline_decision_conflict",
                        "unexpected_conflict_reason",
                    )
                else:
                    raise RuntimeError("changed_economics_reused_observation")
                execution.set_kill_switch(True)
                blocked = execute_receipted_observation(
                    scheduler,
                    consumer,
                    store,
                    **{**sell, "strategy_id": "synthetic-blocked"},
                )
                require(
                    blocked["order"]["status"] == "not_submitted"
                    and blocked["current_risk"]["reason"] == "kill_switch_active",
                    "kill_switch_bypassed",
                )
                try:
                    execute_receipted_observation(
                        scheduler,
                        consumer,
                        store,
                        **{
                            **sell,
                            "policy": AdmissionDecision({recipient}, {recipient}),
                        },
                    )
                except ValueError as exc:
                    require(
                        str(exc) == "receipt_recipient_not_authorized",
                        "unexpected_revocation_reason",
                    )
                else:
                    raise RuntimeError("revocation_bypassed")
                canonical = execution.snapshot()
                require(
                    canonical["fill_count"] == 2 and execution.reconcile()["passed"],
                    "final_reconciliation_failed",
                )
                admin = paper_status()
                require(
                    admin.get("cash") == "1018" or admin.get("cash") == 1018,
                    "canonical_admin_cash_mismatch",
                )
                checks.extend(
                    [
                        "exact_fee_inclusive_round_trip_pnl",
                        "canonical_admin_account_matches",
                        "replay_and_changed_economics_guard",
                        "current_kill_switch_and_revocation_guard",
                    ]
                )
            finally:
                listener.close()
                nodes.close()
    after = source_snapshot()
    require(before == after, "source_changed_during_acceptance")
    return {
        "schema": "osiris.local-receipt-paper-acceptance.v1",
        "passed": True,
        "started_at": started,
        "completed_at": datetime.now(UTC).isoformat(),
        "source": after,
        "source_unchanged": True,
        "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "checks": checks,
        "traces": traces,
        "final_account": canonical,
        "scope": "two-local-rust-nodes-and-independent-local-python-processes",
        "synthetic_market_and_strategy": True,
        "kernel_credentials_checked": True,
        "independent_hosts_verified": False,
        "independent_review_verified": False,
        "release_gate_accepted": False,
        "live_execution": False,
        "broker_order_submitted": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--child", type=Path, help=argparse.SUPPRESS)
    parser.add_argument(
        "--crash-after-order", action="store_true", help=argparse.SUPPRESS
    )
    args = parser.parse_args()
    if args.child:
        child(args.child, crash=args.crash_after_order)
        return 0
    if not args.output or args.crash_after_order:
        parser.error("--output is required")
    if args.output.exists():
        parser.error("output exists; refusing overwrite")
    try:
        report = run()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        report = {
            "schema": "osiris.local-receipt-paper-acceptance.v1",
            "passed": False,
            "error": str(exc),
            "live_execution": False,
            "release_gate_accepted": False,
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode()
    with args.output.open("xb") as file:
        file.write(raw)
    args.output.with_suffix(args.output.suffix + ".sha256").write_text(
        hashlib.sha256(raw).hexdigest() + "  " + args.output.name + "\n"
    )
    print(
        json.dumps(
            {
                "passed": report["passed"],
                "checks": report.get("checks", []),
                "error": report.get("error"),
                "release_gate_accepted": False,
            }
        )
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
