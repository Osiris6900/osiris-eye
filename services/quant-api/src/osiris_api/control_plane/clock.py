"""Clock measurement and the trusted receive-time used by paper runtime paths.

``probe`` is a bounded SNTP query. It is not used for paper marks, orders, or
tests. Host health reads local chrony, timedatectl, or ntpd status only.

PR #458 (not on main) admits paper marks in ``market_feed_gate`` and checks
freshness against whatever event time, receive time, and ``now`` it is given.
That gate should call ``assess_timestamp_provenance`` with the source event
time and ``TrustedClock`` receive stamp instead of trusting those caller
clocks. This module does not import the feed gate, so the two changes merge
in either order.
"""

from __future__ import annotations

import json
import math
import os
import secrets
import socket
import struct
import subprocess
import time
from datetime import datetime, timezone

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


# Paper receive stamps and host-sync health. Not an SNTP query.
FUTURE_TOLERANCE_S = 2.0
REGRESSION_TOLERANCE_S = 5.0
MAX_EVENT_RECEIVE_LATENCY_S = 86_400.0
MAX_CLOCK_OFFSET_MS = 250.0
_HEALTH_CACHE_S = 5.0
CLOCK_HEALTH_ENV = "OSIRIS_CLOCK_HEALTH_JSON"
CALLER_CLOCK_FIELDS = frozenset({
    "now", "received_at", "received_time", "server_receive_time", "receive_time",
})
LOCAL_STATUS_COMMANDS = (
    ("chrony", ("chronyc", "-n", "tracking")),
    ("timedatectl", ("timedatectl", "show")),
    ("timedatectl", ("timedatectl", "show-timesync")),
    ("ntp", ("ntpq", "-c", "rv", "127.0.0.1")),
)
_ALLOWED_STATUS_COMMANDS = frozenset(command for _source, command in LOCAL_STATUS_COMMANDS)

_runtime: TrustedClock | None = None
_health_override: dict | None = None
_health_cache: dict | None = None
_health_cache_until = 0.0


def _finite(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
    )


class TrustedClock:
    """Wall clock for server receive stamps. Tests pass a callable."""

    def __init__(
        self,
        now=None,
        *,
        source_id: str = "osiris.host.utc",
        offset_ms: float | None = None,
        uncertainty_ms: float | None = None,
    ):
        if not isinstance(source_id, str) or not source_id.strip() or len(source_id) > 64:
            raise ValueError("invalid_clock_source")
        if offset_ms is not None and not _finite(offset_ms):
            raise ValueError("invalid_clock_offset")
        if uncertainty_ms is not None and (not _finite(uncertainty_ms) or uncertainty_ms < 0):
            raise ValueError("invalid_clock_uncertainty")
        self.source_id = source_id
        self.offset_ms = None if offset_ms is None else float(offset_ms)
        self.uncertainty_ms = None if uncertainty_ms is None else float(uncertainty_ms)
        self._now = now

    def now_utc(self) -> datetime:
        current = datetime.now(timezone.utc) if self._now is None else self._now()
        if _finite(current):
            current = datetime.fromtimestamp(float(current), timezone.utc)
        if not isinstance(current, datetime) or current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("invalid_clock")
        return current.astimezone(timezone.utc)


def runtime_clock() -> TrustedClock:
    return _runtime or TrustedClock()


def install_runtime_clock(clock: TrustedClock | None) -> None:
    global _runtime
    if clock is not None and not isinstance(clock, TrustedClock):
        raise TypeError("trusted_clock_required")
    _runtime = clock


def server_receive_time() -> datetime:
    return runtime_clock().now_utc()


def _aware(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field}_requires_timezone")
    return value.astimezone(timezone.utc)


def _tolerance(value: object) -> float:
    if not _finite(value) or float(value) < 0:
        raise ValueError("invalid_timestamp_tolerance")
    return float(value)


