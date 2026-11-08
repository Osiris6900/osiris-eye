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
import os
from collections import deque
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from . import authorized_feeds
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

QUALITY_REVIEW_MAX_AGE_SECONDS = 30 * 24 * 60 * 60.0  # mirrors feed_admission
OUTBOX_BACKLOG_ALERT_THRESHOLD = 100
OUTBOX_PATH_ENV = "OSIRIS_OBSERVATION_OUTBOX"


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


def network_status(
    outbox_counts: Mapping[str, Any] | None = None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = _utcnow(now)
    counts = dict(outbox_counts) if outbox_counts else {"configured": False}
    return {
        "schema": NETWORK_SCHEMA,
        "generated_at": current.isoformat(),
        "ingestion_enabled": ingestion_enabled(),
        "kill_switch_env": ENABLE_ENV,
        "outbox": counts,
        "peer_telemetry": "unavailable_in_quant_api_process",
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


def paper_status(*, now: datetime | None = None) -> dict[str, Any]:
    current = _utcnow(now)
    ledger = paper_ledger_snapshot()
    return {
        "schema": PAPER_SCHEMA,
        "generated_at": current.isoformat(),
        "mode": ledger.get("mode", "synthetic-paper-only"),
        "initialized": bool(ledger.get("initialized")),
        "cash": ledger.get("cash"),
        "positions": ledger.get("positions", []),
        "fill_count": ledger.get("fill_count", 0),
        "pnl": ledger.get("pnl"),
        "marks_available": bool(
            ledger.get("pnl") and ledger["pnl"].get("unrealized") is not None
        ),
        "live_trading_authorized": False,
    }


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
    return {
        "schema": RISK_SCHEMA,
        "generated_at": current.isoformat(),
        "flag_count": len(flags),
        "flags": flags,
        "exposure": "unavailable_without_marks",
        "risk_gate": "enforced",
        "live_trading_authorized": False,
    }


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
