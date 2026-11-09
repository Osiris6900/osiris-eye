#!/usr/bin/env python3
"""Sanitized evidence for the recipient E2EE SSH harnesses.

The record keeps commit identity, the SHA-256 of each named node binary, a
boot-id fingerprint, public PeerIds, and the payload digest. It does not store
plaintext, private keys, SSH targets, or overlay addresses.

``local_or_same_host`` covers a single host, a missing fingerprint, or identical
boot ids. ``distinct_boot_id_hosts`` means those fingerprints differed. That is
the same boot-id file the soak harness hashes. It is not a site survey, a
public deployment, or an independent cryptographic audit.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
from datetime import datetime, timezone
from pathlib import Path

BOOT_ID_PATH = "/proc/sys/kernel/random/boot_id"
DISTINCT_BOOT_IDS = "distinct_boot_id_hosts"
LOCAL_OR_SAME_HOST = "local_or_same_host"
SCHEMA = "osiris.p2p.recipient-e2ee-acceptance.v1"
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")


def sha256_file(path: Path) -> str:
    if not path.is_file():
        raise ValueError("binary_missing")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_boot_id(path: Path | None = None) -> str | None:
    candidate = path or Path(BOOT_ID_PATH)
    try:
        data = candidate.read_bytes()
    except OSError:
        return None
    if not data.strip():
        return None
    return hashlib.sha256(data).hexdigest()


def parse_sha256sum(stdout: str, expected_path: str) -> str:
    """Accept one GNU sha256sum line naming exactly ``expected_path``."""
    lines = [line for line in stdout.splitlines() if line.strip()]
    if len(lines) != 1:
        raise ValueError("sha256sum_output_invalid")
    match = re.fullmatch(r"([0-9a-f]{64}) [ *](.*)", lines[0])
    if match is None or match.group(2) != expected_path:
        raise ValueError("sha256sum_output_invalid")
    return match.group(1)


def measurement_class(boot_ids: list) -> str:
    if len(boot_ids) < 2:
        return LOCAL_OR_SAME_HOST
    if any(not isinstance(item, str) or _DIGEST.fullmatch(item) is None for item in boot_ids):
        return LOCAL_OR_SAME_HOST
    if len(set(boot_ids)) != len(boot_ids):
        return LOCAL_OR_SAME_HOST
    return DISTINCT_BOOT_IDS


def remote_file_sha256(ssh_target: str, path: str, ssh_options: list[str], runner=None) -> str:
    if not ssh_target or not path or path.startswith("-"):
        raise ValueError("remote_hash_target_missing")
    run = runner or subprocess.run
    command = ["ssh", "-T", *ssh_options, ssh_target, "sha256sum -- " + shlex.quote(path)]
    completed = run(command, check=False, capture_output=True, text=True, timeout=30)
    if getattr(completed, "returncode", 1) != 0:
        raise RuntimeError("remote_sha256sum_failed")
    return parse_sha256sum(getattr(completed, "stdout", "") or "", path)


def _run_git(args: list[str], runner):
    try:
        return runner(args, check=False, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None


def source_revision(env: dict | None = None, runner=None):
    env = os.environ if env is None else env
    run = runner or subprocess.run
    sha = env.get("GITHUB_SHA") or ""
    source = "GITHUB_SHA" if _COMMIT.fullmatch(sha) else None
    if source is None:
        completed = _run_git(["git", "rev-parse", "HEAD"], run)
        if completed is not None and completed.returncode == 0:
            candidate = completed.stdout.strip()
            if _COMMIT.fullmatch(candidate):
                sha = candidate
                source = "git_rev_parse"
            else:
                sha = None
        else:
            sha = None
    clean = None
    status = _run_git(["git", "status", "--porcelain"], run)
    if status is not None and status.returncode == 0:
        clean = status.stdout == ""
    return sha, source, clean


def workflow_url(env: dict | None = None) -> str | None:
    env = os.environ if env is None else env
    server = (env.get("GITHUB_SERVER_URL") or "").rstrip("/")
    repo = env.get("GITHUB_REPOSITORY") or ""
    run_id = env.get("GITHUB_RUN_ID") or ""
    if server.startswith("https://") and "/" in repo and run_id.isdigit() and ".." not in repo:
        return f"{server}/{repo}/actions/runs/{run_id}"
    return None


def _sanitize_error(error: str | None) -> str | None:
    if not error:
        return None
    text = " ".join(error.split())
    lowered = text.lower()
    if "private key" in lowered or "private_key" in lowered or "begin openssh" in lowered:
        return "error_redacted"
    return text[:500]


class AcceptanceEvidence:
    def __init__(
        self,
        *,
        topology: str,
        marker: str,
        require_distinct_hosts: bool = False,
        evidence_path: Path | None = None,
        environ: dict | None = None,
        runner=None,
        boot_id_path: Path | None = None,
    ):
        if topology not in {"two_host_recipient", "three_host_recipient_only"}:
            raise ValueError("unsupported_e2ee_topology")
        self.topology = topology
        self.marker = marker
        self.require_distinct_hosts = require_distinct_hosts
        self.evidence_path = evidence_path
        self.environ = os.environ if environ is None else environ
        self.runner = runner or subprocess.run
        self.boot_id_path = boot_id_path
        self.hosts: list[dict] = []
        self.closed = False
        self.record: dict | None = None
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.payload_sha256 = None
        self.recipient_peer_id = None
        self.non_recipient_peer_id = None
        self.recipient_digest_matched = False
        self.nonrecipient_discarded = None

    def _add(self, host_id: str, role: str, binary_sha256: str, boot_id_sha256: str | None):
        if not host_id or any(host["host_id"] == host_id for host in self.hosts):
            raise ValueError("invalid_host_id")
        if _DIGEST.fullmatch(binary_sha256 or "") is None:
            raise ValueError("invalid_binary_sha256")
        if boot_id_sha256 is not None and _DIGEST.fullmatch(boot_id_sha256) is None:
            raise ValueError("invalid_boot_id_sha256")
        self.hosts.append({
            "host_id": host_id,
            "role": role,
            "binary_sha256": binary_sha256,
            "boot_id_sha256": boot_id_sha256,
            "peer_id": None,
        })

    def add_local_binary(self, host_id: str, role: str, binary: Path):
        self._add(host_id, role, sha256_file(binary), sha256_boot_id(self.boot_id_path))

    def add_remote_binary(self, host_id: str, role: str, ssh_target: str, remote_binary: str, ssh_options: list[str]):
        binary_hash = remote_file_sha256(ssh_target, remote_binary, ssh_options, self.runner)
        boot_hash = remote_file_sha256(ssh_target, BOOT_ID_PATH, ssh_options, self.runner)
        self._add(host_id, role, binary_hash, boot_hash)

    def set_peer(self, host_id: str, peer_id: str):
        if not peer_id or len(peer_id) > 128 or any(char.isspace() for char in peer_id):
            raise ValueError("invalid_peer_id")
        for host in self.hosts:
            if host["host_id"] == host_id:
                host["peer_id"] = peer_id
                return
        raise ValueError("unknown_host_id")

    def set_delivery(
        self,
        *,
        payload_sha256: str,
        recipient_peer_id: str,
        recipient_digest_matched: bool,
        non_recipient_peer_id: str | None = None,
        nonrecipient_discarded: bool | None = None,
    ):
        if _DIGEST.fullmatch(payload_sha256 or "") is None:
            raise ValueError("invalid_payload_sha256")
        self.payload_sha256 = payload_sha256
        self.recipient_peer_id = recipient_peer_id
        self.recipient_digest_matched = recipient_digest_matched
        self.non_recipient_peer_id = non_recipient_peer_id
        self.nonrecipient_discarded = nonrecipient_discarded

    def finish(self, e2ee_assertions_passed: bool, error: str | None = None) -> dict:
        if self.closed:
            return self.record
        commit, commit_source, clean = source_revision(self.environ, self.runner)
        boot_ids = [host["boot_id_sha256"] for host in self.hosts]
        host_class = measurement_class(boot_ids)
        separation_ok = host_class == DISTINCT_BOOT_IDS
        enforced = (not self.require_distinct_hosts) or separation_ok
        passed = bool(e2ee_assertions_passed) and enforced
        failure = _sanitize_error(error)
        if e2ee_assertions_passed and not enforced:
            failure = "host_boot_ids_not_distinct"
        invocation = "github_actions" if self.environ.get("GITHUB_ACTIONS") == "true" else "local_or_operator"
        record = {
            "schema": SCHEMA,
            "topology": self.topology,
            "marker": self.marker,
            "measurement_class": host_class,
            "invocation": invocation,
            "independent_cryptographic_audit": False,
            "plaintext_retained": False,
            "commit_sha": commit,
            "commit_sha_source": commit_source,
            "clean_worktree": clean,
            "workflow_url": workflow_url(self.environ),
            "attempt": int(self.environ["GITHUB_RUN_ATTEMPT"]) if str(self.environ.get("GITHUB_RUN_ATTEMPT", "")).isdigit() else None,
            "started_at": self.started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "e2ee_assertions_passed": bool(e2ee_assertions_passed),
            "require_distinct_hosts": self.require_distinct_hosts,
            "test_passed": passed,
            "hosts": sorted(self.hosts, key=lambda host: host["host_id"]),
            "recipient_peer_id": self.recipient_peer_id,
            "non_recipient_peer_id": self.non_recipient_peer_id,
            "payload_sha256": self.payload_sha256,
            "recipient_digest_matched": self.recipient_digest_matched,
            "nonrecipient_discarded": self.nonrecipient_discarded,
            "error": failure,
            "notes": [
                "binary_sha256 is the SHA-256 of the named node binary file.",
                "boot_id_sha256 is the SHA-256 of /proc/sys/kernel/random/boot_id bytes, the same fingerprint file the soak harness uses.",
                "distinct_boot_id_hosts means those fingerprints differed. It does not prove a public deployment or an independent cryptographic audit.",
                "local_or_same_host means one host, a missing fingerprint, or identical boot ids. Do not describe that run as independent-host evidence.",
                "independent_cryptographic_audit is false for every record this harness writes.",
            ],
        }
        encoded = json.dumps(record, sort_keys=True, indent=2) + "\n"
        file_sha = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        if self.evidence_path is not None:
            self.evidence_path.parent.mkdir(parents=True, exist_ok=True)
            self.evidence_path.write_text(encoded, encoding="utf-8")
        print(f"E2EE_MEASUREMENT_CLASS={host_class}", flush=True)
        print(f"E2EE_ACCEPTANCE_EVIDENCE_SHA256={file_sha}", flush=True)
        for host in record["hosts"]:
            print(
                f"E2EE_BINARY_SHA256 host={host['host_id']} sha256={host['binary_sha256']}",
                flush=True,
            )
        self.record = record
        self.closed = True
        if e2ee_assertions_passed and not enforced:
            raise RuntimeError("host_boot_ids_not_distinct")
        return record