def assess_timestamp_provenance(
    *,
    event_time: datetime,
    receive_time: datetime,
    last_event_time: datetime | None = None,
    last_receive_time: datetime | None = None,
    future_tolerance_s: float = FUTURE_TOLERANCE_S,
    regression_tolerance_s: float = REGRESSION_TOLERANCE_S,
    max_latency_s: float = MAX_EVENT_RECEIVE_LATENCY_S,
    clock_source_id: str = "osiris.host.utc",
    clock_offset_ms: float | None = None,
    clock_uncertainty_ms: float | None = None,
) -> dict:
    """Compare a source event time with the server receive stamp.

    ``timestamp_untrusted`` means the event is in the future beyond
    ``future_tolerance_s``, older than the last accepted event beyond
    ``regression_tolerance_s``, or farther from receive time than
    ``max_latency_s``. A caller-supplied ``now`` is not an input.
    """
    event = _aware(event_time, "event_time")
    receive = _aware(receive_time, "receive_time")
    last = None if last_event_time is None else _aware(last_event_time, "last_event_time")
    last_receive = (
        None if last_receive_time is None else _aware(last_receive_time, "last_receive_time")
    )
    future = _tolerance(future_tolerance_s)
    regression = _tolerance(regression_tolerance_s)
    limit = _tolerance(max_latency_s)
    if not isinstance(clock_source_id, str) or not clock_source_id.strip():
        raise ValueError("invalid_clock_source")
    if clock_offset_ms is not None and not _finite(clock_offset_ms):
        raise ValueError("invalid_clock_offset")
    if clock_uncertainty_ms is not None and (
        not _finite(clock_uncertainty_ms) or clock_uncertainty_ms < 0
    ):
        raise ValueError("invalid_clock_uncertainty")
    latency = (receive - event).total_seconds()
    causes: list[str] = []
    if latency < -future:
        causes.append("future_event")
    if latency > limit:
        causes.append("implausible_latency")
    if last is not None and (last - event).total_seconds() > regression:
        # A test clock that moves with the event is a historical injection.
        # A receive stamp that stays put while the event jumps backward is not.
        clock_jumped_back = (
            last_receive is not None
            and (last_receive - receive).total_seconds() > regression
        )
        if not clock_jumped_back:
            causes.append("event_regression")
    trusted = not causes
    return {
        "source_event_time": event.isoformat(),
        "server_receive_time": receive.isoformat(),
        "clock_source_id": clock_source_id,
        "clock_offset_ms": None if clock_offset_ms is None else float(clock_offset_ms),
        "clock_uncertainty_ms": (
            None if clock_uncertainty_ms is None else float(clock_uncertainty_ms)
        ),
        "trusted": trusted,
        "flag": None if trusted else "timestamp_untrusted",
        "causes": causes,
        "live_trading_authorized": False,
    }


def stamp_identity(*, injected: bool, depth: bool = False) -> dict:
    """Identity of the clock that produced the server receive time."""
    if depth:
        return {
            "clock_source_id": "osiris.depth",
            "clock_offset_ms": None,
            "clock_uncertainty_ms": None,
        }
    if injected:
        return {
            "clock_source_id": "osiris.injected",
            "clock_offset_ms": None,
            "clock_uncertainty_ms": None,
        }
    clock = runtime_clock()
    source = clock.source_id
    offset = clock.offset_ms
    uncertainty = clock.uncertainty_ms
    if source == "osiris.host.utc":
        health = current_clock_health()
        if offset is None and _finite(health.get("offset_ms")):
            offset = float(health["offset_ms"])
        if uncertainty is None and _finite(health.get("uncertainty_ms")):
            uncertainty = float(health["uncertainty_ms"])
        reported = health.get("clock_source_id")
        if isinstance(reported, str) and reported not in {"", "unknown", "osiris.host.utc"}:
            source = reported
    return {
        "clock_source_id": source,
        "clock_offset_ms": offset,
        "clock_uncertainty_ms": uncertainty,
    }


def _kv(text: str) -> dict[str, str]:
    values = {}
    for line in text.splitlines():
        if "=" in line and ":" not in line.split("=", 1)[0]:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
        elif ":" in line:
            key, value = line.split(":", 1)
            values[key.strip()] = value.strip()
    return values


def _seconds(value: str) -> float | None:
    try:
        number = float(value.split()[0])
    except (ValueError, IndexError):
        return None
    return number if math.isfinite(number) else None


