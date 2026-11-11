#!/usr/bin/env python3
"""Run connected paper acceptance in an isolated account; write actual evidence.

The default replaces only the public HTTP response bytes with synthetic candles.
--live-data fetches real public Coinbase data and never sends an exchange order.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from typing import Iterator
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services/quant-api/src"))

from osiris_api.paper_execution import (  # noqa: E402
    initialize_paper_account, recover, set_kill_switch, snapshot,
)
from osiris_api.paper_pipeline import run_paper_pipeline  # noqa: E402


def require(condition: bool, invariant: str) -> None:
    if not condition:
        raise RuntimeError(invariant)


@contextmanager
def isolated_account(path: Path) -> Iterator[None]:
    name = "OSIRIS_PAPER_EXECUTION_DB"
    previous = os.environ.get(name)
    os.environ[name] = str(path)
    try:
        initialize_paper_account("100000")
        yield
    finally:
        if previous is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = previous


def fixture_response(prices: list[int]):
    """Produce provider-shaped bytes; real adapter and every later stage still run."""
    def get(url: str, **_kwargs: object) -> bytes:
        query = parse_qs(urlparse(url).query)
        end = datetime.fromisoformat(query["end"][0])
        start = end - timedelta(minutes=len(prices))
        rows = [
            [int((start + timedelta(minutes=index)).timestamp()), price - 1,
             price + 1, price, price, 10]
            for index, price in enumerate(prices)
        ]
        return json.dumps(list(reversed(rows))).encode()
    return get


def check_run(result: dict) -> None:
    require(result["live_execution"] is False, "live_execution_must_remain_disabled")
    require(result["broker_order_submitted"] is False, "broker_order_must_never_be_submitted")
    require(result["reconciliation"]["passed"] is True, "ledger_must_reconcile")
    require(len(result["market"]["bars"]) == 4, "all_strategy_candles_must_be_retained")
    require(result["workflow"]["backtest"]["signal_alignment"] ==
            "signal[t-1]_applied_to_return[t]", "backtest_must_be_lagged")


def run_offline(report: dict) -> None:
    # Replay deliberately targets the same minute. Avoid crossing its last two
    # seconds while the real adapter records HTTP availability on the wall clock.
    wall = datetime.now(timezone.utc)
    if wall.second >= 58:
        time.sleep(60 - wall.second - wall.microsecond / 1_000_000 + 0.01)
    with patch("osiris_api.public_market_data._get", side_effect=fixture_response([100, 110, 120, 130])):
        bought = run_paper_pipeline(quantity="2", fee="1")
        check_run(bought)
        require(bought["order"]["status"] == "filled", "buy_must_fill")
        require(Decimal(bought["account"]["cash"]) == Decimal("99739"), "buy_cash_includes_fee")
        require(bought["account"]["positions"] == [
            {"symbol": "BTC-USD", "quantity": "2", "average_cost": "130.5"}
        ], "buy_position_cost_includes_fee")
        require(Decimal(bought["account"]["unrealized_pnl"]) == Decimal("-1"), "buy_mark_includes_fee")
        # Same closed minute, with enough room for HTTP retrieval availability.
        candle_end = datetime.fromisoformat(bought["market"]["observed_at"])
        replay = run_paper_pipeline(quantity="2", fee="1", now=candle_end + timedelta(seconds=59, microseconds=999999))
        check_run(replay)
        require(replay["replayed"] is True and replay["account"]["fill_count"] == 1,
                "replay_must_not_duplicate_fill")
    report["runs"].extend([bought, replay])
    report["checks"].extend(["market_adapter_to_strategy_to_risk_to_fill", "exact_buy_cash_and_fee_cost_basis",
                              "marked_unrealized_pnl", "same_candle_idempotency"])

    # A second closed minute uses a library-only clock and synthetic data.
    sell_clock = candle_end + timedelta(minutes=1, seconds=59)
    with patch("osiris_api.public_market_data._get", side_effect=fixture_response([140, 135, 130, 125])):
        sold = run_paper_pipeline(quantity="2", fee="1", now=sell_clock)
    check_run(sold)
    require(sold["order"]["status"] == "filled", "sell_must_fill")
    require(Decimal(sold["account"]["cash"]) == Decimal("99988"), "round_trip_cash_exact")
    require(Decimal(sold["account"]["realized_pnl"]) == Decimal("-12"), "round_trip_pnl_includes_both_fees")
    require(sold["account"]["positions"] == [], "full_close_removes_position")
    require(Decimal(sold["account"]["unrealized_pnl"]) == 0, "closed_position_has_no_unrealized_pnl")
    report["runs"].append(sold)
    report["checks"].append("exact_round_trip_cash_positions_and_pnl")

    set_kill_switch(True)
    with patch("osiris_api.public_market_data._get", side_effect=fixture_response([100, 110, 120, 130])):
        killed = run_paper_pipeline(quantity="2", strategy_id="kill-check", now=sell_clock)
    check_run(killed)
    require(killed["workflow"]["risk"]["reason"] == "kill_switch_active", "kill_switch_must_block_strategy")
    require(killed["order"]["status"] == "not_submitted", "kill_switch_must_prevent_order")
    require(killed["account"]["fill_count"] == 2, "kill_switch_must_prevent_fill")
    set_kill_switch(False)
    report["runs"].append(killed)
    report["checks"].append("persisted_kill_switch_blocks_new_orders")

    with patch("osiris_api.public_market_data._get", side_effect=fixture_response([100, 110, 120, 130])):
        oversized = run_paper_pipeline(quantity="1000", strategy_id="order-limit-check", now=sell_clock)
    check_run(oversized)
    require(oversized["workflow"]["risk"]["reason"] == "max_order_notional_exceeded", "order_limit_must_block")
    require(oversized["account"]["fill_count"] == 2, "order_limit_must_prevent_fill")
    report["runs"].append(oversized)
    report["checks"].append("independent_order_notional_limit")

    before = snapshot()
    with patch("osiris_api.public_market_data._get", return_value=b"not valid json"):
        try:
            run_paper_pipeline(strategy_id="malformed-check", now=sell_clock)
        except (ValueError, RuntimeError) as exc:
            report["refusals"].append({"scenario": "malformed_feed", "error": str(exc)})
        else:
            raise RuntimeError("malformed_feed_must_fail_closed")
    require(snapshot()["fill_count"] == before["fill_count"], "invalid_feed_must_not_fill")
    report["checks"].append("invalid_market_data_fails_closed")
    check_restart(report)


def check_restart(report: dict) -> None:
    # A new Python process proves that recovery uses SQLite, not module memory.
    code = (
        "import json; from osiris_api.paper_execution import recover; "
        "r=recover(); s=r['snapshot']; "
        "print(json.dumps({'passed':r['passed'],'cash':s['cash'],"
        "'realized_pnl':s['realized_pnl'],'positions':s['positions'],'fill_count':s['fill_count']}))"
    )
    child = subprocess.run([sys.executable, "-c", code], cwd=ROOT, check=True,
                           text=True, capture_output=True, timeout=30)
    retained = json.loads(child.stdout)
    current = snapshot()
    require(retained["passed"] is True, "restarted_process_must_reconcile")
    require(all(retained[field] == current[field] for field in
                ("cash", "realized_pnl", "positions", "fill_count")), "restart_state_must_match")
    require(recover()["passed"] is True, "in_process_recovery_must_reconcile")
    report["restart"] = retained
    report["checks"].append("separate_process_restart_recovers_exact_ledger_state")


def run_live(report: dict, symbol: str, quantity: str) -> None:
    result = run_paper_pipeline(symbol=symbol, quantity=quantity)
    check_run(result)
    report["runs"].append(result)
    report["checks"].extend(["real_coinbase_public_http_fetch", "closed_contiguous_market_history",
                              "market_strategy_risk_order_accounting_pipeline", "ledger_reconciliation"])
    report["observed_order_status"] = result["order"]["status"]
    report["observed_fill_count"] = result["account"]["fill_count"]
    # SELL with no inventory or HOLD is a valid observed outcome. Never manufacture
    # a positive strategy decision just to make the connected-data check pass.
    check_restart(report)


def provenance() -> dict:
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
                            capture_output=True, check=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, text=True,
                               capture_output=True, check=True).stdout.strip())
    source_paths = [
        "scripts/run_market_paper_acceptance.py",
        "services/quant-api/src/osiris_api/paper_pipeline.py",
        "services/quant-api/src/osiris_api/paper_workflow.py",
        "services/quant-api/src/osiris_api/paper_execution.py",
        "services/quant-api/src/osiris_api/paper_pipeline_status.py",
        "services/quant-api/src/osiris_api/public_market_data.py",
        "services/quant-api/src/osiris_api/market_gateway.py",
        "services/quant-api/src/osiris_api/risk.py",
    ]
    result = {"base_commit_sha": commit, "working_tree_dirty": dirty,
              "source_sha256": {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
                                for path in source_paths}}
    server, repository, run_id = (os.getenv(name) for name in
                                  ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"))
    if all((server, repository, run_id)):
        result["ci_run_url"] = f"{server}/{repository}/actions/runs/{run_id}"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--live-data", action="store_true")
    parser.add_argument("--symbol", default="BTC-USD")
    parser.add_argument("--quantity", default="0.001")
    args = parser.parse_args()
    report = {
        "schema": "osiris.market_paper_acceptance.v1", "passed": False,
        "mode": "live_public_data" if args.live_data else "offline_fixture",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "live_execution_enabled": False, "broker_order_submitted": False,
        "scope": "single_runner_isolated_temporary_paper_sqlite",
        "release_gate_accepted": False, "runs": [], "checks": [], "refusals": [],
        "limitations": ["Simulated fills at closed candle close; no slippage or venue matching.",
                        "Momentum is a plumbing diagnostic, not a validated profitable strategy.",
                        "This does not establish Rust/P2P independent-host acceptance or live broker readiness."],
    }
    try:
        report.update(provenance())
        require(not os.getenv("OSIRIS_ENABLE_LIVE_EXECUTION"), "live_execution_environment_refused")
        with tempfile.TemporaryDirectory(prefix="osiris-market-paper-acceptance-") as folder:
            with isolated_account(Path(folder) / "paper.sqlite3"):
                if args.live_data:
                    run_live(report, args.symbol, args.quantity)
                else:
                    run_offline(report)
                report["final_account"] = snapshot()
        report["passed"] = True
    except Exception as exc:
        report["error"] = {"type": type(exc).__name__, "message": str(exc)}
    report["completed_at"] = datetime.now(timezone.utc).isoformat()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    digest = hashlib.sha256(args.output.read_bytes()).hexdigest()
    args.output.with_suffix(args.output.suffix + ".sha256").write_text(
        f"{digest}  {args.output.name}\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "mode": report["mode"],
                      "checks": report["checks"], "output": str(args.output),
                      "sha256": digest, "error": report.get("error")}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
