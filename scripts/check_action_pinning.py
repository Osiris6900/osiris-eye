"""Fail-closed ratchet for third-party GitHub Actions references.

Scans every workflow YAML, including composite local actions' workflow files.
This does not establish that pinned commits have been independently audited.
"""
import argparse
import re
from pathlib import Path

USE = re.compile(r"^\s*(?:-\s*)?uses:\s*['\"]?([^\s#'\"]+)", re.MULTILINE)
SHA = re.compile(r"^[0-9a-fA-F]{40}$")

def inspect(text, filename):
    errors = []
    for action in USE.findall(text):
        if action.startswith("./"):
            continue
        if action.startswith("docker://"):
            if "@sha256:" not in action or not re.fullmatch(r"[0-9a-fA-F]{64}", action.split("@sha256:", 1)[-1]):
                errors.append(f"{filename}: unpinned docker action {action}")
            continue
        if "@" not in action or not SHA.fullmatch(action.rsplit("@", 1)[-1]):
            errors.append(f"{filename}: mutable action reference {action}")
    if not re.search(r"(?m)^permissions:\s*$", text):
        errors.append(f"{filename}: missing explicit top-level permissions")
    return errors

def check(root):
    root = Path(root)
    files = sorted(set(root.glob("*.yml")) | set(root.glob("*.yaml")))
    if not files:
        return ["no workflow YAML files found"]
    errors = []
    for path in files:
        errors.extend(inspect(path.read_text(encoding="utf-8"), str(path)))
    return errors

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--workflows", default=".github/workflows")
    args = parser.parse_args()
    errors = check(args.workflows)
    for error in errors:
        print(error)
    raise SystemExit(bool(errors))
