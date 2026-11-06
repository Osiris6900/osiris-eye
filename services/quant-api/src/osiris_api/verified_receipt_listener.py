"""One-shot supervisor-owned Unix socket receipt ingestion.

The supervisor MUST provide the Rust child PID, UID and sender peer ID from
its own process launch and authenticated configuration, never from client data.
This listener is deliberately not a background daemon and does not provide
durable Rust-side event delivery or an acknowledgement protocol.
"""
from __future__ import annotations

import os
import socket
import stat
from pathlib import Path

from .verified_receipt_consumer import VerifiedReceiptConsumer
from .verified_receipt_unix import receive_verified_receipt


def serve_one_receipt(
    socket_path: str,
    consumer: VerifiedReceiptConsumer,
    *,
    expected_rust_pid: int,
    expected_rust_uid: int,
    now: int,
    timeout_seconds: float = 2.0,
) -> bool:
    """Bind protected socket, accept one pinned peer and persist one event.

    Socket directory must already exist, be owned by the current effective
    UID, and be non-writable by group/other. Existing paths are never unlinked
    to avoid replacing another process's socket. Unix only.
    """
    if os.name != "posix" or not hasattr(socket, "SO_PEERCRED"):
        raise RuntimeError("linux_unix_credentials_required")
    path = Path(socket_path)
    if not path.is_absolute() or not path.name or len(os.fsencode(socket_path)) >= 100:
        raise ValueError("invalid_receipt_socket_path")
    parent = path.parent
    info = parent.stat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
            or info.st_mode & 0o022):
        raise ValueError("insecure_receipt_socket_directory")
    if path.is_symlink() or path.exists():
        raise ValueError("receipt_socket_already_exists")
    if type(timeout_seconds) not in (float, int) or not 0 < timeout_seconds <= 30:
        raise ValueError("invalid_receipt_socket_timeout")
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        listener.bind(socket_path)
        os.chmod(socket_path, 0o600)
        listener.listen(1)
        listener.settimeout(timeout_seconds)
        with listener.accept()[0] as connection:
            connection.settimeout(timeout_seconds)
            return receive_verified_receipt(
                connection, consumer,
                expected_rust_pid=expected_rust_pid,
                expected_rust_uid=expected_rust_uid, now=now,
            )
    finally:
        listener.close()
        # Only unlink the socket we created, not a preexisting path.
        try:
            if path.is_socket():
                path.unlink()
        except FileNotFoundError:
            pass
