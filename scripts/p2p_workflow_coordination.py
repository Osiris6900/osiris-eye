#!/usr/bin/env python3
"""Bounded, same-run artifact coordination for independent P2P CI hosts.

Only endpoint addresses, SSH *public* host keys, and completion status belong in
these artifacts. SSH client private keys and node key/state directories do not.
"""
from __future__ import annotations

import argparse
import base64
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import time


def artifact_exists(repository: str, run_id: str, name: str, *, timeout: float = 30) -> bool:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("invalid repository")
    if not run_id.isdecimal():
        raise ValueError("invalid run ID")
    if not 0 < timeout <= 30:
        raise ValueError("artifact API timeout must be between 0 and 30 seconds")
    response = subprocess.run(
        ["gh", "api", f"repos/{repository}/actions/runs/{run_id}/artifacts?per_page=100"],
        check=True, capture_output=True, text=True, timeout=timeout,
    )
    return any(
        artifact.get("name") == name and artifact.get("expired") is False
        for artifact in json.loads(response.stdout)["artifacts"]
    )


def wait_for_artifact(repository: str, run_id: str, name: str, timeout: float) -> None:
    if not 0 < timeout <= 1200:
        raise ValueError("artifact timeout must be between 0 and 1200 seconds")
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"Timed out waiting for same-run artifact: {name}")
        try:
            found = artifact_exists(repository, run_id, name, timeout=min(30, remaining))
            # A response arriving after the deadline cannot release another host.
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Timed out waiting for same-run artifact: {name}")
            if found:
                print(f"Same-run artifact ready: {name}", flush=True)
                return
        except (subprocess.SubprocessError, KeyError, json.JSONDecodeError):
            # Temporary API errors do not skip the gate or print auth-bearing output.
            pass
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"Timed out waiting for same-run artifact: {name}")
        time.sleep(min(3, remaining))


def public_host_key(value: str) -> str:
    fields = value.strip().split()
    if len(fields) not in (2, 3) or fields[0] != "ssh-ed25519":
        raise ValueError("expected one Ed25519 SSH public host key")
    raw = base64.b64decode(fields[1], validate=True)
    # OpenSSH wire encoding: uint32 length + algorithm + uint32 length + 32 bytes.
    if len(raw) != 51 or raw[:19] != b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20":
        raise ValueError("malformed Ed25519 SSH public host key")
    return " ".join(fields[:2])


def write_endpoint(path: Path, ip: str, host_key_path: Path) -> None:
    address = str(ipaddress.ip_address(ip))
    path.write_text(json.dumps({"ip": address, "ssh_port": 22,
                               "ssh_host_key": public_host_key(host_key_path.read_text())}) + "\n")


def write_known_hosts(path: Path, endpoints: list[Path]) -> None:
    lines = []
    for endpoint_path in endpoints:
        endpoint = json.loads(endpoint_path.read_text())
        address = str(ipaddress.ip_address(endpoint["ip"]))
        if endpoint.get("ssh_port") != 22:
            raise ValueError("unsupported SSH endpoint port")
        lines.append(f"{address} {public_host_key(endpoint['ssh_host_key'])}\n")
    if not lines:
        raise ValueError("no SSH host keys to pin")
    path.write_text("".join(lines))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    wait = subparsers.add_parser("wait-artifact")
    wait.add_argument("--name", required=True)
    wait.add_argument("--timeout", type=float, default=600)
    endpoint = subparsers.add_parser("write-endpoint")
    endpoint.add_argument("--path", type=Path, required=True)
    endpoint.add_argument("--ip", required=True)
    endpoint.add_argument("--host-key", type=Path, required=True)
    known_hosts = subparsers.add_parser("known-hosts")
    known_hosts.add_argument("--path", type=Path, required=True)
    known_hosts.add_argument("--endpoint", type=Path, action="append", required=True)
    result = subparsers.add_parser("write-result")
    result.add_argument("--path", type=Path, required=True)
    result.add_argument("--status", choices=("success", "failure", "cancelled", "skipped"), required=True)
    args = parser.parse_args()
    if args.command == "wait-artifact":
        wait_for_artifact(os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_RUN_ID"],
                          args.name, args.timeout)
    elif args.command == "write-endpoint":
        write_endpoint(args.path, args.ip, args.host_key)
    elif args.command == "known-hosts":
        write_known_hosts(args.path, args.endpoint)
    elif args.command == "write-result":
        args.path.parent.mkdir(parents=True, exist_ok=True)
        args.path.write_text(json.dumps({"status": args.status}) + "\n")


if __name__ == "__main__":
    main()
