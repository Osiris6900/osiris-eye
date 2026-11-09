#!/usr/bin/env python3
"""Fail closed when any workflow uses a mutable third-party action reference."""
from pathlib import Path
import re
import sys

USE = re.compile(r"""^\s*(?:-\s*)?uses:\s*['"]?([^\s#'"]+)""", re.MULTILINE)
SHA = re.compile(r"^[0-9a-fA-F]{40}$")


def violations(root: Path) -> list[str]:
    errors = []
    workflows = root / ".github" / "workflows"
    for path in sorted(workflows.glob("*.yml")) + sorted(workflows.glob("*.yaml")):
        for action in USE.findall(path.read_text(encoding="utf-8")):
            if action.startswith("./"):
                continue
            if action.count("@") != 1 or not SHA.fullmatch(action.rsplit("@", 1)[-1]):
                errors.append(f"{path.relative_to(root)}: unpinned action {action}")
    return errors


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    errors = violations(root)
    for error in errors:
        print(error, file=sys.stderr)
    print(f"Action pin ratchet: {'FAIL' if errors else 'PASS'}")
    raise SystemExit(bool(errors))