def _parse_chrony(text: str) -> dict | None:
    values = _kv(text)
    if "System time" not in values or "Leap status" not in values:
        return None
    system = values["System time"]
    magnitude = _seconds(system)
    if magnitude is None:
        return None
    direction = system.split()
    if len(direction) < 3 or direction[2] not in {"fast", "slow"}:
        return None
    offset_s = magnitude if direction[2] == "fast" else -magnitude
    uncertainty = _seconds(values["RMS offset"]) if "RMS offset" in values else None
    leap = values["Leap status"]
    return {
        "synchronized": leap in {"Normal", "Insert second", "Delete second"},
        "offset_ms": offset_s * 1000,
        "uncertainty_ms": None if uncertainty is None else uncertainty * 1000,
    }


def _parse_timedatectl_show(text: str) -> dict | None:
    values = _kv(text)
    if "NTPSynchronized" not in values:
        return None
    return {"synchronized": values["NTPSynchronized"].lower() == "yes"}


def _usec_to_ms(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    if not math.isfinite(number):
        return None
    return number / 1000.0


def _parse_timesync(text: str) -> dict | None:
    values = _kv(text)
    offset = _usec_to_ms(values.get("OffsetUSec"))
    uncertainty = _usec_to_ms(values.get("RootDistanceUSec"))
    if offset is None and uncertainty is None and "NTPSynchronized" not in values:
        return None
    parsed: dict = {}
    if offset is not None:
        parsed["offset_ms"] = offset
    if uncertainty is not None:
        parsed["uncertainty_ms"] = uncertainty
    if "NTPSynchronized" in values:
        parsed["synchronized"] = values["NTPSynchronized"].lower() == "yes"
    return parsed


def _parse_ntp_rv(text: str) -> dict | None:
    if "offset=" not in text:
        return None
    fields: dict[str, str] = {}
    for token in text.replace(",", " ").replace("\n", " ").split():
        if "=" in token:
            key, value = token.split("=", 1)
            fields[key] = value
    try:
        offset = float(fields["offset"])
    except (KeyError, ValueError):
        return None
    if not math.isfinite(offset):
        return None
    uncertainty = None
    if "rootdisp" in fields:
        try:
            uncertainty = float(fields["rootdisp"])
        except ValueError:
            uncertainty = None
        if uncertainty is not None and not math.isfinite(uncertainty):
            uncertainty = None
    synchronized = "sync_ntp" in text or "sync_pps" in text
    return {
        "synchronized": synchronized,
        "offset_ms": offset,
        "uncertainty_ms": uncertainty,
    }


def _parse_status(source: str, text: str) -> dict | None:
    if source == "chrony":
        return _parse_chrony(text)
    if source == "timedatectl-show":
        return _parse_timedatectl_show(text)
    if source == "timedatectl-timesync":
        return _parse_timesync(text)
    if source == "ntp":
        return _parse_ntp_rv(text)
    return None


def _run_local(argv: list[str]) -> tuple[int, str]:
    """Run one allowlisted local status command. Never contacts a time server."""
    if tuple(argv) not in _ALLOWED_STATUS_COMMANDS:
        return 127, ""
    try:
        completed = subprocess.run(
            argv, check=False, capture_output=True, text=True, timeout=1.0,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 127, ""
    return completed.returncode, completed.stdout or ""


def _invoke(runner, argv: tuple[str, ...]) -> dict | None:
    try:
        code, output = runner(list(argv))
    except (TypeError, ValueError):
        return None
    if type(code) is not int or not isinstance(output, str) or code != 0:
        return None
    source = {
        ("chronyc", "-n", "tracking"): "chrony",
        ("timedatectl", "show"): "timedatectl-show",
        ("timedatectl", "show-timesync"): "timedatectl-timesync",
        ("ntpq", "-c", "rv", "127.0.0.1"): "ntp",
    }.get(argv)
    return None if source is None else _parse_status(source, output)


def _health(source: str, reading: dict, max_offset_ms: float) -> dict:
    offset = reading.get("offset_ms")
    uncertainty = reading.get("uncertainty_ms")
    body = {
        "clock_source_id": source,
        "offset_ms": None if not _finite(offset) else float(offset),
        "uncertainty_ms": None if not _finite(uncertainty) else float(uncertainty),
        "live_trading_authorized": False,
        "network_probe": False,
        "max_offset_ms": max_offset_ms,
    }
    if reading.get("synchronized") is not True:
        return {**body, "status": "degraded", "reason": "sync_unknown", "orders_fail_closed": True}
    if body["offset_ms"] is None:
        return {**body, "status": "degraded", "reason": "offset_unknown", "orders_fail_closed": True}
    if abs(body["offset_ms"]) > max_offset_ms:
        return {**body, "status": "degraded", "reason": "offset_exceeded", "orders_fail_closed": True}
    if body["uncertainty_ms"] is not None and (
        body["uncertainty_ms"] < 0 or body["uncertainty_ms"] > max_offset_ms
    ):
        return {
            **body, "status": "degraded", "reason": "uncertainty_exceeded",
            "orders_fail_closed": True,
        }
    return {**body, "status": "healthy", "reason": "synchronized", "orders_fail_closed": False}


def _unknown(max_offset_ms: float) -> dict:
    return {
        "status": "degraded", "reason": "sync_unknown", "clock_source_id": "unknown",
        "offset_ms": None, "uncertainty_ms": None, "orders_fail_closed": True,
        "live_trading_authorized": False, "network_probe": False,
        "max_offset_ms": max_offset_ms,
    }


def read_host_clock_health(
    *, runner=None, max_offset_ms: float = MAX_CLOCK_OFFSET_MS,
) -> dict:
    """Read chrony, timedatectl, or local ntpd status. No network and no ``probe``."""
    if not _finite(max_offset_ms) or max_offset_ms < 0:
        raise ValueError("invalid_clock_offset_threshold")
    limit = float(max_offset_ms)
    run = runner or _run_local
    chrony = _invoke(run, ("chronyc", "-n", "tracking"))
    if chrony is not None:
        return _health("chrony", chrony, limit)
    show = _invoke(run, ("timedatectl", "show"))
    sync = _invoke(run, ("timedatectl", "show-timesync"))
    if show is not None or sync is not None:
        merged = {**(show or {}), **(sync or {})}
        if "synchronized" not in merged:
            merged["synchronized"] = False
        return _health("timedatectl", merged, limit)
    ntp = _invoke(run, ("ntpq", "-c", "rv", "127.0.0.1"))
    if ntp is not None:
        return _health("ntp", ntp, limit)
    return _unknown(limit)


def install_clock_health(result: dict | None) -> None:
    global _health_override
    if result is not None and not isinstance(result, dict):
        raise TypeError("clock_health_required")
    _health_override = None if result is None else dict(result)


def _health_from_env() -> dict | None:
    raw = os.environ.get(CLOCK_HEALTH_ENV, "")
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    parsed["live_trading_authorized"] = False
    parsed["network_probe"] = False
    return parsed


def current_clock_health(*, max_offset_ms: float = MAX_CLOCK_OFFSET_MS) -> dict:
    """Injected health in tests; otherwise a short-lived local status reading."""
    global _health_cache, _health_cache_until
    if _health_override is not None:
        body = dict(_health_override)
        body["live_trading_authorized"] = False
        body["network_probe"] = False
        return body
    inherited = _health_from_env()
    if inherited is not None:
        return inherited
    now = time.monotonic()
    if (
        _health_cache is not None
        and now < _health_cache_until
        and _health_cache.get("max_offset_ms") == float(max_offset_ms)
    ):
        return dict(_health_cache)
    reading = read_host_clock_health(max_offset_ms=max_offset_ms)
    _health_cache = reading
    _health_cache_until = now + _HEALTH_CACHE_S
    return dict(reading)


def reset_clock_state() -> None:
    """Drop the injected clock, health override, and status cache."""
    global _runtime, _health_override, _health_cache, _health_cache_until
    _runtime = None
    _health_override = None
    _health_cache = None
    _health_cache_until = 0.0
