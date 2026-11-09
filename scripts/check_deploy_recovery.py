#!/usr/bin/env python3
"""Dry-run check for staging and production recovery plans.

This script does not deploy, roll back, or contact a host. A passing
check is not evidence that a recovery drill ran.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REQUIRED = ("staging", "production")


def validate(plan: dict) -> list[str]:
    errors = []
    if not isinstance(plan, dict) or plan.get("schema") != "osiris.deploy-recovery-plan.v1":
        return ["schema must be osiris.deploy-recovery-plan.v1"]
    if plan.get("live_trading_authorized") is not False:
        errors.append("live_trading_authorized must be false")
    envs = plan.get("environments")
    if not isinstance(envs, dict):
        errors.append("environments must be an object")
    else:
        for name in REQUIRED:
            env = envs.get(name)
            if not isinstance(env, dict):
                errors.append(f"{name} environment required")
                continue
            rollback = env.get("rollback_command")
            if not isinstance(rollback, str) or not rollback.strip():
                errors.append(f"{name} rollback_command required")
            if env.get("drill_status") not in ("not_run", "passed", "failed"):
                errors.append(f"{name} drill_status must be not_run, passed, or failed")
            if env.get("drill_status") == "passed" and not isinstance(env.get("evidence_url"), str):
                errors.append(f"{name} passed drill requires evidence_url")
    if plan.get("executes_commands") is not False:
        errors.append("executes_commands must be false")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path)
    args = parser.parse_args(argv)
    errors = validate(json.loads(args.plan.read_text(encoding="utf-8")))
    print(json.dumps({"passed": not errors, "errors": errors, "executed": False}))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
