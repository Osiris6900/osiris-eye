#!/usr/bin/env python3
"""Read-only integration preflight for Osiris Eye.

Never edits files, runs cargo update, or treats unavailable tooling as success.
Run from any working directory: python scripts/master_integration_preflight.py
"""
from __future__ import annotations

import argparse
import ast
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
P2P = ROOT / "services/p2p-node"
PY_SRC = ROOT / "services/quant-api/src/osiris_api"
CHECK_PATHS = (
    P2P / "Cargo.toml",
    P2P / "src/main.rs",
    PY_SRC / "main.py",
)


def run(command: list[str], cwd: Path, timeout: int = 120) -> dict:
    try:
        result = subprocess.run(command, cwd=cwd, capture_output=True, text=True,
                                timeout=timeout, check=False)
        return {"ok": result.returncode == 0, "command": command,
                "exit_code": result.returncode,
                "output": (result.stdout + "\n" + result.stderr)[-3000:]}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "command": command, "error": str(exc)}


def preflight(skip_cargo: bool = False) -> dict:
    checks = []
    for path in CHECK_PATHS:
        checks.append({"check": "path", "path": str(path.relative_to(ROOT)),
                       "ok": path.is_file()})
    for path in (PY_SRC / "risk/input_validator.py", PY_SRC / "risk/engine.py",
                 P2P / "src/crypto/receipt_verification.rs"):
        checks.append({"check": "proposed-module", "path": str(path.relative_to(ROOT)),
                       "exists": path.exists(), "ok": True})
    for path in PY_SRC.rglob("*.py") if PY_SRC.is_dir() else ():
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeError, OSError) as exc:
            checks.append({"check": "python-syntax", "path": str(path.relative_to(ROOT)),
                           "ok": False, "error": str(exc)})
    if not skip_cargo:
        if not shutil.which("cargo"):
            checks.append({"check": "cargo", "ok": False, "error": "cargo unavailable"})
        elif not (P2P / "Cargo.toml").is_file():
            checks.append({"check": "cargo", "ok": False, "error": "manifest missing"})
        else:
            for command in (["cargo", "metadata", "--locked", "--format-version", "1", "--no-deps"],
                            ["cargo", "test", "--locked", "--no-run"]):
                checks.append({"check": "cargo", **run(command, P2P, 240)})
            if shutil.which("cargo-audit"):
                checks.append({"check": "cargo-audit",
                               **run(["cargo", "audit"], P2P, 180)})
            else:
                checks.append({"check": "cargo-audit", "ok": False,
                               "error": "cargo-audit unavailable; cannot claim clean"})
    return {"repository": str(ROOT), "read_only": True,
            "checks": checks, "passed": all(c["ok"] for c in checks)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-cargo", action="store_true",
                        help="Only check paths and Python syntax; NOT a security pass")
    args = parser.parse_args()
    report = preflight(args.skip_cargo)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
