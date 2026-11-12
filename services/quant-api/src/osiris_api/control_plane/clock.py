"""Bounded SNTP clock-offset measurement, not OS clock adjustment or NTS authentication."""

from __future__ import annotations

import math
import secrets
import socket
import struct
import time

NTP_EPOCH = 2_208_988_800


def ntp_stamp(seconds: float) -> bytes:
    if not math.isfinite(seconds) or not 0 <= seconds < 2**32 - NTP_EPOCH:
        raise ValueError("unsupported_ntp_era")
    value = seconds + NTP_EPOCH
    integer = int(value)
    return struct.pack("!II", integer, int((value - integer) * 2**32))


def unix_stamp(raw: bytes) -> float:
    seconds, fraction = struct.unpack("!II", raw)
    return seconds - NTP_EPOCH + fraction / 2**32


def measure_reply(
    packet: bytes, nonce: bytes, sent: float, received: float, elapsed: float
) -> dict:
    if (
        not isinstance(packet, bytes)
        or not isinstance(nonce, bytes)
        or any(type(value) not in (int, float) or not math.isfinite(value)
               for value in (sent, received, elapsed))
        or len(packet) != 48
        or len(nonce) != 8
        or packet[24:32] != nonce
        or packet[0] >> 6 == 3
        or (packet[0] & 7) != 4
        or (packet[0] >> 3 & 7) not in (3, 4)
        or not 1 <= packet[1] <= 15
        or elapsed < 0
        or elapsed > 5
        or abs((received - sent) - elapsed) > 0.25
    ):
        raise ValueError("untrusted_or_invalid_ntp_reply")
    server_receive, server_send = unix_stamp(packet[32:40]), unix_stamp(packet[40:48])
    delay = (received - sent) - (server_send - server_receive)
    if server_send < server_receive or delay < -0.001 or abs(server_receive - sent) > 86400:
        raise ValueError("invalid_ntp_timing")
    offset = ((server_receive - sent) + (server_send - received)) / 2
    return {
        "offset_ms": offset * 1000,
        "round_trip_ms": max(0, delay * 1000),
        "stratum": packet[1],
        "time_source_authenticated": False,
        "os_clock_changed": False,
    }


def probe(host: str, port: int = 123, *, timeout: float = 2) -> dict:
    if (
        not isinstance(host, str)
        or not host
        or len(host) > 253
        or type(port) is not int
        or not 1 <= port <= 65535
        or not 0 < timeout <= 5
    ):
        raise ValueError("invalid_clock_probe")
    nonce = secrets.token_bytes(8)
    packet = bytearray(48)
    packet[0] = 0x23
    packet[40:48] = nonce
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
        connection.settimeout(timeout)
        connection.connect((host, port))
        sent, monotonic_start = time.time(), time.monotonic()
        connection.send(packet)
        response = connection.recv(49)
        received, elapsed = time.time(), time.monotonic() - monotonic_start
    return measure_reply(response, nonce, sent, received, elapsed)
