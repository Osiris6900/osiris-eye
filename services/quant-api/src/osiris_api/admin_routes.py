"""Authenticated read-only admin API (issue #180).

Every route under ``/admin/`` enforces the canonical RBAC contract from
``admin_centre`` server-side. The outer bearer middleware in ``main.py``
exempts this prefix so this router's own fail-closed checks are the single
gate: no admin token configuration means 503, an unknown or missing token
means 401, and an authenticated role without the required permission means
403. Every attempt is recorded in the audit log, and every response passes
the secrets-hygiene scan before it leaves the process.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Request

from . import admin_centre
from .admin_centre import AdminAuth
from .authorized_feeds import FeedAdapterRejection

router = APIRouter(prefix="/admin", tags=["admin"])


def _authorize(request: Request, permission: str) -> tuple[str, AdminAuth]:
    """Resolve the caller's role or raise; every decision is audited."""
    auth = AdminAuth.from_environment()
    if not auth.configured:
        admin_centre.AUDIT_LOG.record("unknown", permission, "deny", "admin_not_configured")
        raise HTTPException(status_code=503, detail="admin_not_configured")
    role = auth.resolve(request.headers.get("authorization", ""))
    if role is None:
        admin_centre.AUDIT_LOG.record(
            "unknown", permission, "deny", "admin_authentication_required"
        )
        raise HTTPException(status_code=401, detail="admin_authentication_required")
    if not admin_centre.authorize(role, permission, authenticated=True):
        admin_centre.AUDIT_LOG.record(role, permission, "deny", "admin_permission_denied")
        raise HTTPException(status_code=403, detail="admin_permission_denied")
    admin_centre.AUDIT_LOG.record(role, permission, "allow")
    return role, auth


def _respond(payload: dict[str, Any], auth: AdminAuth, role: str, action: str) -> dict[str, Any]:
    """Defense-in-depth: never let a credential-shaped value leave the API."""
    violations = admin_centre.scan_payload(payload, auth.secrets())
    if violations:
        admin_centre.AUDIT_LOG.record(
            role, action, "deny", "admin_response_redacted:" + ",".join(violations[:3])
        )
        raise HTTPException(status_code=500, detail="admin_response_redacted")
    return payload


def _outbox_counts() -> dict[str, Any]:
    """Read-only outbox counts when an outbox database is configured.

    Never creates the database file as a side effect of a GET."""
    path = os.getenv(admin_centre.OUTBOX_PATH_ENV, "")
    if not path.strip():
        return {"configured": False}
    if not Path(path).exists():
        return {"configured": True, "available": False}
    from .observation_outbox import ObservationOutbox

    outbox = ObservationOutbox(path)
    return {
        "configured": True,
        "available": True,
        "pending": outbox.pending_count(),
        "rejected": outbox.rejected_count(),
    }


def _snapshots() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    network = admin_centre.network_status(outbox_counts=_outbox_counts())
    feeds = admin_centre.feeds_status()
    paper = admin_centre.paper_status()
    risk = admin_centre.risk_status(feeds, network, paper)
    admin_centre.evaluate_alerts(admin_centre.ALERTS, risk)
    return network, feeds, paper, risk


@router.get("/overview")
def overview(request: Request) -> dict[str, Any]:
    role, auth = _authorize(request, "read_health")
    network, feeds, paper, risk = _snapshots()
    payload = {
        "schema": admin_centre.OVERVIEW_SCHEMA,
        "generated_at": network["generated_at"],
        "service": "osiris-quant-api",
        "api_version": request.app.version,
        "role": role,
        "roles_configured": list(auth.roles),
        "ingestion_enabled": network["ingestion_enabled"],
        "feeds": {
            "adapter_count": feeds["adapter_count"],
            "suspended_count": feeds["suspended_count"],
            "stale_review_count": feeds["stale_review_count"],
        },
        "paper_initialized": paper["initialized"],
        "active_alert_count": len(admin_centre.ALERTS.active()),
        "risk_flag_count": risk["flag_count"],
        "live_trading_authorized": False,
    }
    return _respond(payload, auth, role, "read_health")


