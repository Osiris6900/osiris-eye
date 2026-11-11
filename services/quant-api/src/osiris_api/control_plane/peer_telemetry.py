"""Collect the core Rust node's bounded /peer-metrics endpoint on numeric loopback."""

import ipaddress
import json
import time
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, build_opener

from .database import Database
from .vendors import NoRedirect


def collect(db: Database, url: str):
    parsed = urlsplit(url)
    try:
        valid_host = parsed.hostname and ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        valid_host = False
    if (
        parsed.scheme != "http"
        or not valid_host
        or parsed.port is None
        or parsed.path != "/peer-metrics"
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        raise ValueError("numeric_loopback_peer_metrics_required")
    try:
        with build_opener(ProxyHandler({}), NoRedirect()).open(url, timeout=3) as response:
            raw = response.read(262145)
        if len(raw) > 262144:
            raise ValueError("peer_metrics_response_too_large")
        report = json.loads(raw)
    except (OSError, UnicodeError, ValueError):
        raise ValueError("local_peer_metrics_unavailable") from None
    if (
        not isinstance(report, dict)
        or report.get("schema") != "osiris.peer-metrics.v1"
        or report.get("scope") != "local-node-process"
        or report.get("latency_kind") != "verified_receipt_admission_ack"
        or not isinstance(report.get("peers"), list)
        or len(report["peers"]) > 256
    ):
        raise ValueError("invalid_peer_metrics_contract")
    seen = set()
    for peer in report["peers"]:
        required = {
            "peer_id",
            "connected",
            "connections",
            "disconnects",
            "published_messages",
            "cryptographically_verified_receipts",
            "unconfirmed_timeouts",
            "latency_history",
        }
        if (
            not isinstance(peer, dict)
            or set(peer) != required
            or type(peer["connected"]) is not bool
        ):
            raise ValueError("invalid_peer_metrics_shape")
        pid = peer["peer_id"]
        if not isinstance(pid, str) or not pid or len(pid) > 128 or pid in seen:
            raise ValueError("invalid_peer_metrics_identity")
        seen.add(pid)
        for key in required - {"peer_id", "connected", "latency_history"}:
            if type(peer[key]) is not int or not 0 <= peer[key] <= 2**64 - 1:
                raise ValueError("invalid_peer_metrics_counter")
        history = peer["latency_history"]
        if not isinstance(history, list) or len(history) > 60:
            raise ValueError("invalid_peer_latency_history")
        previous = -1
        for sample in history:
            if (
                not isinstance(sample, dict)
                or set(sample) != {"elapsed_ms", "receipt_latency_ms"}
                or any(type(value) is not int or value < 0 for value in sample.values())
                or sample["elapsed_ms"] < previous
            ):
                raise ValueError("invalid_peer_latency_sample")
            previous = sample["elapsed_ms"]
    item = db.get("peer-telemetry", "latest")
    value = {
        "captured_ms": int(time.time() * 1000),
        "report": report,
        "independent_host_provenance_verified": False,
    }
    db.put("peer-telemetry", "latest", value, expected_version=item["version"] if item else 0)
    return value
