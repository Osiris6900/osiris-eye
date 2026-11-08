"""Dependency lockfile inventory gate; not a vulnerability scan."""
import argparse
import json
from pathlib import Path

REQUIRED = (
    "services/p2p-node/Cargo.lock",
    "apps/web/package-lock.json",
    "services/quant-api/pyproject.toml",
)

def validate(root):
    root = Path(root)
    errors = []
    for relative in REQUIRED:
        path = root / relative
        if not path.is_file() or path.stat().st_size == 0:
            errors.append(f"missing or empty dependency input: {relative}")
    lock = root / "apps/web/package-lock.json"
    if lock.is_file():
        try:
            data = json.loads(lock.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("lockfileVersion") not in (2, 3) or not isinstance(data.get("packages"), dict):
                errors.append("npm lockfile must contain versioned package graph")
        except (ValueError, UnicodeError):
            errors.append("invalid npm lockfile JSON")
    return errors

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    args = parser.parse_args()
    problems = validate(args.root)
    print(json.dumps({"passed": not problems, "errors": problems}))
    raise SystemExit(bool(problems))
