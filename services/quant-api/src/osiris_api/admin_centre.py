"""Authenticated read-only admin control centre (issue #180).

This module is the canonical fail-closed RBAC contract for the operator
dashboard. ``scripts/check_operator_permissions.py`` imports the same tables
from here so the offline contract and the live API can never drift apart.

Design rules:
- Authorization is server-side only; a frontend check is not authorization.
- DANGEROUS actions (live orders, key export, risk-gate disable, audit
  erasure) are denied for every role, including security_admin.
- Admin bearer tokens come from the environment and are never returned in
  any response. ``scan_payload`` is a defense-in-depth check run on every
  admin response before it leaves the API.
- Everything here is read-only research/paper state plus two bounded
  operational controls (ingestion kill switch, feed-adapter revocation).
  No live broker connectivity exists.
"""
from __future__ import annotations

import hmac
import json
import math
import os
import re
import sqlite3
from collections import deque
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import quote

from . import authorized_feeds
from .p2p_local_health import read_local_node_health, software_version
from .admin_alert_store import (
    ALERT_DB_ENV, AlertStoreError, acknowledge_stored_alert,
    record_raised_alerts, resolve_alert_db_path, unavailable_alert_history,
)
from .durable_paper_ledger import snapshot as paper_ledger_snapshot
from .p2p_ingestion import ENABLE_ENV

# ---------------------------------------------------------------------------
# RBAC contract (canonical; scripts/check_operator_permissions.py re-exports)

PERMISSIONS: dict[str, frozenset[str]] = {
    "viewer": frozenset({"read_health", "read_audit"}),
    "analyst": frozenset({"read_health", "read_audit", "read_research"}),
    "operator": frozenset({"read_health", "read_audit", "read_research", "ack_alert"}),
    "risk_officer": frozenset(
        {"read_health", "read_audit", "read_research", "ack_alert", "activate_kill_switch"}
    ),
    "security_admin": frozenset(
        {"read_health", "read_audit", "read_research", "ack_alert", "revoke_peer"}
    ),
}
DANGEROUS: frozenset[str] = frozenset(
    {"place_live_order", "export_private_key", "disable_risk_gate", "erase_audit"}
)


def authorize(role: Any, action: Any, authenticated: bool = False) -> bool:
    """Fail closed: unauthenticated, unknown roles and DANGEROUS actions deny."""
    return bool(
        authenticated is True
        and role in PERMISSIONS
        and action not in DANGEROUS
        and action in PERMISSIONS[role]
    )


# ---------------------------------------------------------------------------
# Per-role bearer tokens from the environment

TOKENS_ENV = "OSIRIS_ADMIN_TOKENS"
_MIN_TOKEN_LENGTH = 8


class AdminAuth:
    """Resolve a bearer token to an admin role.

    Configuration is a JSON object in ``OSIRIS_ADMIN_TOKENS`` mapping role
    names to token strings, e.g. ``{"viewer": "...", "operator": "..."}``.
    Malformed configuration, unknown roles, short tokens or a token shared
    between roles all fail closed (``configured`` becomes False and every
    admin endpoint answers 503)."""

    def __init__(self, tokens: Mapping[str, str], valid: bool) -> None:
        self._tokens = dict(tokens)
        self._valid = valid

    @classmethod
    def from_environment(cls, env: Mapping[str, str] | None = None) -> "AdminAuth":
        env = os.environ if env is None else env
        raw = env.get(TOKENS_ENV, "")
        if not raw.strip():
            return cls({}, False)
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return cls({}, False)
        if not isinstance(parsed, dict) or not parsed:
            return cls({}, False)
        tokens: dict[str, str] = {}
        seen: set[str] = set()
        for role, token in parsed.items():
            if role not in PERMISSIONS or not isinstance(token, str):
                return cls({}, False)
            if len(token) < _MIN_TOKEN_LENGTH or token in seen:
                return cls({}, False)
            seen.add(token)
            tokens[role] = token
        return cls(tokens, True)

    @property
    def configured(self) -> bool:
        return self._valid and bool(self._tokens)

    @property
    def roles(self) -> tuple[str, ...]:
        return tuple(sorted(self._tokens))

    def secrets(self) -> tuple[str, ...]:
        """Configured token values; used only for redaction scans, never returned."""
        return tuple(self._tokens.values())

    def resolve(self, authorization_header: Any) -> str | None:
        if not self.configured or not isinstance(authorization_header, str):
            return None
        if not authorization_header.startswith("Bearer "):
            return None
        supplied = authorization_header[len("Bearer "):].encode("utf-8", errors="ignore")
        for role, token in self._tokens.items():
            if hmac.compare_digest(supplied, token.encode("utf-8")):
                return role
        return None


# ---------------------------------------------------------------------------
# Append-only in-memory audit log

AUDIT_SCHEMA = "osiris.admin_audit_entry.v1"


