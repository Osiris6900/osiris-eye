#!/usr/bin/env python3
"""Regenerate the hashed Python deployment lock for the quant API.

Runs pip-tools (backtracking resolver) against
`services/quant-api/pyproject.toml` with the supported deployment extras
(see check_python_lock.DEPLOYMENT_EXTRAS), then rewrites the output with an
explicit metadata header so scripts/check_python_lock.py can detect a stale
lock. Every resolved distribution is exactly pinned and carries
`--hash=sha256:` artifact digests from the package index.

Requirements: Python 3.11 (the supported deployment interpreter) with
pip-tools installed (`pip install pip-tools`). Network access to a package
index is required; the default index is PyPI, and a mirror may be supplied
with --index-url (artifact hashes are identical for identical files).

Example:
    python scripts/resolve_python_lock.py
    python scripts/resolve_python_lock.py --index-url https://mirrors.aliyun.com/pypi/simple/
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_python_lock import DEPLOYMENT_EXTRAS  # noqa: E402
from python_lock import LOCK_HEADER_SCHEMA, LOCK_RELATIVE_PATH, parse_hashed_lock  # noqa: E402

SUPPORTED_PYTHON = "3.11"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def resolve_lock(
    pyproject: Path,
    output: Path,
    index_url: str | None = None,
    pip_compile: str | None = None,
    now: str | None = None,
) -> None:
    binary = pip_compile or shutil.which("pip-compile")
    if binary is None:
        raise ValueError("pip-compile not found; install pip-tools into a Python 3.11 environment")
    command = [
        binary,
        "--resolver=backtracking",
        "--generate-hashes",
        "--no-emit-index-url",
        "--no-emit-find-links",
        "--quiet",
    ]
    for extra in DEPLOYMENT_EXTRAS:
        command.extend(["--extra", extra])
    command.extend(["--output-file", "-", str(pyproject)])
    if index_url:
        command.extend(["--index-url", index_url])
    result = subprocess.run(command, capture_output=True, text=True, timeout=1800)
    if result.returncode != 0:
        raise ValueError(f"pip-compile failed (exit {result.returncode}): {result.stderr.strip()[:2000]}")
    body = result.stdout
    if "==" not in body or "--hash=sha256:" not in body:
        raise ValueError("pip-compile produced no hashed requirements; refusing to write the lock")

    generated_at = now or datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    header = (
        f"# schema: {LOCK_HEADER_SCHEMA}\n"
        f"# generated_by: scripts/resolve_python_lock.py\n"
        f"# generator: pip-compile (pip-tools) --resolver=backtracking --generate-hashes "
        f"--extra {' --extra '.join(DEPLOYMENT_EXTRAS)}\n"
        f"# python_version: {SUPPORTED_PYTHON}\n"
        f"# pyproject_sha256: {sha256_file(pyproject)}\n"
        f"# generated_at: {generated_at}\n"
        f"# regenerate: python scripts/resolve_python_lock.py "
        f"(optionally --index-url <mirror>); verify: python scripts/check_python_lock.py\n"
        f"# scope: resolved deployment environment only; not a vulnerability scan or license review\n"
    )
    # Drop the pip-compile comment block (it duplicates and could fork the
    # metadata contract); keep only requirement lines and their hashes.
    kept = [line for line in body.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(header + "\n" + "\n".join(kept) + "\n")
    # Fail closed: never leave behind a lock our own parser rejects.
    entries = parse_hashed_lock(output)
    print(f"Wrote {output}: {len(entries)} pinned+hashed distributions (python {SUPPORTED_PYTHON})")


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pyproject", type=Path, default=root / "services/quant-api/pyproject.toml")
    parser.add_argument("--output", type=Path, default=root / LOCK_RELATIVE_PATH)
    parser.add_argument("--index-url", default=None)
    parser.add_argument("--pip-compile", default=None, help="explicit pip-compile binary path")
    args = parser.parse_args()
    version = sys.version_info
    if (version.major, version.minor) != tuple(int(part) for part in SUPPORTED_PYTHON.split(".")):
        raise SystemExit(
            f"resolve_python_lock.py must run under Python {SUPPORTED_PYTHON} "
            f"(running {version.major}.{version.minor}); use python{SUPPORTED_PYTHON}"
        )
    try:
        resolve_lock(args.pyproject, args.output, args.index_url, args.pip_compile)
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