@router.get("/network/status")
def network_status(request: Request) -> dict[str, Any]:
    role, auth = _authorize(request, "read_health")
    payload = admin_centre.network_status(outbox_counts=_outbox_counts())
    return _respond(payload, auth, role, "read_health")


@router.get("/feeds/status")
def feeds_status(request: Request) -> dict[str, Any]:
    role, auth = _authorize(request, "read_health")
    payload = admin_centre.feeds_status()
    return _respond(payload, auth, role, "read_health")


@router.get("/paper/status")
def paper_status(request: Request) -> dict[str, Any]:
    role, auth = _authorize(request, "read_research")
    payload = admin_centre.paper_status()
    return _respond(payload, auth, role, "read_research")


@router.get("/risk/status")
def risk_status(request: Request) -> dict[str, Any]:
    role, auth = _authorize(request, "read_research")
    _, _, _, risk = _snapshots()
    return _respond(risk, auth, role, "read_research")


@router.get("/alerts")
def alerts(request: Request) -> dict[str, Any]:
    role, auth = _authorize(request, "read_health")
    _snapshots()  # refresh alert registry from current risk flags
    payload = {
        "schema": "osiris.admin_alert_list.v1",
        "active": admin_centre.ALERTS.active(),
        "acknowledged": [
            a for a in admin_centre.ALERTS.all() if a["status"] == "acknowledged"
        ],
    }
    return _respond(payload, auth, role, "read_health")


@router.post("/alerts/{alert_id}/ack")
def ack_alert(request: Request, alert_id: str) -> dict[str, Any]:
    role, auth = _authorize(request, "ack_alert")
    alert = admin_centre.ALERTS.ack(alert_id, role)
    if alert is None:
        admin_centre.AUDIT_LOG.record(role, "ack_alert", "deny", "alert_not_found")
        raise HTTPException(status_code=404, detail="alert_not_found")
    return _respond({"acknowledged": True, "alert": alert}, auth, role, "ack_alert")


@router.post("/network/kill-switch")
def kill_switch(request: Request, payload: dict = Body(...)) -> dict[str, Any]:
    role, auth = _authorize(request, "activate_kill_switch")
    enabled = payload.get("enabled")
    if not isinstance(enabled, bool):
        admin_centre.AUDIT_LOG.record(
            role, "activate_kill_switch", "deny", "kill_switch_state_invalid"
        )
        raise HTTPException(status_code=422, detail="kill_switch_state_invalid")
    admin_centre.set_ingestion_enabled(enabled)
    admin_centre.AUDIT_LOG.record(
        role, "activate_kill_switch", "allow", f"ingestion_enabled={enabled}"
    )
    body = {
        "schema": "osiris.admin_kill_switch.v1",
        "ingestion_enabled": enabled,
        "kill_switch_env": admin_centre.ENABLE_ENV,
        "live_trading_authorized": False,
    }
    return _respond(body, auth, role, "activate_kill_switch")


@router.post("/feeds/{adapter_id}/revoke")
def revoke_feed(request: Request, adapter_id: str) -> dict[str, Any]:
    role, auth = _authorize(request, "revoke_peer")
    try:
        result = admin_centre.revoke_feed_adapter(adapter_id)
    except FeedAdapterRejection:
        admin_centre.AUDIT_LOG.record(
            role, "revoke_peer", "deny", f"adapter_not_registered:{adapter_id}"
        )
        raise HTTPException(status_code=404, detail="adapter_not_registered")
    admin_centre.AUDIT_LOG.record(role, "revoke_peer", "allow", f"adapter={adapter_id}")
    result["schema"] = "osiris.admin_feed_revocation.v1"
    return _respond(result, auth, role, "revoke_peer")


@router.get("/audit")
def audit(request: Request) -> dict[str, Any]:
    role, auth = _authorize(request, "read_audit")
    payload = {
        "schema": "osiris.admin_audit_log.v1",
        "entries": admin_centre.AUDIT_LOG.recent(100),
        "storage": "process_memory_bounded_1000",
        "erase_audit": "denied_for_all_roles",
    }
    return _respond(payload, auth, role, "read_audit")
