"""Explicit, digest-pinned native feed invocation over an operator-owned capture root."""

import hashlib
import json
import subprocess
from pathlib import Path


def run_native(
    binary: str,
    binary_sha256: str,
    capture_root: str,
    paths: list[str],
    *,
    workers: int = 1,
    prefetch: bool = False,
):
    executable, root = Path(binary).resolve(strict=True), Path(capture_root).resolve(strict=True)
    if (
        not executable.is_file()
        or hashlib.sha256(executable.read_bytes()).hexdigest() != binary_sha256
    ):
        raise ValueError("native_binary_digest_mismatch")
    if (
        type(workers) is not int
        or not 1 <= workers <= 16
        or not isinstance(paths, list)
        or not 1 <= len(paths) <= 8
    ):
        raise ValueError("invalid_native_configuration")
    args = [str(executable), "--workers", str(workers)]
    if prefetch:
        args.append("--prefetch")
    for path in paths:
        capture = (root / path).resolve(strict=True)
        if not capture.is_file() or not capture.is_relative_to(root):
            raise ValueError("capture_outside_configured_root")
        args.extend(["--file", str(capture)])
    result = subprocess.run(args, capture_output=True, timeout=30, check=False)
    if result.returncode or len(result.stdout) > 65536:
        raise ValueError("native_processing_failed")
    return json.loads(result.stdout)