class AuditLog:
    """Bounded in-memory record of every admin decision, including denials.

    Entries are never erased or mutated through the API (``erase_audit`` is a
    DANGEROUS action). The log is process-local evidence, not a durable
    compliance store."""

    def __init__(self, maxlen: int = 1000) -> None:
        self._entries: deque[dict[str, Any]] = deque(maxlen=maxlen)

    def record(
        self,
        role: str,
        action: str,
        decision: str,
        detail: str = "",
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        if decision not in {"allow", "deny"}:
            raise ValueError("audit_decision_invalid")
        stamp = now or datetime.now(timezone.utc)
        entry = {
            "schema": AUDIT_SCHEMA,
            "recorded_at": stamp.astimezone(timezone.utc).isoformat(),
            "role": str(role),
            "action": str(action),
            "decision": decision,
            "detail": str(detail)[:200],
        }
        self._entries.append(entry)
        return entry

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("audit_limit_invalid")
        return list(self._entries)[-limit:]

    def clear(self) -> None:
        self._entries.clear()


# ---------------------------------------------------------------------------
# Secrets hygiene: defense-in-depth scan applied to every admin response

SECRET_KEY_MARKERS = (
    "token",
    "secret",
    "password",
    "private_key",
    "public_key",
    "authorization",
    "credential",
    "api_key",
    "apikey",
    "session",
)


def scan_payload(
    payload: Any,
    secret_values: Iterable[str] = (),
    *,
    _path: str = "$",
) -> list[str]:
    """Return JSON-ish paths where a payload leaks credential-shaped data.

    A violation is either a key containing a secret marker (any depth) or a
    string value containing a configured secret. An empty result means the
    payload is safe to return."""
    secrets = [s for s in secret_values if isinstance(s, str) and len(s) >= _MIN_TOKEN_LENGTH]
    violations: list[str] = []
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            key_text = str(key)
            lowered = key_text.lower()
            child = f"{_path}.{key_text}"
            if any(marker in lowered for marker in SECRET_KEY_MARKERS):
                violations.append(child)
            violations.extend(scan_payload(value, secrets, _path=child))
    elif isinstance(payload, (list, tuple)):
        for index, value in enumerate(payload):
            violations.extend(scan_payload(value, secrets, _path=f"{_path}[{index}]"))
    elif isinstance(payload, str):
        if any(secret in payload for secret in secrets):
            violations.append(_path)
    return violations


# ---------------------------------------------------------------------------
# Operational alerts (in-memory, idempotent by kind+key)

ALERT_SCHEMA = "osiris.admin_alert.v1"


class AlertsRegistry:
    def __init__(self) -> None:
        self._alerts: dict[str, dict[str, Any]] = {}

    def raise_alert(
        self,
        kind: str,
        key: str,
        severity: str,
        message: str,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        if severity not in {"info", "warning", "critical"}:
            raise ValueError("alert_severity_invalid")
        alert_id = f"{kind}:{key}"
        existing = self._alerts.get(alert_id)
        if existing is not None and existing["status"] == "active":
            return existing
        stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
        alert = {
            "schema": ALERT_SCHEMA,
            "alert_id": alert_id,
            "kind": str(kind),
            "severity": severity,
            "message": str(message)[:300],
            "status": "active",
            "raised_at": stamp,
            "acknowledged_at": None,
            "acknowledged_by": None,
        }
        self._alerts[alert_id] = alert
        return alert

    def ack(self, alert_id: str, role: str, *, now: datetime | None = None) -> dict[str, Any] | None:
        alert = self._alerts.get(str(alert_id))
        if alert is None or alert["status"] != "active":
            return None
        alert["status"] = "acknowledged"
        alert["acknowledged_at"] = (now or datetime.now(timezone.utc)).astimezone(
            timezone.utc
        ).isoformat()
        alert["acknowledged_by"] = str(role)
        return alert

    def active(self) -> list[dict[str, Any]]:
        return [a for a in self._alerts.values() if a["status"] == "active"]

    def all(self) -> list[dict[str, Any]]:
        return list(self._alerts.values())

    def clear(self) -> None:
        self._alerts.clear()


# ---------------------------------------------------------------------------
# Read-only snapshots

NETWORK_SCHEMA = "osiris.admin_network_status.v1"
FEEDS_SCHEMA = "osiris.admin_feeds_status.v1"
PAPER_SCHEMA = "osiris.admin_paper_status.v1"
RISK_SCHEMA = "osiris.admin_risk_status.v1"
OVERVIEW_SCHEMA = "osiris.admin_overview.v1"
MONITORING_SCHEMA = "osiris.admin_monitoring.v1"
OPERATIONS_SCHEMA = "osiris.admin_operations.v1"
PAPER_TRADING_SCHEMA = "osiris.admin_paper_trading.v1"

QUALITY_REVIEW_MAX_AGE_SECONDS = 30 * 24 * 60 * 60.0  # mirrors feed_admission
OUTBOX_BACKLOG_ALERT_THRESHOLD = 100
OUTBOX_PATH_ENV = "OSIRIS_OBSERVATION_OUTBOX"
IDENTITY_DB_ENV = "OSIRIS_P2P_DB_PATH"
# Same default as P2PIdentityRegistry. A missing file is never created here.
DEFAULT_IDENTITY_DB_PATH = "./data/osiris-p2p.sqlite3"
PEER_NODE_LIST_LIMIT = 50
OPEN_POSITION_LIMIT = 50
PAPER_ORDER_LIMIT = 20
_ORDER_STATES = frozenset({"accepted", "partially_filled", "filled", "cancelled", "rejected"})
_OPEN_ORDER_STATES = frozenset({"accepted", "partially_filled"})
_ORDER_SIDES = frozenset({"BUY", "SELL"})
_ORDER_ID_TEXT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_BOOK_FLAGS = {
    "ingestion_disabled": ("warning", "P2P ingestion kill switch is disabled"),
    "paper_ledger_uninitialized": (
        "info", "Paper ledger is not initialized; cash, positions and P&L unavailable",
    ),
    "paper_reconciliation_failed": (
        "critical", "Paper account reconciliation failed; accounting unavailable",
    ),
    "paper_execution_halted": ("critical", "Paper execution kill switch is active"),
}
_LIMIT_FIELDS = (
    "max_order_notional", "max_position_notional", "max_daily_loss", "max_gross_exposure",
)
_NODE_ID_RE = re.compile(r"^[0-9a-f]{64}$")
_ISO_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?\+00:00$")
_TRUST_LEVELS = frozenset({"trusted", "public_untrusted"})
_PEER_REASONS = frozenset({
    "not_configured",
    "registry_file_absent",
    "registry_schema_absent",
    "registry_unreadable",
})
_NODE_REASONS = frozenset({
    "not_configured",
    "invalid_port",
    "health_http_error",
    "health_payload_too_large",
    "health_unreachable_or_invalid",
})
_NODE_COUNTS = (
    "uptime_seconds", "connected_peers", "bootstrap_peers_connected",
    "bootstrap_peers_total", "relays_total", "relay_reservations",
)
_NODE_FLAGS = ("ready", "relay_configured", "relay_reservation_active")
_PAPER_SOURCES = frozenset({"legacy_synthetic", "canonical_execution"})
_PAPER_MODES = frozenset({"synthetic-paper-only", "paper_only"})
_OPEN_ORDER_REASONS = frozenset({"not_stored", "account_unavailable"})
_DECIMAL_TEXT = re.compile(r"^-?\d+(\.\d+)?$")
_SYMBOL_TEXT = re.compile(r"^[A-Z0-9][A-Z0-9._-]{0,31}$")
_MAX_NODE_COUNT = 1_000_000
_UNIX_MIN = 946684800.0  # 2000-01-01 UTC
_UNIX_MAX = 4102444800.0  # 2100-01-01 UTC
_REQUIRED_NODE_COLUMNS = frozenset({
    "node_id", "trust_level", "enrolled_at", "last_seen_at",
})


def _utcnow(now: datetime | None = None) -> datetime:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("admin_now_missing_timezone")
    return current.astimezone(timezone.utc)


def ingestion_enabled(env: Mapping[str, str] | None = None) -> bool:
    env = os.environ if env is None else env
    return env.get(ENABLE_ENV) == "1"


def set_ingestion_enabled(enabled: bool, env: Mapping[str, str] | None = None) -> bool:
    """Flip the P2P ingestion kill switch for this process.

    The switch is the same ``OSIRIS_P2P_INGEST_ENABLED`` gate consulted by
    ``p2p_ingestion.validate_p2p_observation`` and ``ObservationOutbox.drain``,
    so the change has real effect on every in-process ingestion path."""
    if not isinstance(enabled, bool):
        raise ValueError("kill_switch_state_invalid")
    os.environ[ENABLE_ENV] = "1" if enabled else "0"
    return enabled


def resolve_identity_db_path(env: Mapping[str, str] | None = None) -> str | None:
    """Path of the identity registry. Missing env uses the registry default."""
    source = os.environ if env is None else env
    if IDENTITY_DB_ENV not in source:
        return DEFAULT_IDENTITY_DB_PATH
    value = source.get(IDENTITY_DB_ENV)
    return value if isinstance(value, str) else None


def _unavailable_peer_health(reason: str) -> dict[str, Any]:
    if reason not in _PEER_REASONS:
        reason = "registry_unreadable"
    return {
        "status": "unavailable",
        "reason": reason,
        "source": "p2p_identity_registry",
        "observation": "identity_enrollment_only",
        "liveness": "not_observed",
        "last_enrollment_meaning": "enrollment_or_reenrollment_not_liveness",
        "peer_count": None,
        "trusted_count": None,
        "public_untrusted_count": None,
        "unclassified_count": None,
        "omitted_count": None,
        "nodes": None,
        "nodes_truncated": False,
    }


def _iso_or_none(value: Any) -> str | None:
    if isinstance(value, str) and _ISO_UTC_RE.fullmatch(value):
        return value
    return None


def _count_or_none(raw: Mapping[str, Any], name: str) -> int | None:
    value = raw.get(name)
    if type(value) is int and value >= 0:
        return value
    return None


def _peer_health_view(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    """Copy a registry read into a fixed shape. Unknown text is dropped."""
    if not isinstance(raw, Mapping) or raw.get("status") != "enrolled_registry":
        reason = raw.get("reason") if isinstance(raw, Mapping) else "not_configured"
        return _unavailable_peer_health(reason if isinstance(reason, str) else "not_configured")
    nodes: list[dict[str, Any]] = []
    for item in raw.get("nodes") or []:
        if not isinstance(item, Mapping):
            continue
        node_id = item.get("node_id")
        trust = item.get("trust_level")
        if not isinstance(node_id, str) or _NODE_ID_RE.fullmatch(node_id) is None:
            continue
        if trust not in _TRUST_LEVELS and trust != "unclassified":
            trust = "unclassified"
        nodes.append({
            "node_id": node_id,
            "trust_level": trust,
            "enrolled_at": _iso_or_none(item.get("enrolled_at")),
            "last_enrollment_observed_at": _iso_or_none(
                item.get("last_enrollment_observed_at")
            ),
        })
    peer_count = _count_or_none(raw, "peer_count")
    trusted = _count_or_none(raw, "trusted_count")
    public = _count_or_none(raw, "public_untrusted_count")
    unclassified = _count_or_none(raw, "unclassified_count")
    omitted = _count_or_none(raw, "omitted_count")
    if (
        peer_count is None or trusted is None or public is None
        or unclassified is None or omitted is None
    ):
        return _unavailable_peer_health("registry_unreadable")
    if trusted + public + unclassified != peer_count:
        return _unavailable_peer_health("registry_unreadable")
    shown = nodes[:PEER_NODE_LIST_LIMIT]
    truncated = peer_count > PEER_NODE_LIST_LIMIT
    if truncated and len(shown) != PEER_NODE_LIST_LIMIT:
        return _unavailable_peer_health("registry_unreadable")
    if not truncated and len(shown) != peer_count:
        return _unavailable_peer_health("registry_unreadable")
    return {
        "status": "enrolled_registry",
        "reason": None,
        "source": "p2p_identity_registry",
        "observation": "identity_enrollment_only",
        "liveness": "not_observed",
        "last_enrollment_meaning": "enrollment_or_reenrollment_not_liveness",
        "peer_count": peer_count,
        "trusted_count": trusted,
        "public_untrusted_count": public,
        "unclassified_count": unclassified,
        "omitted_count": omitted,
        "nodes": shown,
        "nodes_truncated": truncated,
    }


def _unix_to_iso(value: Any) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(float(value)):
        return None
    number = float(value)
    if number < _UNIX_MIN or number > _UNIX_MAX:
        return None
    return datetime.fromtimestamp(number, timezone.utc).isoformat()


def _peer_health_from_rows(rows: list[Any]) -> dict[str, Any]:
    trusted = public = unclassified = omitted = 0
    nodes: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        node_id = row["node_id"]
        if not isinstance(node_id, str) or _NODE_ID_RE.fullmatch(node_id) is None:
            omitted += 1
            continue
        if node_id in seen:
            return _unavailable_peer_health("registry_unreadable")
        seen.add(node_id)
        trust = row["trust_level"]
        if trust == "trusted":
            trusted += 1
        elif trust == "public_untrusted":
            public += 1
        else:
            unclassified += 1
            trust = "unclassified"
        if len(nodes) < PEER_NODE_LIST_LIMIT:
            nodes.append({
                "node_id": node_id,
                "trust_level": trust,
                "enrolled_at": _unix_to_iso(row["enrolled_at"]),
                "last_enrollment_observed_at": _unix_to_iso(row["last_seen_at"]),
            })
    return _peer_health_view({
        "status": "enrolled_registry",
        "peer_count": trusted + public + unclassified,
        "trusted_count": trusted,
        "public_untrusted_count": public,
        "unclassified_count": unclassified,
        "omitted_count": omitted,
        "nodes": nodes,
        "nodes_truncated": trusted + public + unclassified > PEER_NODE_LIST_LIMIT,
    })


def read_enrolled_identity_registry(path: str | None) -> dict[str, Any]:
    """Read enrolled node ids. Does not create, migrate, or write the file.

    ``last_seen_at`` is the last enrollment or re-enrollment time. It is not a
    liveness probe, and public keys are not selected.
    """
    if not isinstance(path, str) or not path.strip() or path.strip() == ":memory:":
        return _unavailable_peer_health("not_configured")
    candidate = Path(path)
    connection = None
    try:
        if not candidate.is_file():
            return _unavailable_peer_health("registry_file_absent")
        resolved = candidate.resolve(strict=True)
        uri = f"file:{quote(resolved.as_posix())}?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        if connection.execute("PRAGMA query_only").fetchone()[0] != 1:
            return _unavailable_peer_health("registry_unreadable")
        present = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='p2p_nodes'"
        ).fetchone()
        if present is None:
            return _unavailable_peer_health("registry_schema_absent")
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(p2p_nodes)")
        }
        if not _REQUIRED_NODE_COLUMNS <= columns:
            return _unavailable_peer_health("registry_schema_absent")
        rows = connection.execute(
            "SELECT node_id, trust_level, enrolled_at, last_seen_at "
            "FROM p2p_nodes ORDER BY enrolled_at, node_id"
        ).fetchall()
    except (OSError, sqlite3.Error):
        return _unavailable_peer_health("registry_unreadable")
    finally:
        if connection is not None:
            connection.close()
    return _peer_health_from_rows(rows)


def network_status(
    outbox_counts: Mapping[str, Any] | None = None,
    identity_registry: Mapping[str, Any] | None = None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = _utcnow(now)
    counts = dict(outbox_counts) if outbox_counts else {"configured": False}
    peer_health = _peer_health_view(identity_registry)
    local_live_node = read_local_node_health()
    return {
        "schema": NETWORK_SCHEMA,
        "generated_at": current.isoformat(),
        "ingestion_enabled": ingestion_enabled(),
        "kill_switch_env": ENABLE_ENV,
        "outbox": counts,
        "peer_telemetry": (
            "local_node_loopback_health"
            if local_live_node["status"] == "observed"
            else "identity_enrollment_not_live_sessions"
            if peer_health["status"] == "enrolled_registry"
            else "unavailable_in_quant_api_process"
        ),
        "local_live_node": local_live_node,
        "peer_health": peer_health,
        "live_trading_authorized": False,
    }


def _parse_instant(text: Any) -> datetime | None:
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        parsed = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def feeds_status(*, now: datetime | None = None) -> dict[str, Any]:
    current = _utcnow(now)
    adapters: list[dict[str, Any]] = []
    for name in sorted(authorized_feeds.ADAPTER_ENTITLEMENTS):
        record = authorized_feeds.ADAPTER_ENTITLEMENTS[name]
        quality = record.get("quality_review") or {}
        reviewed_at = _parse_instant(quality.get("reviewed_at"))
        age_seconds = (current - reviewed_at).total_seconds() if reviewed_at else None
        fresh = bool(
            age_seconds is not None and 0 <= age_seconds <= QUALITY_REVIEW_MAX_AGE_SECONDS
        )
        adapters.append(
            {
                "adapter": name,
                "entitled": True,
                "suspended": authorized_feeds.is_adapter_suspended(name),
                "instruments": sorted(str(i) for i in record.get("instruments", [])),
                "quality_review": {
                    "status": quality.get("status"),
                    "reviewer": quality.get("reviewer"),
                    "reviewed_at": quality.get("reviewed_at"),
                    "age_seconds": age_seconds,
                    "fresh": fresh,
                },
            }
        )
    return {
        "schema": FEEDS_SCHEMA,
        "generated_at": current.isoformat(),
        "adapter_count": len(adapters),
        "suspended_count": sum(1 for a in adapters if a["suspended"]),
        "stale_review_count": sum(1 for a in adapters if not a["quality_review"]["fresh"]),
        "quality_review_max_age_seconds": QUALITY_REVIEW_MAX_AGE_SECONDS,
        "adapters": adapters,
    }


def _cost_basis_gross(positions: Any) -> str | None:
    """Sum of absolute quantity times average cost. None when a row will not parse."""
    if not isinstance(positions, list):
        return None
    total = Decimal("0")
    for position in positions:
        if not isinstance(position, Mapping):
            return None
        try:
            quantity = Decimal(str(position.get("quantity")))
            average = Decimal(str(position.get("average_cost")))
        except (InvalidOperation, TypeError, ValueError):
            return None
        if not quantity.is_finite() or not average.is_finite():
            return None
        total += abs(quantity * average)
    return str(total)


def paper_status(*, now: datetime | None = None) -> dict[str, Any]:
    current = _utcnow(now)
    # Explicit execution-account configuration is authoritative even when the
    # file is absent or corrupt. Never fall back to a different funded account.
    if os.getenv("OSIRIS_PAPER_EXECUTION_DB", "").strip():
        from .paper_execution import account_valuation, execution_status

        result = {
            "schema": PAPER_SCHEMA, "generated_at": current.isoformat(),
            "source_ledger": "canonical_execution", "mode": "paper_only",
            "initialized": False, "cash": None, "positions": None,
            "fill_count": None, "recent_fills": None, "pnl": None,
            "cost_basis_gross": None, "market_value": None, "open_orders": None,
            "orders": None, "open_orders_reason": "account_unavailable",
            "marks_available": False, "kill_switch_active": None,
            "reconciliation": {"passed": False, "breaks": ["account_unavailable"]},
            "live_trading_authorized": False,
        }
        try:
            status = execution_status(read_only=True)
            account, report = status["account"], status["reconciliation"]
            result["initialized"] = account.get("initialized") is True
            result["reconciliation"] = report
            if report.get("passed") is not True:
                return result
            result.update({
                "cash": account["cash"], "positions": account["positions"],
                "fill_count": account["fill_count"], "recent_fills": account["recent_fills"],
                "cost_basis_gross": _cost_basis_gross(account["positions"]),
                "open_orders": account["open_orders"], "orders": account["orders"],
                "open_orders_reason": None, "kill_switch_active": account["kill_switch_active"],
                **account_valuation(account, now=current),
            })
        except (ValueError, ArithmeticError, sqlite3.Error, OSError):
            result["reconciliation"] = {"passed": False, "breaks": ["account_unavailable"]}
        return result
    ledger = paper_ledger_snapshot()
    initialized = bool(ledger.get("initialized"))
    positions = ledger.get("positions", []) if initialized else []
    recent = ledger.get("recent_fills", []) if initialized else []
    return {
        "schema": PAPER_SCHEMA,
        "generated_at": current.isoformat(),
        "source_ledger": "legacy_synthetic",
        "mode": ledger.get("mode", "synthetic-paper-only"),
        "initialized": initialized,
        "cash": ledger.get("cash") if initialized else None,
        "positions": positions,
        "fill_count": ledger.get("fill_count", 0) if initialized else 0,
        "recent_fills": recent if isinstance(recent, list) else [],
        "pnl": ledger.get("pnl") if initialized else None,
        "cost_basis_gross": _cost_basis_gross(positions) if initialized else None,
        "market_value": ledger.get("market_value") if initialized else None,
        "open_orders": None,
        "open_orders_reason": "not_stored",
        "marks_available": bool(
            initialized and ledger.get("pnl") and ledger["pnl"].get("unrealized") is not None
        ),
        "live_trading_authorized": False,
    }


def _peer_health_summary(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    """Counts only. Node ids stay on the network snapshot, not this panel."""
    view = _peer_health_view(raw)
    return {
        "status": view["status"],
        "reason": view["reason"],
        "observation": view["observation"],
        "liveness": view["liveness"],
        "peer_count": view["peer_count"],
        "trusted_count": view["trusted_count"],
        "public_untrusted_count": view["public_untrusted_count"],
        "unclassified_count": view["unclassified_count"],
        "omitted_count": view["omitted_count"],
        "nodes_truncated": view["nodes_truncated"] is True and view["status"] == "enrolled_registry",
    }


def _node_status(local: Mapping[str, Any] | None, *, ingestion_enabled: bool) -> dict[str, Any]:
    """One local loopback probe. Unknown fields, including PeerId, are dropped."""
    unavailable = {
        "status": "unavailable",
        "reason": "not_configured",
        "source": "loopback_local_node",
        **{name: None for name in _NODE_FLAGS},
        **{name: None for name in _NODE_COUNTS},
        "scope": "one_local_node_not_network_total",
        "authenticated_or_attested": False,
        "ingestion_enabled": ingestion_enabled is True,
    }
    if not isinstance(local, Mapping) or local.get("status") != "observed":
        reason = local.get("reason") if isinstance(local, Mapping) else "not_configured"
        unavailable["reason"] = reason if reason in _NODE_REASONS else "health_unreachable_or_invalid"
        return unavailable
    numbers = {name: local.get(name) for name in _NODE_COUNTS}
    flags = {name: local.get(name) for name in _NODE_FLAGS}
    if any(type(value) is not int or not 0 <= value <= _MAX_NODE_COUNT for value in numbers.values()):
        unavailable["reason"] = "health_unreachable_or_invalid"
        return unavailable
    if any(type(value) is not bool for value in flags.values()):
        unavailable["reason"] = "health_unreachable_or_invalid"
        return unavailable
    if (
        numbers["bootstrap_peers_connected"] > numbers["bootstrap_peers_total"]
        or numbers["relay_reservations"] > numbers["relays_total"]
        or numbers["bootstrap_peers_connected"] > numbers["connected_peers"]
    ):
        unavailable["reason"] = "health_unreachable_or_invalid"
        return unavailable
    return {
        "status": "observed",
        "reason": None,
        "source": "loopback_local_node",
        **flags,
        **numbers,
        "scope": "one_local_node_not_network_total",
        "authenticated_or_attested": False,
        "ingestion_enabled": ingestion_enabled is True,
    }


def _blank_paper(status: str, **overrides: Any) -> dict[str, Any]:
    payload = {
        "status": status,
        "source_ledger": None,
        "mode": None,
        "initialized": None,
        "cash": None,
        "position_count": None,
        "fill_count": None,
        "open_order_count": None,
        "open_orders_reason": None,
        "realized_pnl": None,
        "unrealized_pnl": None,
        "equity": None,
        "kill_switch_active": None,
        "reconciliation_passed": None,
        "live_trading_authorized": False,
    }
    payload.update(overrides)
    payload["live_trading_authorized"] = False
    return payload


def _decimal_text(value: Any) -> str | None:
    if isinstance(value, str) and len(value) <= 40 and _DECIMAL_TEXT.fullmatch(value):
        return value
    return None


def _optional_decimal(value: Any) -> tuple[str | None, bool]:
    if value is None:
        return None, True
    text = _decimal_text(value)
    return (text, True) if text is not None else (None, False)


def _position_count(positions: Any) -> int | None:
    if not isinstance(positions, list):
        return None
    for row in positions:
        if not isinstance(row, Mapping):
            return None
        symbol = row.get("symbol")
        if not isinstance(symbol, str) or _SYMBOL_TEXT.fullmatch(symbol) is None:
            return None
    return len(positions)


def _paper_operational_view(paper: Mapping[str, Any] | None, *, allowed: bool) -> dict[str, Any]:
    """Read-only paper summary. Denied callers receive no balances or symbols."""
    if not allowed:
        return _blank_paper("permission_denied")
    if not isinstance(paper, Mapping):
        return _blank_paper("unavailable")
    source = paper.get("source_ledger")
    if source not in _PAPER_SOURCES:
        return _blank_paper("unavailable")
    mode_raw = paper.get("mode")
    mode = mode_raw if mode_raw in _PAPER_MODES else None
    halt = paper.get("kill_switch_active")
    kill_switch = halt if type(halt) is bool else None
    if source == "canonical_execution":
        report = paper.get("reconciliation")
        if not isinstance(report, Mapping) or report.get("passed") is not True:
            return _blank_paper(
                "reconciliation_unavailable",
                source_ledger=source,
                mode=mode,
                initialized=paper.get("initialized") is True,
                kill_switch_active=kill_switch,
                reconciliation_passed=False,
            )
    if paper.get("initialized") is not True:
        return _blank_paper(
            "not_initialized",
            source_ledger=source,
            mode=mode,
            initialized=False,
            kill_switch_active=kill_switch,
            reconciliation_passed=True if source == "canonical_execution" else None,
        )
    cash = _decimal_text(paper.get("cash"))
    pnl = paper.get("pnl") if isinstance(paper.get("pnl"), Mapping) else {}
    realized, realized_ok = _optional_decimal(pnl.get("realized"))
    unrealized, unrealized_ok = _optional_decimal(pnl.get("unrealized"))
    equity, equity_ok = _optional_decimal(pnl.get("equity"))
    fill_count = paper.get("fill_count")
    fill_ok = type(fill_count) is int and fill_count >= 0
    if cash is None or not realized_ok or not unrealized_ok or not equity_ok or not fill_ok:
        return _blank_paper("unavailable")
    open_orders = paper.get("open_orders")
    if isinstance(open_orders, list) and all(isinstance(row, Mapping) for row in open_orders):
        open_order_count: int | None = len(open_orders)
        open_orders_reason = None
    elif open_orders is None:
        open_order_count = None
        reason = paper.get("open_orders_reason")
        open_orders_reason = reason if reason in _OPEN_ORDER_REASONS else "unavailable"
    else:
        open_order_count = None
        open_orders_reason = "unavailable"
    return _blank_paper(
        "available",
        source_ledger=source,
        mode=mode,
        initialized=True,
        cash=cash,
        position_count=_position_count(paper.get("positions")),
        fill_count=fill_count,
        open_order_count=open_order_count,
        open_orders_reason=open_orders_reason,
        realized_pnl=realized,
        unrealized_pnl=unrealized,
        equity=equity,
        kill_switch_active=kill_switch,
        reconciliation_passed=True if source == "canonical_execution" else None,
    )


def monitoring_status(
    network: Mapping[str, Any],
    paper: Mapping[str, Any] | None,
    *,
    paper_allowed: bool,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Read-only peer, local-node and paper operational snapshot.

    ``paper`` is ignored unless ``paper_allowed`` is true, so a health-only
    caller cannot receive balances through this projection.
    """
    generated = network.get("generated_at") if isinstance(network, Mapping) else None
    if not isinstance(generated, str) or _ISO_UTC_RE.fullmatch(generated) is None:
        generated = _utcnow(now).isoformat()
    peer = network.get("peer_health") if isinstance(network, Mapping) else None
    local = network.get("local_live_node") if isinstance(network, Mapping) else None
    ingestion = isinstance(network, Mapping) and network.get("ingestion_enabled") is True
    return {
        "schema": MONITORING_SCHEMA,
        "generated_at": generated,
        "read_only": True,
        "live_trading_authorized": False,
        "peer_health": _peer_health_summary(peer if isinstance(peer, Mapping) else None),
        "node_status": _node_status(local if isinstance(local, Mapping) else None, ingestion_enabled=ingestion),
        "paper": _paper_operational_view(paper if paper_allowed else None, allowed=paper_allowed),
    }


def _canonical_money(number: Decimal) -> str | None:
    if not number.is_finite():
        return None
    text = format(number, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    text = text or "0"
    if len(text) > 40 or _DECIMAL_TEXT.fullmatch(text) is None:
        return None
    return text


def _operations_local(local: Mapping[str, Any] | None, generated: str) -> dict[str, Any]:
    """One local loopback node. PeerId is never copied into status or version."""
    status = _node_status(local, ingestion_enabled=False)
    if status["status"] != "observed" or not isinstance(local, Mapping):
        reason = status.get("reason")
        return {
            "source": "loopback_local_node",
            "status": "unavailable",
            "reason": reason if reason in _NODE_REASONS else "health_unreachable_or_invalid",
            "last_seen_at": None,
            "last_seen_meaning": None,
            "version": None,
            "version_reason": "not_reported",
            "liveness": "not_observed",
            "authenticated_or_attested": False,
        }
    version = software_version(local.get("version"))
    return {
        "source": "loopback_local_node",
        "status": "ready" if status["ready"] is True else "not_ready",
        "reason": None,
        "last_seen_at": generated,
        "last_seen_meaning": "admin_probe_time_not_node_clock",
        "version": version,
        "version_reason": "reported" if version else "not_reported",
        "liveness": "loopback_probe",
        "authenticated_or_attested": False,
    }


def _operations_nodes(network: Mapping[str, Any] | None, generated: str) -> dict[str, Any]:
    peer = _peer_health_view(
        network.get("peer_health") if isinstance(network, Mapping) else None
    )
    local_raw = network.get("local_live_node") if isinstance(network, Mapping) else None
    enrolled: list[dict[str, Any]] = []
    if peer["status"] == "enrolled_registry":
        for item in peer["nodes"]:
            enrolled.append({
                "source": "identity_registry",
                "node_id": item["node_id"],
                "status": "enrolled",
                "trust_level": item["trust_level"],
                "last_seen_at": item["last_enrollment_observed_at"],
                "last_seen_meaning": "enrollment_or_reenrollment_not_liveness",
                "version": None,
                "version_reason": "not_recorded",
                "liveness": "not_observed",
            })
    return {
        "status": peer["status"],
        "reason": peer["reason"],
        "enrolled_count": peer["peer_count"] if peer["status"] == "enrolled_registry" else None,
        "nodes_truncated": peer["nodes_truncated"] is True and peer["status"] == "enrolled_registry",
        "nodes": enrolled,
        "local": _operations_local(local_raw if isinstance(local_raw, Mapping) else None, generated),
    }


def _kill_switch_view(ingestion_enabled: bool, paper_state: str, halted: bool | None) -> dict[str, Any]:
    return {
        "ingestion_enabled": ingestion_enabled is True,
        "ingestion_state": "enabled" if ingestion_enabled is True else "disabled",
        "paper_execution_halted": halted,
        "paper_execution_state": paper_state,
        "live_trading_authorized": False,
    }


def _daily_loss_view(state: str, *, reason: str | None = None) -> dict[str, Any]:
    return {
        "basis": None,
        "state": state,
        "reason": state if reason is None else reason,
        "realized_pnl": None,
        "limit": None,
        "live_execution": False,
    }


def _accept_daily_loss(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    """Keep a paper daily-loss gate only when its amounts recompute the same state."""
    rejected = _daily_loss_view("unavailable", reason="account_unavailable")
    if not isinstance(raw, Mapping) or raw.get("live_execution") is not False:
        return rejected
    if raw.get("basis") != "realized_pnl" or raw.get("state") not in {"clear", "limit_reached"}:
        return rejected
    realized_text = _decimal_text(raw.get("realized_pnl"))
    limit_text = _decimal_text(raw.get("limit"))
    if realized_text is None or limit_text is None:
        return rejected
    try:
        realized = Decimal(realized_text)
        limit = Decimal(limit_text)
    except InvalidOperation:
        return rejected
    if limit < 0 or not realized.is_finite() or not limit.is_finite():
        return rejected
    expected = "limit_reached" if realized <= -limit else "clear"
    if raw.get("state") != expected:
        return rejected
    realized_out = _canonical_money(realized)
    limit_out = _canonical_money(limit)
    if realized_out is None or limit_out is None:
        return rejected
    return {
        "basis": "realized_pnl",
        "state": expected,
        "reason": None,
        "realized_pnl": realized_out,
        "limit": limit_out,
        "live_execution": False,
    }


def _load_daily_loss_gate() -> dict[str, Any] | None:
    try:
        from .paper_execution import daily_loss_gate

        gate = daily_loss_gate()
    except (ValueError, ArithmeticError, sqlite3.Error, OSError, ImportError):
        return None
    return gate if isinstance(gate, Mapping) else None


def _open_position_rows(positions: Any) -> tuple[list[dict[str, str]] | None, int | None, bool]:
    if not isinstance(positions, list):
        return None, None, False
    rows: list[dict[str, str]] = []
    for item in positions:
        if not isinstance(item, Mapping):
            return None, None, False
        symbol = item.get("symbol")
        quantity = _decimal_text(item.get("quantity"))
        average = _decimal_text(item.get("average_cost"))
        if not isinstance(symbol, str) or _SYMBOL_TEXT.fullmatch(symbol) is None:
            return None, None, False
        if quantity is None or average is None:
            return None, None, False
        try:
            qty = Decimal(quantity)
            cost = Decimal(average)
        except InvalidOperation:
            return None, None, False
        if not qty.is_finite() or not cost.is_finite() or cost < 0:
            return None, None, False
        if qty == 0:
            continue
        shown_qty = _canonical_money(qty)
        shown_cost = _canonical_money(cost)
        if shown_qty is None or shown_cost is None:
            return None, None, False
        rows.append({"symbol": symbol, "quantity": shown_qty, "average_cost": shown_cost})
    truncated = len(rows) > OPEN_POSITION_LIMIT
    return rows[:OPEN_POSITION_LIMIT], len(rows), truncated


def _paper_trading_view(paper: Mapping[str, Any] | None, *, ingestion_enabled: bool) -> dict[str, Any]:
    """Paper-only kill switch, open positions and daily-loss gate.

    Balances, order ids and marks are left on the paper status route.
    """
    denied_numbers = {
        "source_ledger": None,
        "mode": None,
        "open_positions": None,
        "open_position_count": None,
        "positions_truncated": False,
        "live_trading_authorized": False,
    }
    if not isinstance(paper, Mapping):
        return {
            "status": "unavailable",
            **denied_numbers,
            "kill_switch": _kill_switch_view(ingestion_enabled, "unavailable", None),
            "positions_reason": "unavailable",
            "daily_loss_gate": _daily_loss_view("unavailable"),
        }
    source = paper.get("source_ledger")
    if source not in _PAPER_SOURCES:
        return {
            "status": "unavailable",
            **denied_numbers,
            "kill_switch": _kill_switch_view(ingestion_enabled, "unavailable", None),
            "positions_reason": "unavailable",
            "daily_loss_gate": _daily_loss_view("unavailable"),
        }
    mode_raw = paper.get("mode")
    mode = mode_raw if mode_raw in _PAPER_MODES else None
    if source == "canonical_execution":
        report = paper.get("reconciliation")
        if not isinstance(report, Mapping) or report.get("passed") is not True:
            return {
                "status": "reconciliation_unavailable",
                "source_ledger": source,
                "mode": mode,
                "kill_switch": _kill_switch_view(ingestion_enabled, "unavailable", None),
                "open_positions": None,
                "open_position_count": None,
                "positions_truncated": False,
                "positions_reason": "reconciliation_unavailable",
                "daily_loss_gate": _daily_loss_view("reconciliation_unavailable"),
                "live_trading_authorized": False,
            }
    if paper.get("initialized") is not True:
        return {
            "status": "not_initialized",
            "source_ledger": source,
            "mode": mode,
            "kill_switch": _kill_switch_view(ingestion_enabled, "unavailable", None),
            "open_positions": None,
            "open_position_count": None,
            "positions_truncated": False,
            "positions_reason": "not_initialized",
            "daily_loss_gate": _daily_loss_view("not_initialized"),
            "live_trading_authorized": False,
        }
    if source == "legacy_synthetic":
        paper_state, halted = "not_stored", None
        gate = _daily_loss_view("not_stored")
    else:
        halt = paper.get("kill_switch_active")
        if halt is True:
            paper_state, halted = "halted", True
        elif halt is False:
            paper_state, halted = "clear", False
        else:
            paper_state, halted = "unavailable", None
        gate = _accept_daily_loss(_load_daily_loss_gate())
    positions, count, truncated = _open_position_rows(paper.get("positions"))
    return {
        "status": "available",
        "source_ledger": source,
        "mode": mode,
        "kill_switch": _kill_switch_view(ingestion_enabled, paper_state, halted),
        "open_positions": positions,
        "open_position_count": count,
        "positions_truncated": truncated,
        "positions_reason": None if positions is not None else "unavailable",
        "daily_loss_gate": gate,
        "live_trading_authorized": False,
    }


def operations_status(
    network: Mapping[str, Any],
    paper: Mapping[str, Any] | None,
    *,
    paper_allowed: bool,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Per-node status and a paper-only operational summary.

    ``paper`` is ignored unless ``paper_allowed`` is true. The daily-loss gate
    is read only for an allowed, reconciled paper execution account.
    """
    generated = network.get("generated_at") if isinstance(network, Mapping) else None
    if not isinstance(generated, str) or _ISO_UTC_RE.fullmatch(generated) is None:
        generated = _utcnow(now).isoformat()
    ingestion = isinstance(network, Mapping) and network.get("ingestion_enabled") is True
    if paper_allowed:
        trading = _paper_trading_view(paper, ingestion_enabled=ingestion)
    else:
        trading = {
            "status": "permission_denied",
            "source_ledger": None,
            "mode": None,
            "kill_switch": _kill_switch_view(ingestion, "permission_denied", None),
            "open_positions": None,
            "open_position_count": None,
            "positions_truncated": False,
            "positions_reason": "permission_denied",
            "daily_loss_gate": _daily_loss_view("permission_denied"),
            "live_trading_authorized": False,
        }
    return {
        "schema": OPERATIONS_SCHEMA,
        "generated_at": generated,
        "read_only": True,
        "live_trading_authorized": False,
        "nodes": _operations_nodes(network if isinstance(network, Mapping) else None, generated),
        "paper_trading": trading,
    }


def _hidden_exposure(status: str = "unavailable_without_marks") -> dict[str, Any]:
    basis = status if status in {"unavailable_without_marks", "unavailable"} else "unavailable"
    return {
        "status": status,
        "market_value": None,
        "exposure": basis,
        "exposure_basis": basis,
        "cost_basis_gross": None,
        "marks_available": False,
    }


def _marked_exposure(paper: Mapping[str, Any], risk: Mapping[str, Any] | None) -> dict[str, Any]:
    """Marked exposure only when the paper snapshot and risk snapshot agree."""
    market = paper.get("market_value")
    market_text = _decimal_text(market) if market is not None else None
    if market is not None and market_text is None:
        return _hidden_exposure("unavailable")
    cost = paper.get("cost_basis_gross")
    cost_text = _decimal_text(cost) if cost is not None else None
    if cost is not None and cost_text is None:
        cost_text = None
    marks = paper.get("marks_available") is True
    if isinstance(risk, Mapping):
        risk_market = risk.get("market_value")
        risk_exposure = risk.get("exposure")
        risk_cost = risk.get("cost_basis_gross")
        if risk_market is not None and _decimal_text(risk_market) != market_text:
            return _hidden_exposure("unavailable")
        if market_text is None:
            if risk_exposure not in (None, "unavailable_without_marks"):
                return _hidden_exposure("unavailable")
        elif risk_exposure not in (None, market_text):
            return _hidden_exposure("unavailable")
        if risk_cost is not None and _decimal_text(risk_cost) != cost_text:
            cost_text = None
    if market_text is not None and not marks:
        return _hidden_exposure("unavailable")
    if marks and market_text is None:
        hidden = _hidden_exposure("unavailable")
        hidden["cost_basis_gross"] = cost_text
        return hidden
    if market_text is None:
        hidden = _hidden_exposure()
        hidden["cost_basis_gross"] = cost_text
        return hidden
    return {
        "status": "mark_to_market",
        "market_value": market_text,
        "exposure": market_text,
        "exposure_basis": "mark_to_market",
        "cost_basis_gross": cost_text,
        "marks_available": True,
    }


def _paper_order_row(item: Any, *, open_only: bool) -> dict[str, str] | None:
    if not isinstance(item, Mapping):
        return None
    order_id = item.get("client_order_id")
    symbol = item.get("symbol")
    side = item.get("side")
    state = item.get("state")
    quantity = _decimal_text(item.get("quantity"))
    if not isinstance(order_id, str) or _ORDER_ID_TEXT.fullmatch(order_id) is None:
        return None
    if not isinstance(symbol, str) or _SYMBOL_TEXT.fullmatch(symbol) is None:
        return None
    if side not in _ORDER_SIDES or not isinstance(state, str):
        return None
    if open_only:
        if state not in _OPEN_ORDER_STATES:
            return None
    elif state not in _ORDER_STATES:
        return None
    if quantity is None:
        return None
    try:
        qty = Decimal(quantity)
        if not qty.is_finite() or qty <= 0:
            return None
    except InvalidOperation:
        return None
    price_raw = item.get("price")
    price_text = None
    if open_only or price_raw is not None:
        price_text = _decimal_text(price_raw)
        if price_text is None:
            return None
        try:
            price = Decimal(price_text)
        except InvalidOperation:
            return None
        if not price.is_finite() or price <= 0:
            return None
        price_text = _canonical_money(price)
        if price_text is None:
            return None
    shown_qty = _canonical_money(qty)
    if shown_qty is None:
        return None
    row = {
        "client_order_id": order_id,
        "symbol": symbol,
        "side": side,
        "state": state,
        "quantity": shown_qty,
    }
    if price_text is not None:
        row["price"] = price_text
    for name in ("filled_quantity", "leaves_quantity"):
        raw = item.get(name)
        if raw is None:
            continue
        text = _decimal_text(raw)
        if text is None:
            return None
        try:
            amount = Decimal(text)
        except InvalidOperation:
            return None
        if not amount.is_finite() or amount < 0:
            return None
        shown = _canonical_money(amount)
        if shown is None:
            return None
        row[name] = shown
    return row


def _paper_order_rows(
    orders: Any, *, open_only: bool,
) -> tuple[list[dict[str, str]] | None, int | None, bool, str | None]:
    if not isinstance(orders, list):
        return None, None, False, "unavailable"
    rows: list[dict[str, str]] = []
    for item in orders:
        row = _paper_order_row(item, open_only=open_only)
        if row is None:
            return None, None, False, "unavailable"
        rows.append(row)
    truncated = len(rows) > PAPER_ORDER_LIMIT
    return rows[:PAPER_ORDER_LIMIT], len(rows), truncated, None


def _limit_block(status: str, reason: str | None, limits: dict[str, str] | None = None) -> dict[str, Any]:
    return {
        "status": status,
        "reason": reason,
        "limits": limits,
        "live_execution": False,
    }


def _accept_limit_policy(policy: Mapping[str, Any] | None, *, halted: bool) -> dict[str, Any]:
    """Keep limit amounts only when they are finite paper limits and the halt matches."""
    if not isinstance(policy, Mapping) or policy.get("live_execution") is not False:
        return _limit_block("unavailable", "account_unavailable")
    if policy.get("broker_order_submitted") is not False:
        return _limit_block("unavailable", "account_unavailable")
    if policy.get("status") != "available" or policy.get("kill_switch_active") is not halted:
        return _limit_block("unavailable", "account_unavailable")
    raw = policy.get("limits")
    if not isinstance(raw, Mapping):
        return _limit_block("unavailable", "account_unavailable")
    limits: dict[str, str] = {}
    for name in _LIMIT_FIELDS:
        text = _decimal_text(raw.get(name))
        if text is None:
            return _limit_block("unavailable", "account_unavailable")
        try:
            value = Decimal(text)
        except InvalidOperation:
            return _limit_block("unavailable", "account_unavailable")
        if not value.is_finite() or (value < 0 if name == "max_daily_loss" else value <= 0):
            return _limit_block("unavailable", "account_unavailable")
        shown = _canonical_money(value)
        if shown is None:
            return _limit_block("unavailable", "account_unavailable")
        limits[name] = shown
    return _limit_block("available", None, limits)


def _book_flags(risk: Mapping[str, Any] | None) -> list[dict[str, str]]:
    if not isinstance(risk, Mapping) or not isinstance(risk.get("flags"), list):
        return []
    flags: list[dict[str, str]] = []
    for item in risk["flags"]:
        if not isinstance(item, Mapping):
            continue
        kind = item.get("kind")
        expected = _BOOK_FLAGS.get(kind)
        if expected is None:
            continue
        severity, message = expected
        if item.get("severity") != severity or item.get("message") != message:
            continue
        flags.append({"kind": str(kind), "severity": severity, "message": message})
    return flags


def _kill_switch_book(ingestion_enabled: bool | None, state: str, halted: bool | None) -> dict[str, Any]:
    return {
        "ingestion_enabled": ingestion_enabled if type(ingestion_enabled) is bool else None,
        "paper_execution_halted": halted,
        "paper_execution_state": state,
        "live_trading_authorized": False,
    }


def _trading_book(status: str, generated: str, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema": PAPER_TRADING_SCHEMA,
        "generated_at": generated,
        "read_only": True,
        "status": status,
        "source_ledger": None,
        "mode": None,
        "live_trading_authorized": False,
        "live_execution": False,
        "broker_order_submitted": False,
        "positions": None,
        "position_count": None,
        "positions_truncated": False,
        "positions_reason": None,
        "open_orders": None,
        "open_order_count": None,
        "open_orders_truncated": False,
        "open_orders_reason": None,
        "recent_orders": None,
        "recent_order_count": None,
        "recent_orders_truncated": False,
        "recent_orders_reason": None,
        "recent_orders_order": None,
        "marked_exposure": _hidden_exposure(),
        "kill_switch": _kill_switch_book(None, "unavailable", None),
        "risk_limits": _limit_block("unavailable", "unavailable"),
        "risk_gate": None,
        "risk_flags": [],
    }
    payload.update(overrides)
    payload["read_only"] = True
    payload["live_trading_authorized"] = False
    payload["live_execution"] = False
    payload["broker_order_submitted"] = False
    limits = payload.get("risk_limits")
    if isinstance(limits, dict):
        limits["live_execution"] = False
    halt = payload.get("kill_switch")
    if isinstance(halt, dict):
        halt["live_trading_authorized"] = False
    return payload


def _load_risk_limits() -> dict[str, Any] | None:
    try:
        from .paper_execution import read_risk_limits

        policy = read_risk_limits()
    except (ValueError, ArithmeticError, sqlite3.Error, OSError, ImportError):
        return None
    return policy if isinstance(policy, Mapping) else None


def paper_trading_status(
    paper: Mapping[str, Any] | None = None,
    risk: Mapping[str, Any] | None = None,
    *,
    ingestion_enabled: bool | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Read-only paper book: positions, orders, marked exposure, limits and halt.

    This does not submit, cancel, or amend an order, and it does not change a
    kill switch or a risk limit. A legacy ledger has no stored orders or limits.
    """
    current = _utcnow(now)
    if paper is None:
        paper = paper_status(now=current)
    if risk is None and isinstance(paper, Mapping):
        risk = risk_status(paper=paper, now=current)
    generated = paper.get("generated_at") if isinstance(paper, Mapping) else None
    if not isinstance(generated, str) or _ISO_UTC_RE.fullmatch(generated) is None:
        generated = current.isoformat()
    flags = _book_flags(risk if isinstance(risk, Mapping) else None)
    gate = risk.get("risk_gate") if isinstance(risk, Mapping) else None
    risk_gate = "enforced" if gate == "enforced" else None
    ingestion = ingestion_enabled if type(ingestion_enabled) is bool else None
    if not isinstance(paper, Mapping):
        return _trading_book(
            "unavailable", generated,
            kill_switch=_kill_switch_book(ingestion, "unavailable", None),
            risk_flags=flags, risk_gate=risk_gate,
        )
    source = paper.get("source_ledger")
    if source not in _PAPER_SOURCES:
        return _trading_book(
            "unavailable", generated,
            kill_switch=_kill_switch_book(ingestion, "unavailable", None),
            risk_flags=flags, risk_gate=risk_gate,
        )
    mode_raw = paper.get("mode")
    mode = mode_raw if mode_raw in _PAPER_MODES else None
    if source == "canonical_execution":
        report = paper.get("reconciliation")
        if not isinstance(report, Mapping) or report.get("passed") is not True:
            return _trading_book(
                "reconciliation_unavailable", generated,
                source_ledger=source, mode=mode,
                positions_reason="reconciliation_unavailable",
                open_orders_reason="account_unavailable",
                recent_orders_reason="account_unavailable",
                marked_exposure=_hidden_exposure("unavailable"),
                kill_switch=_kill_switch_book(ingestion, "unavailable", None),
                risk_limits=_limit_block("reconciliation_unavailable", "reconciliation_unavailable"),
                risk_flags=flags, risk_gate=risk_gate,
            )
    if paper.get("initialized") is not True:
        return _trading_book(
            "not_initialized", generated,
            source_ledger=source, mode=mode,
            positions_reason="not_initialized",
            open_orders_reason="not_stored" if source == "legacy_synthetic" else "account_unavailable",
            recent_orders_reason="not_stored" if source == "legacy_synthetic" else "account_unavailable",
            kill_switch=_kill_switch_book(ingestion, "not_stored" if source == "legacy_synthetic" else "unavailable", None),
            risk_limits=_limit_block(
                "not_stored" if source == "legacy_synthetic" else "not_initialized",
                "not_stored" if source == "legacy_synthetic" else "not_initialized",
            ),
            risk_flags=flags, risk_gate=risk_gate,
        )
    if source == "legacy_synthetic":
        paper_state, halted = "not_stored", None
        limits = _limit_block("not_stored", "not_stored")
    else:
        halt = paper.get("kill_switch_active")
        if halt is True:
            paper_state, halted = "halted", True
        elif halt is False:
            paper_state, halted = "clear", False
        else:
            paper_state, halted = "unavailable", None
        limits = (
            _accept_limit_policy(_load_risk_limits(), halted=halt)
            if type(halt) is bool else _limit_block("unavailable", "account_unavailable")
        )
    positions, count, truncated = _open_position_rows(paper.get("positions"))
    open_raw = paper.get("open_orders")
    if open_raw is None:
        reason = paper.get("open_orders_reason")
        open_reason = reason if reason in _OPEN_ORDER_REASONS else "unavailable"
        open_rows, open_count, open_truncated = None, None, False
    else:
        open_rows, open_count, open_truncated, open_reason = _paper_order_rows(open_raw, open_only=True)
    if "orders" not in paper or paper.get("orders") is None:
        stored = paper.get("open_orders_reason")
        recent_reason = stored if stored in _OPEN_ORDER_REASONS else "unavailable"
        recent_rows, recent_count, recent_truncated = None, None, False
    else:
        recent_rows, recent_count, recent_truncated, recent_reason = _paper_order_rows(
            paper.get("orders"), open_only=False,
        )
    return _trading_book(
        "available", generated,
        source_ledger=source, mode=mode,
        positions=positions, position_count=count, positions_truncated=truncated,
        positions_reason=None if positions is not None else "unavailable",
        open_orders=open_rows, open_order_count=open_count,
        open_orders_truncated=open_truncated, open_orders_reason=open_reason,
        recent_orders=recent_rows, recent_order_count=recent_count,
        recent_orders_truncated=recent_truncated, recent_orders_reason=recent_reason,
        recent_orders_order="client_order_id" if recent_rows is not None else None,
        marked_exposure=_marked_exposure(paper, risk if isinstance(risk, Mapping) else None),
        kill_switch=_kill_switch_book(ingestion, paper_state, halted),
        risk_limits=limits, risk_flags=flags, risk_gate=risk_gate,
    )


def risk_status(
    feeds: Mapping[str, Any] | None = None,
    network: Mapping[str, Any] | None = None,
    paper: Mapping[str, Any] | None = None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Deterministic risk flags derived from the other snapshots.

    Unavailable measurements are reported as unavailable; nothing is
    fabricated. Unrealized P&L requires external marks and is normally
    absent, so exposure-based breaches cannot be computed here."""
    current = _utcnow(now)
    feeds = feeds or feeds_status(now=current)
    network = network or network_status(now=current)
    paper = paper or paper_status(now=current)
    flags: list[dict[str, str]] = []
    if not network.get("ingestion_enabled"):
        flags.append(
            {
                "kind": "ingestion_disabled",
                "key": "global",
                "severity": "warning",
                "message": "P2P ingestion kill switch is disabled",
            }
        )
    outbox = network.get("outbox") or {}
    pending = outbox.get("pending")
    if isinstance(pending, int) and pending > OUTBOX_BACKLOG_ALERT_THRESHOLD:
        flags.append(
            {
                "kind": "outbox_backlog",
                "key": "global",
                "severity": "warning",
                "message": f"Observation outbox backlog {pending} exceeds "
                f"{OUTBOX_BACKLOG_ALERT_THRESHOLD}",
            }
        )
    for adapter in feeds.get("adapters", []):
        if not adapter["quality_review"]["fresh"]:
            flags.append(
                {
                    "kind": "feed_quality_review_stale",
                    "key": str(adapter["adapter"]),
                    "severity": "critical",
                    "message": f"Adapter {adapter['adapter']} quality review is stale or invalid",
                }
            )
        if adapter["suspended"]:
            flags.append(
                {
                    "kind": "feed_adapter_suspended",
                    "key": str(adapter["adapter"]),
                    "severity": "info",
                    "message": f"Adapter {adapter['adapter']} is revoked by security_admin",
                }
            )
    if not paper.get("initialized"):
        flags.append(
            {
                "kind": "paper_ledger_uninitialized",
                "key": "global",
                "severity": "info",
                "message": "Paper ledger is not initialized; cash, positions and P&L unavailable",
            }
        )
    if paper.get("source_ledger") == "canonical_execution":
        if (paper.get("reconciliation") or {}).get("passed") is not True:
            flags.append({"kind": "paper_reconciliation_failed", "key": "canonical",
                          "severity": "critical",
                          "message": "Paper account reconciliation failed; accounting unavailable"})
        if paper.get("kill_switch_active") is True:
            flags.append({"kind": "paper_execution_halted", "key": "canonical",
                          "severity": "critical", "message": "Paper execution kill switch is active"})
    market_value = paper.get("market_value")
    return {
        "schema": RISK_SCHEMA,
        "generated_at": current.isoformat(),
        "flag_count": len(flags),
        "flags": flags,
        "exposure": market_value if market_value is not None else "unavailable_without_marks",
        "exposure_basis": "mark_to_market" if market_value is not None else "unavailable_without_marks",
        "cost_basis_gross": paper.get("cost_basis_gross"),
        "market_value": market_value if market_value is not None else None,
        "risk_gate": "enforced",
        "live_trading_authorized": False,
    }


def sync_durable_alerts(
    risk: Mapping[str, Any], *, now: datetime | None = None,
) -> dict[str, Any]:
    """Persist raised risk alerts; unreadable stores fail closed."""
    path = resolve_alert_db_path()
    if path is None:
        return unavailable_alert_history("not_configured")
    try:
        return record_raised_alerts(path, risk.get("flags", []) if isinstance(risk, Mapping) else [], now=now)
    except (AlertStoreError, OSError, ValueError):
        return unavailable_alert_history("alert_store_unavailable")


def acknowledge_durable_alert(
    alert_id: str, role: str, *, now: datetime | None = None,
) -> dict[str, Any] | None:
    path = resolve_alert_db_path()
    if path is None:
        raise AlertStoreError("not_configured")
    return acknowledge_stored_alert(path, alert_id, role, now=now)


def evaluate_alerts(
    registry: AlertsRegistry,
    risk: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Mirror current risk flags into the alert registry (idempotent)."""
    for flag in risk.get("flags", []):
        registry.raise_alert(
            flag["kind"],
            key=flag.get("key", "global"),
            severity=flag["severity"],
            message=flag["message"],
            now=now,
        )
    return registry.active()


def revoke_feed_adapter(adapter_id: str) -> dict[str, Any]:
    """Revoke a feed adapter's entitlement at runtime (security_admin).

    Enforcement is real: ``authorized_feeds.ingest_adapter_ticks`` refuses a
    suspended adapter BEFORE fetch. The revocation set is process-local and
    resets on restart; durable revocation sync across nodes remains future
    work tracked by the P2P reliability workstream."""
    authorized_feeds.suspend_adapter(str(adapter_id))
    return {
        "adapter": str(adapter_id),
        "suspended": True,
        "scope": "quant_api_process",
        "durable": False,
    }


# ---------------------------------------------------------------------------
# Process-local runtime state (reset between tests)

AUDIT_LOG = AuditLog()
ALERTS = AlertsRegistry()


def reset_admin_state() -> None:
    """Test isolation hook: clears audit, alerts and adapter suspensions."""
    AUDIT_LOG.clear()
    ALERTS.clear()
    for name in list(authorized_feeds.suspended_adapters()):
        authorized_feeds.restore_adapter(name)
