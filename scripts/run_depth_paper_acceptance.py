#!/usr/bin/env python3
"""Retain genuine keyless L2 -> conservative paper fills -> recovery evidence.

Diagnostic orders exercise execution independently of any strategy signal.
The frozen fee is an assumption, not an authenticated venue/account fee.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from urllib.request import Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services/quant-api/src"))

from osiris_api import paper_execution as ledger  # noqa: E402
from osiris_api.control_plane.database import Database, canonical, digest  # noqa: E402
from osiris_api.control_plane.depth import DepthService  # noqa: E402
from osiris_api.control_plane.vendors import NoRedirect  # noqa: E402
from osiris_api.paper_depth_execution import execute_depth_fill  # noqa: E402

SYMBOLS = ("BTC-USD", "ETH-USD")
REFERENCE = "public-keyless-internal-paper-diagnostic-no-redistribution-approval"
SOURCE_FILES = (
    "scripts/run_depth_paper_acceptance.py",
    *("services/quant-api/src/osiris_api/" + name for name in (
        "paper_execution.py", "paper_depth_execution.py", "decimal_safety.py", "risk.py",
        "control_plane/depth.py", "control_plane/database.py", "control_plane/broker.py",
        "control_plane/vendors.py",
    )),
)


def manifest():
    return {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in SOURCE_FILES}


def write(path, value):
    with path.open("xb") as file:
        os.chmod(path, 0o600)
        file.write(canonical(value) + b"\n")


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


class CaptureTransport:
    def __init__(self, directory):
        self.directory, self.captures = directory, []

    def request(self, method, url):
        require(method == "GET" and url in {
            "https://api.exchange.coinbase.com/products/" + symbol + "/book?level=2"
            for symbol in SYMBOLS
        }, "fixed_public_depth_endpoint_required")
        requested = datetime.now(UTC).isoformat()
        with build_opener(NoRedirect()).open(Request(url, headers={
            "User-Agent": "Osiris-Eye/0.2 keyless-paper-depth", "Accept": "application/json",
        }), timeout=10) as response:
            raw = response.read(2_097_153)
        received = datetime.now(UTC).isoformat()
        require(len(raw) <= 2_097_152, "depth_response_too_large")
        filename = f"response-{len(self.captures) + 1}.json"
        with (self.directory / filename).open("xb") as file:
            os.chmod(self.directory / filename, 0o600)
            file.write(raw)
        metadata = {"method": method, "url": url, "requested_at": requested,
                    "received_at": received, "body_file": filename,
                    "body_sha256": hashlib.sha256(raw).hexdigest(), "body_bytes": len(raw)}
        self.captures.append(metadata)
        write(self.directory / (filename + ".capture.json"), metadata)
        return json.loads(raw)


def probe(directory):
    require(not directory.resolve().is_relative_to(ROOT), "evidence_must_be_outside_source_checkout")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    require(not subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT),
            "commit_depth_probe_source_before_execution")
    require(os.getenv("OSIRIS_PUBLIC_COINBASE_ENABLED", "true").lower() not in {"0", "false", "no", "off"},
            "coinbase_public_source_disabled")
    code = manifest()
    plan = {"schema": "osiris.keyless-depth-plan.v1", "candidate_commit": head,
            "created_at": datetime.now(UTC).isoformat(), "symbols": list(SYMBOLS),
            "quantity": "0.0001", "assumed_fee_bps": 10, "participation_bps": 1000,
            "min_latency_ms": 250, "max_age_ms": 5000, "max_levels_per_order": 20,
            "purpose": "execution_diagnostic_not_strategy_validation",
            "source_files": code, "live_trading_authorized": False,
            "redistribution_authorized": False, "release_gate_accepted": False}
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    write(directory / "plan.json", plan)
    report = {"schema": "osiris.keyless-depth-exercise.v1", "passed": False,
              "candidate_commit": head, "plan_sha256": digest(plan), "exercises": [],
              "live_execution": False, "broker_order_submitted": False,
              "release_gate_accepted": False, "execution_calibrated": False}
    transport = CaptureTransport(directory)
    previous = os.environ.get("OSIRIS_PAPER_EXECUTION_DB")
    os.environ["OSIRIS_PAPER_EXECUTION_DB"] = str(directory / "paper.sqlite3")
    try:
        ledger.initialize_paper_account("10000")
        service = DepthService(Database(str(directory / "control.sqlite3")),
                               entitlement_reference=REFERENCE, transport=transport)
        for symbol in SYMBOLS:
            book = service.capture(symbol)
            write(directory / (symbol + "-normalized-book.json"), book)
            time.sleep(0.3)
            for side, field in (("BUY", "asks"), ("SELL", "bids")):
                key = symbol + "-" + side
                price = book[field][min(19, len(book[field]) - 1)][0]
                fee = ledger._canonical(Decimal(plan["quantity"]) * Decimal(price) * Decimal("0.001"))
                stamp = datetime.fromtimestamp(book["captured_ms"] / 1000, UTC).isoformat()
                order = ledger.submit_paper_order(
                    client_order_id=key, symbol=symbol, side=side, quantity=plan["quantity"],
                    price=price, fee=fee, observed_at=stamp, now=datetime.now(UTC).isoformat(),
                    risk_service_available=True, kill_switch_active=False, working=True,
                )
                require(order["state"] == "accepted", "diagnostic_order_refused")
                exercise = {"symbol": symbol, "side": side, "order": order, "fills": []}
                report["exercises"].append(exercise)
                for level in range(plan["max_levels_per_order"]):
                    plan_id = key + "-" + str(level)
                    args = {"client_order_id": key, "client_plan_id": plan_id,
                            "risk_service_available": True}
                    if not report.get("crash_rollback_verified"):
                        before = ledger.snapshot()
                        try:
                            execute_depth_fill(service, **args, simulate_crash=True)
                        except RuntimeError as error:
                            require(str(error) == "simulated_crash_before_commit", "unexpected_crash_error")
                        else:
                            raise ValueError("crash_hook_did_not_refuse")
                        require(before == ledger.snapshot(), "crash_changed_financial_state")
                        report["crash_rollback_verified"] = True
                    result = execute_depth_fill(service, **args)
                    exercise["fills"].append(result)
                    require(result["depth"]["outcome"] == "filled", "insufficient_conservative_displayed_liquidity")
                    if not report.get("new_process_replay_verified"):
                        child_code = '''
import json,sys
from osiris_api.control_plane.database import Database
from osiris_api.control_plane.depth import DepthService
from osiris_api.paper_depth_execution import execute_depth_fill
service=DepthService(Database(sys.argv[1]),entitlement_reference=sys.argv[2])
print(json.dumps(execute_depth_fill(service,client_order_id=sys.argv[3],client_plan_id=sys.argv[4])))
'''
                        child = subprocess.run([sys.executable, "-c", child_code, service.db.path,
                            REFERENCE, key, plan_id], capture_output=True, text=True, timeout=30, check=True)
                        replay = json.loads(child.stdout)
                        require(replay["fill"] == result["fill"] and replay["status"] == "idempotent_replay"
                                and replay["execution_authorized"] is False, "restart_duplicated_or_reapproved_fill")
                        report["new_process_replay_verified"] = True
                    if result["state"] == "filled":
                        break
                require(ledger.get_paper_order(key)["state"] == "filled", "diagnostic_order_not_fully_filled")
            require(not ledger.snapshot()["positions"], "round_trip_left_inventory")
        recovered = ledger.recover()
        state = recovered["snapshot"]
        require(recovered["passed"] is True and not state["positions"] and not state["open_orders"],
                "round_trip_does_not_reconcile")
        require(Decimal(state["cash"]) == Decimal(state["initial_cash"]) + Decimal(state["realized_pnl"]),
                "flat_cash_and_realized_pnl_disagree")
        require(code == manifest() and head == subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
            and not subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT),
            "depth_probe_source_changed")
        report.update(passed=True, snapshot=state, reconciliation=ledger.reconciliation_report())
    except Exception as error:
        report["error"] = {"type": type(error).__name__, "message": str(error)}
    finally:
        if previous is None:
            os.environ.pop("OSIRIS_PAPER_EXECUTION_DB", None)
        else:
            os.environ["OSIRIS_PAPER_EXECUTION_DB"] = previous
    report["captures"] = transport.captures
    report["report_sha256"] = digest(report)
    write(directory / "result.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = probe(args.output_dir.resolve())
        print(json.dumps({key: result.get(key) for key in (
            "passed", "candidate_commit", "report_sha256", "error", "release_gate_accepted",
        )}))
        return 0 if result["passed"] else 1
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(json.dumps({"passed": False, "error": str(error), "release_gate_accepted": False}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
