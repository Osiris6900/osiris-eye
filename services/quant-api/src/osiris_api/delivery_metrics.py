"""Local delivery metrics and operator alert evaluation for receipt-gated delivery.

Issue #133 asks for metrics on pending, acknowledged, unconfirmed,
quarantined, retry age and dead-letter counts, plus documented operational
alerts. This module reads the existing durable stores and never writes to
them, never retransmits, and never pages anyone by itself.

Every number is a local SQLite observation. ``acknowledged`` means a verified
recipient admission receipt was durably committed, not that the recipient
processed the observation. ``unconfirmed`` is an unknown outcome, not proof of
loss. Metrics do not establish exactly-once delivery.
"""
from __future__ import annotations

from typing import Any

SCHEMA = "osiris.delivery_metrics.v1"
DEAD_LETTER_STATES = ("held", "denied", "expired")
DEFAULT_PENDING_RECEIPT_AGE_SECONDS = 240  # twice the 120s receipt wait window
DEFAULT_OUTBOX_PENDING_THRESHOLD = 100

NOTES = (
    "local_sqlite_observation_only",
    "acknowledged_means_verified_admission_receipt_not_application_processing",
    "unconfirmed_is_unknown_outcome_not_failure",
    "metrics_do_not_page_an_operator",
)


def _require_clock(now: int) -> None:
    if type(now) is not int or now < 0:
        raise ValueError("invalid_metrics_clock")


def _require_threshold(value: int, name: str) -> None:
    if type(value) is not int or value < 0:
        raise ValueError(f"invalid_{name}_threshold")


def _require_metric(value: Any, name: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"invalid_component_metric_{name}")
    return value


def _outbox_section(outbox: Any) -> dict[str, Any]:
    if outbox is None:
        return {"configured": False}
    if not callable(getattr(outbox, "pending_count", None)) or not callable(
        getattr(outbox, "rejected_count", None)
    ):
        raise TypeError("invalid_outbox_component")
    return {
        "configured": True,
        "pending": _require_metric(outbox.pending_count(), "outbox_pending"),
        "quarantined": _require_metric(outbox.rejected_count(), "outbox_quarantined"),
    }


def _ledger_section(ledger: Any, now: int) -> dict[str, Any]:
    if ledger is None:
        return {"configured": False}
    if not callable(getattr(ledger, "counts", None)) or not callable(
        getattr(ledger, "outstanding", None)
    ):
        raise TypeError("invalid_receipt_ledger_component")
    counts = ledger.counts()
    if not isinstance(counts, dict):
        raise ValueError("invalid_component_metric_ledger_counts")
    section = {
        "configured": True,
        "pending": _require_metric(counts.get("pending", 0), "ledger_pending"),
        "acknowledged": _require_metric(counts.get("acknowledged", 0), "ledger_acknowledged"),
        "unconfirmed": _require_metric(counts.get("unconfirmed", 0), "ledger_unconfirmed"),
    }
    rows = ledger.outstanding(limit=1000)
    if not isinstance(rows, list):
        raise ValueError("invalid_component_metric_ledger_outstanding")
    ages = []
    for row in rows:
        created = row.get("created_at") if isinstance(row, dict) else None
        if type(created) is not int:
            raise ValueError("invalid_component_metric_ledger_outstanding")
        ages.append(max(0, now - created))
    section["outstanding_examined"] = len(rows)
    section["oldest_outstanding_age_seconds"] = max(ages) if ages else None
    return section


def _scheduler_section(scheduler: Any) -> dict[str, Any]:
    if scheduler is None:
        return {"configured": False}
    enabled = getattr(scheduler, "enabled", None)
    if enabled is not True and enabled is not False:
        raise TypeError("invalid_retry_scheduler_component")
    if not callable(getattr(scheduler, "counts", None)):
        raise TypeError("invalid_retry_scheduler_component")
    if enabled is not True:
        return {"configured": True, "enabled": False}
    counts = scheduler.counts()
    if not isinstance(counts, dict):
        raise ValueError("invalid_component_metric_scheduler_counts")
    validated = {state: _require_metric(value, f"scheduler_{state}")
                 for state, value in counts.items()}
    return {
        "configured": True,
        "enabled": True,
        "counts": validated,
        "dead_letter": sum(validated.get(state, 0) for state in DEAD_LETTER_STATES),
    }


def collect_delivery_metrics(
    *,
    outbox: Any = None,
    ledger: Any = None,
    scheduler: Any = None,
    now: int,
) -> dict[str, Any]:
    """Read-only snapshot of the durable delivery stores. Never sends or acks."""
    _require_clock(now)
    return {
        "schema": SCHEMA,
        "generated_at": now,
        "outbox": _outbox_section(outbox),
        "receipt_ledger": _ledger_section(ledger, now),
        "retry_scheduler": _scheduler_section(scheduler),
        "notes": list(NOTES),
    }


