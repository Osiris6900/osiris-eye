"""Fail-closed offline integration acceptance gate for OSIRIS paper release.

Consumes independently generated evidence statuses. This is a coordination
check, not proof that tests actually ran or that an external reviewer signed.
No broker connectivity or live trading is enabled.
"""
import argparse
import json
from pathlib import Path

REQUIRED = (
    "p2p_cross_host_passed",
    "crypto_negative_tests_passed",
    "authorized_market_data_passed",
    "paper_reconciliation_passed",
    "risk_kill_switch_passed",
    "operator_rbac_passed",
    "backup_restore_passed",
    "ci_passed",
    "network_partition_recovery_passed",
    "restart_replay_recovery_passed",
    "paper_order_trace_passed",
)

def validate(record):
    if not isinstance(record, dict):
        return ["integration evidence must be an object"]
    errors = [f"{key} must be true" for key in REQUIRED if record.get(key) is not True]
    if record.get("live_trading_enabled") is not False:
        errors.append("live_trading_enabled must be false")
    if record.get("test_mode") != "paper":
        errors.append("test_mode must be paper")
    if record.get("external_order_count") != 0 or type(record.get("external_order_count")) is not int:
        errors.append("external_order_count must be integer zero")
    return errors

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("evidence", type=Path)
    args = parser.parse_args()
    errors = validate(json.loads(args.evidence.read_text(encoding="utf-8")))
    print(json.dumps({"passed": not errors, "errors": errors}))
    raise SystemExit(bool(errors))
