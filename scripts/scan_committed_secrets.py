"""Fail when a tracked file is a private key, a known token, or a dotenv file.

The scanner prints kinds and paths only. It does not print the matched secret.
``.env.example`` is allowed. Fixture strings that mention a key header without a
key body are not treated as private keys.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

_PRIVATE_KEY = re.compile(
    r"-----BEGIN (?:RSA |OPENSSH |EC |ENCRYPTED )?PRIVATE KEY-----"
    r"\s*[A-Za-z0-9+/=]{32,}\s*"
    r"-----END (?:RSA |OPENSSH |EC |ENCRYPTED )?PRIVATE KEY-----"
)
_TOKEN = re.compile(
    r"ghp_[A-Za-z0-9]{20,}"
    r"|github_pat_[A-Za-z0-9_]{20,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|xox[baprs]-[A-Za-z0-9-]{10,}"
    r"|sk_live_[A-Za-z0-9]{16,}"
)


def repository_root() -> Path:
    start = Path(__file__).resolve().parent
    for candidate in (start, *start.parents):
        if (candidate / ".git").exists():
            return candidate
    raise SystemExit("repository_root_not_found")


def _is_dotenv(path: Path) -> bool:
    name = path.name
    if name == ".env":
        return True
    return name.startswith(".env.") and not name.endswith(".example")


def scan_paths(paths) -> list[dict]:
    findings = []
    for path in paths:
        path = Path(path)
        if _is_dotenv(path):
            findings.append({"path": str(path), "kind": "dotenv_file"})
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if _PRIVATE_KEY.search(text):
            findings.append({"path": str(path), "kind": "private_key"})
        if _TOKEN.search(text):
            findings.append({"path": str(path), "kind": "api_token"})
    return findings


def scan_repository(root: Path | None = None) -> list[dict]:
    root = repository_root() if root is None else Path(root)
    completed = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, check=True, capture_output=True
    )
    paths = [root / name.decode() for name in completed.stdout.split(b"\0") if name]
    return scan_paths(paths)


def main() -> int:
    findings = scan_repository()
    if findings:
        for item in findings:
            print(f"{item['kind']}:{item['path']}", file=sys.stderr)
        return 1
    print("committed_secrets: none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
