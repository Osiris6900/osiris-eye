"""Read-only, explicitly opt-in loopback P2P health for the Admin Centre.

Only a numeric local port can be configured. Never fetch arbitrary URLs,
follow redirects, expose the node PeerId, or treat this as a network-wide count.
This is an unauthenticated local probe, not an attested production monitor.
"""
from __future__ import annotations

from http.client import HTTPConnection, HTTPException
import json
import os
import re
from typing import Any, Mapping

PORT_ENV = "OSIRIS_P2P_LOCAL_HEALTH_PORT"
MAX_BODY_BYTES = 4096
MAX_COUNT = 1_000_000
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+-]{0,40}$")


def software_version(value: Any) -> str | None:
    """Accept a short software version. Drop everything else, including PeerId."""
    if isinstance(value, str) and _VERSION_RE.fullmatch(value):
        return value
    return None


def _unavailable(reason: str) -> dict[str, Any]:
    return {"status": "unavailable", "reason": reason, "source": "loopback_local_node"}


def _count(value: Any) -> int | None:
    return value if type(value) is int and 0 <= value <= MAX_COUNT else None


def read_local_node_health(environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Only connect to a fixed loopback IP, with bounded time/bytes and strict fields."""
    env = os.environ if environ is None else environ
    raw_port = env.get(PORT_ENV)
    if raw_port is None:
        return _unavailable("not_configured")
    if (
        not isinstance(raw_port, str)
        or re.fullmatch(r"[1-9][0-9]{0,4}", raw_port) is None
        or int(raw_port) > 65535
    ):
        return _unavailable("invalid_port")
    conn = HTTPConnection("127.0.0.1", int(raw_port), timeout=0.3)
    try:
        conn.request("GET", "/health")
        response = conn.getresponse()
        if response.status != 200:
            return _unavailable("health_http_error")
        raw = response.read(MAX_BODY_BYTES + 1)
        if len(raw) > MAX_BODY_BYTES:
            return _unavailable("health_payload_too_large")
        value = json.loads(raw)
    except (OSError, HTTPException, TimeoutError, UnicodeError, ValueError):
        return _unavailable("health_unreachable_or_invalid")
    finally:
        conn.close()
    if not isinstance(value, dict) or value.get("status") != "alive":
        return _unavailable("health_unreachable_or_invalid")

    numbers = {
        name: _count(value.get(name))
        for name in (
            "uptime_seconds", "connected_peers", "bootstrap_peers_connected",
            "bootstrap_peers_total", "relays_total", "relay_reservations",
        )
    }
    if any(number is None for number in numbers.values()):
        return _unavailable("health_unreachable_or_invalid")
    ready = value.get("ready")
    relay_configured = value.get("relay_configured")
    relay_reservation_active = value.get("relay_reservation_active")
    if any(type(flag) is not bool for flag in (ready, relay_configured, relay_reservation_active)):
        return _unavailable("health_unreachable_or_invalid")
    if (
        numbers["bootstrap_peers_connected"] > numbers["bootstrap_peers_total"]
        or numbers["relay_reservations"] > numbers["relays_total"]
        or numbers["bootstrap_peers_connected"] > numbers["connected_peers"]
    ):
        return _unavailable("health_unreachable_or_invalid")
    # PeerId and every unrecognized field are deliberately omitted.
    # Version is optional: a missing or unsafe value stays null and does not
    # fail the rest of the probe.
    return {
        "status": "observed",
        "source": "loopback_local_node",
        "ready": ready,
        "relay_configured": relay_configured,
        "relay_reservation_active": relay_reservation_active,
        **numbers,
        "version": software_version(value.get("version")),
        "scope": "one_local_node_not_network_total",
        "authenticated_or_attested": False,
    }
