"""Offline fail-closed acceptance of paper OMS/risk failure-test evidence.

This validates independently generated simulator results; it does not run the
production OMS and cannot replace a real E2E failure-injection test.
"""
import argparse
import json
import re
from pathlib import Path

SCENARIOS = ("kill_switch", "restart_recovery", "stale_data_rejection")
SHA = re.compile(r"^[a-f0-9]{40}$")


def validate(report):
    errors = []
    if not isinstance(report, dict):
        return ["report must be an object"]
    if not SHA.fullmatch(str(report.get("commit_sha", ""))):
        errors.append("exact commit SHA required")
    if report.get("live_execution_enabled") is not False:
        errors.append("live execution must be disabled")
    scenarios = report.get("scenarios")
    if not isinstance(scenarios, dict):
        return errors + ["scenarios object required"]
    if set(scenarios) != set(SCENARIOS):
        errors.append("all three scenarios required, no extras")
    for name in SCENARIOS:
        item = scenarios.get(name)
        if not isinstance(item, dict):
            errors.append(f"{name}: result missing")
            continue
        if item.get("passed") is not True:
            errors.append(f"{name}: failure simulation did not pass")
        if item.get("commit_sha") != report.get("commit_sha"):
            errors.append(f"{name}: commit mismatch")
        if item.get("orders_accepted_after_failure") != 0:
            errors.append(f"{name}: orders accepted after failure")
        if item.get("unreconciled_orders") != 0:
            errors.append(f"{name}: unreconciled orders")
        if not isinstance(item.get("test_command"), str) or not item["test_command"].strip():
            errors.append(f"{name}: test command missing")
        if not isinstance(item.get("ci_run_url"), str) or not re.fullmatch(
            r"https://github\.com/Osiris6900/osiris-eye/actions/runs/[1-9][0-9]*", item["ci_run_url"]
        ):
            errors.append(f"{name}: CI run URL missing or invalid")
    return errors


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    errors = validate(json.loads(args.report.read_text(encoding="utf-8")))
    print(json.dumps({"passed": not errors, "errors": errors}, indent=2))
    raise SystemExit(bool(errors))


if __name__ == "__main__":
    main()