def evaluate_operational_alerts(
    report: dict[str, Any],
    *,
    pending_receipt_age_seconds: int = DEFAULT_PENDING_RECEIPT_AGE_SECONDS,
    unconfirmed_threshold: int = 0,
    dead_letter_threshold: int = 0,
    quarantine_threshold: int = 0,
    outbox_pending_threshold: int = DEFAULT_OUTBOX_PENDING_THRESHOLD,
) -> list[dict[str, Any]]:
    """Turn a metrics report into deterministic operator alerts.

    Alerts are advisory and local. None of them authorizes retransmission:
    every alert's remediation is operator inspection, never an automatic resend.
    """
    if not isinstance(report, dict) or report.get("schema") != SCHEMA:
        raise ValueError("invalid_metrics_report")
    _require_threshold(pending_receipt_age_seconds, "pending_receipt_age_seconds")
    _require_threshold(unconfirmed_threshold, "unconfirmed")
    _require_threshold(dead_letter_threshold, "dead_letter")
    _require_threshold(quarantine_threshold, "quarantine")
    _require_threshold(outbox_pending_threshold, "outbox_pending")
    alerts = []
    outbox = report["outbox"]
    if outbox.get("configured"):
        if outbox["pending"] > outbox_pending_threshold:
            alerts.append({
                "alert": "outbox_backlog",
                "severity": "warning",
                "observed": outbox["pending"],
                "threshold": outbox_pending_threshold,
                "detail": "undrained local outbox rows are accumulating; "
                          "check the drainer, writer and ingestion switch",
            })
        if outbox["quarantined"] > quarantine_threshold:
            alerts.append({
                "alert": "quarantined_observations",
                "severity": "warning",
                "observed": outbox["quarantined"],
                "threshold": quarantine_threshold,
                "detail": "quarantined rows are terminal until an operator "
                          "reviews the recorded rejection reasons",
            })
    ledger = report["receipt_ledger"]
    if ledger.get("configured"):
        age = ledger.get("oldest_outstanding_age_seconds")
        if age is not None and age > pending_receipt_age_seconds:
            alerts.append({
                "alert": "stale_pending_receipts",
                "severity": "warning",
                "observed": age,
                "threshold": pending_receipt_age_seconds,
                "detail": "verified receipts are not arriving; check the "
                          "receipt listener, supervisor pinning and recipients; "
                          "do not resend",
            })
        if ledger["unconfirmed"] > unconfirmed_threshold:
            alerts.append({
                "alert": "unconfirmed_deliveries",
                "severity": "warning",
                "observed": ledger["unconfirmed"],
                "threshold": unconfirmed_threshold,
                "detail": "receipt wait expired; the outcome is unknown, not "
                          "lost, and never a reason to mint a new envelope",
            })
    scheduler = report["retry_scheduler"]
    if scheduler.get("configured") and scheduler.get("enabled"):
        if scheduler["dead_letter"] > dead_letter_threshold:
            alerts.append({
                "alert": "dead_letter_rows",
                "severity": "critical",
                "observed": scheduler["dead_letter"],
                "threshold": dead_letter_threshold,
                "detail": "held, denied or expired attempts need manual "
                          "review of the row reason before any further action",
            })
    return alerts


def delivery_health_report(
    *,
    outbox: Any = None,
    ledger: Any = None,
    scheduler: Any = None,
    now: int,
    pending_receipt_age_seconds: int = DEFAULT_PENDING_RECEIPT_AGE_SECONDS,
    unconfirmed_threshold: int = 0,
    dead_letter_threshold: int = 0,
    quarantine_threshold: int = 0,
    outbox_pending_threshold: int = DEFAULT_OUTBOX_PENDING_THRESHOLD,
) -> dict[str, Any]:
    """One call: metrics snapshot plus evaluated operational alerts."""
    report = collect_delivery_metrics(
        outbox=outbox, ledger=ledger, scheduler=scheduler, now=now,
    )
    return {
        "metrics": report,
        "alerts": evaluate_operational_alerts(
            report,
            pending_receipt_age_seconds=pending_receipt_age_seconds,
            unconfirmed_threshold=unconfirmed_threshold,
            dead_letter_threshold=dead_letter_threshold,
            quarantine_threshold=quarantine_threshold,
            outbox_pending_threshold=outbox_pending_threshold,
        ),
    }
