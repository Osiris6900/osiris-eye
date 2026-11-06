"""Linux Unix-domain receipt listener with kernel-verified peer credentials.

The supervisor pins the Rust process PID and UID out of band. Event JSON is
never a credential. A same-UID process with a different PID is rejected before
its bytes are parsed. The transport ack is written only after SQLite commits.

Trust limit: code already running inside this process can reach the ledger
file directly. The credential seal stops a boolean flag from acknowledging a
receipt; it is not a defense against a compromised interpreter. SO_PEERCRED
PIDs can be reused after the pinned process exits, so the supervisor must
re-pin both PIDs when either process restarts.
"""
from __future__ import annotations

import json
import os
import socket
import stat
import struct
import threading
from collections.abc import Callable
from pathlib import Path

from .verified_receipt_consumer import VerifiedReceiptConsumer
from .verified_receipt_event import ACK_SCHEMA, APPLIED, DUPLICATE

MAX_FRAME_BYTES = 8192
_SEAL = object()


class LinuxPeerCredentials:
    """Kernel identity of one accepted AF_UNIX peer. Not constructible from a flag."""

    __slots__ = ("pid", "uid")

    def __init__(self, pid: int, uid: int, seal: object):
        if seal is not _SEAL or type(pid) is not int or type(uid) is not int or pid <= 0 or uid < 0:
            raise ValueError("untrusted_peer_credential")
        self.pid = pid
        self.uid = uid


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("receipt_duplicate_json_key")
        result[key] = value
    return result


def _read_exact(connection: socket.socket, count: int) -> bytes:
    result = bytearray()
    while len(result) < count:
        chunk = connection.recv(count - len(result))
        if not chunk:
            raise ValueError("receipt_frame_truncated")
        result.extend(chunk)
    return bytes(result)


def _write_frame(connection: socket.socket, payload: bytes) -> None:
    if not 1 <= len(payload) <= MAX_FRAME_BYTES:
        raise ValueError("invalid_receipt_frame_size")
    connection.sendall(struct.pack("!I", len(payload)) + payload)


def _authenticate(connection: socket.socket, *, expected_rust_pid: int, expected_rust_uid: int) -> LinuxPeerCredentials:
    if (type(expected_rust_pid) is not int or expected_rust_pid <= 0
            or type(expected_rust_uid) is not int or expected_rust_uid < 0):
        raise ValueError("invalid_expected_rust_identity")
    if connection.family != socket.AF_UNIX or connection.type & socket.SOCK_STREAM != socket.SOCK_STREAM:
        raise ValueError("receipt_unix_stream_required")
    if not hasattr(socket, "SO_PEERCRED"):
        raise RuntimeError("linux_peer_credentials_required")
    credentials = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
    pid, uid, _gid = struct.unpack("3i", credentials)
    if pid != expected_rust_pid or uid != expected_rust_uid:
        raise ValueError("unauthenticated_rust_peer")
    return LinuxPeerCredentials(pid, uid, _SEAL)


def bind_verified_receipt_listener(path: str) -> socket.socket:
    """Bind an AF_UNIX stream socket in a 0700 directory owned by this user."""
    if not isinstance(path, str) or not path or "\x00" in path or not path.startswith("/"):
        raise ValueError("invalid_receipt_socket_path")
    socket_path = Path(path)
    parent = socket_path.parent
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(parent, 0o700)
    directory = parent.lstat()
    if stat.S_ISLNK(directory.st_mode) or not stat.S_ISDIR(directory.st_mode):
        raise ValueError("receipt_socket_directory_invalid")
    if stat.S_IMODE(directory.st_mode) != 0o700 or directory.st_uid != os.geteuid():
        raise ValueError("receipt_socket_directory_permissions")
    if socket_path.exists() or socket_path.is_symlink():
        current = socket_path.lstat()
        if stat.S_ISLNK(current.st_mode) or not stat.S_ISSOCK(current.st_mode) or current.st_uid != os.geteuid():
            raise ValueError("receipt_socket_path_occupied")
        os.unlink(socket_path)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        listener.bind(os.fspath(socket_path))
        os.chmod(socket_path, 0o600)
        listener.listen(4)
    except Exception:
        listener.close()
        raise
    return listener


def receive_verified_receipt(
    connection: socket.socket,
    consumer: VerifiedReceiptConsumer,
    *,
    expected_rust_pid: int,
    expected_rust_uid: int,
    now: int,
) -> str:
    """Persist one authenticated frame, then write the transport ack.

    ``applied`` acknowledges the correlated receipt. ``duplicate`` confirms a
    byte-for-byte replay of an event already stored and does not change state.
    Failures raise before any ack bytes are written.
    """
    credentials = _authenticate(
        connection, expected_rust_pid=expected_rust_pid, expected_rust_uid=expected_rust_uid,
    )
    size = struct.unpack("!I", _read_exact(connection, 4))[0]
    if not 1 <= size <= MAX_FRAME_BYTES:
        raise ValueError("invalid_receipt_frame_size")
    try:
        event = json.loads(
            _read_exact(connection, size).decode("utf-8"),
            object_pairs_hook=_unique_pairs,
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid_receipt_frame_json") from exc
    disposition = consumer.consume(event, peer_credentials=credentials, now=now)
    if disposition not in (APPLIED, DUPLICATE):
        raise ValueError("invalid_receipt_disposition")
    ack = json.dumps(
        {"schema": ACK_SCHEMA, "event_id": event["event_id"], "disposition": disposition},
        separators=(",", ":"),
    ).encode("utf-8")
    _write_frame(connection, ack)
    return disposition


def accept_one_verified_receipt(
    listener: socket.socket,
    consumer: VerifiedReceiptConsumer,
    *,
    expected_rust_pid: int,
    expected_rust_uid: int,
    now: int,
) -> str:
    """Accept one backlog connection and consume its single event frame."""
    connection, _address = listener.accept()
    try:
        return receive_verified_receipt(
            connection, consumer,
            expected_rust_pid=expected_rust_pid,
            expected_rust_uid=expected_rust_uid,
            now=now,
        )
    finally:
        connection.close()


def serve_verified_receipts(
    listener: socket.socket,
    consumer: VerifiedReceiptConsumer,
    *,
    expected_rust_pid: int,
    expected_rust_uid: int,
    now_fn: Callable[[], int],
    stop: threading.Event,
) -> None:
    """Accept events until ``stop`` is set. Per-connection failures are isolated."""
    listener.settimeout(0.2)
    while not stop.is_set():
        try:
            connection, _address = listener.accept()
        except TimeoutError:
            continue
        except OSError:
            break
        try:
            connection.settimeout(5)
            receive_verified_receipt(
                connection, consumer,
                expected_rust_pid=expected_rust_pid,
                expected_rust_uid=expected_rust_uid,
                now=now_fn(),
            )
        except (OSError, ValueError, RuntimeError):
            pass
        finally:
            connection.close()
