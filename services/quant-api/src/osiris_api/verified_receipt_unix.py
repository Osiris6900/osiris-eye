"""Linux Unix-domain receipt frame adapter with kernel-verified peer credentials.

Only use on an AF_UNIX SOCK_STREAM connection accepted from a protected
socket path. Pin the *actual* Rust process PID and UID out-of-band (e.g. the
supervisor's child PID); never accept either from event JSON. A same-UID
process is not sufficient authentication without the PID pin. No network
listener is exposed by this module.
"""
from __future__ import annotations

import json
import socket
import struct

from .verified_receipt_consumer import VerifiedReceiptConsumer

MAX_FRAME_BYTES = 8192


def _read_exact(connection: socket.socket, count: int) -> bytes:
    result = bytearray()
    while len(result) < count:
        chunk = connection.recv(count - len(result))
        if not chunk:
            raise ValueError("receipt_frame_truncated")
        result.extend(chunk)
    return bytes(result)


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("receipt_duplicate_json_key")
        result[key] = value
    return result


def receive_verified_receipt(
    connection: socket.socket,
    consumer: VerifiedReceiptConsumer,
    *,
    expected_rust_pid: int,
    expected_rust_uid: int,
    now: int,
) -> bool:
    """Consume one length-prefixed JSON event only from pinned Linux peer.

    Authentication uses Linux SO_PEERCRED, not client-provided booleans.
    The caller must obtain the PID from a trusted supervisor and ensure the
    socket was created in a protected directory; a reused PID must not be
    trusted across process restarts without supervisor re-pinning.
    """
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
    size = struct.unpack("!I", _read_exact(connection, 4))[0]
    if not 1 <= size <= MAX_FRAME_BYTES:
        raise ValueError("invalid_receipt_frame_size")
    try:
        event = json.loads(_read_exact(connection, size).decode("utf-8"),
                           object_pairs_hook=_unique_pairs)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid_receipt_frame_json") from exc
    return consumer.consume(event, authenticated_rust_peer=True, now=now)
