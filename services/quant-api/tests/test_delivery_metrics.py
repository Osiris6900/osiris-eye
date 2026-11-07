"""Delivery metrics: read-only aggregation and deterministic operator alerts."""
import hashlib
import os
from datetime import UTC, datetime

import pytest

from osiris_api.delivery_metrics import (
    SCHEMA,
    collect_delivery_metrics,
    delivery_health_report,
    evaluate_operational_alerts,
)
from osiris_api.observation_outbox import ObservationOutbox
from osiris_api.receipt_gated_retry import (
    AdmissionDecision,
    ReceiptRetryScheduler,
)
from osiris_api.recipient_receipt_ledger import RecipientReceiptLedger

NOW = 1_762_470_030
CLOCK = datetime(2026, 11, 6, 23, 0, 30, tzinfo=UTC)
RECIPIENT = "peer-b"
DIGEST = "ab" * 32
MESSAGE = "m" * 22


def observation(payload="probe", when="2026-11-06T23:00:00Z"):
    return {
        "schema": "osiris.observation.v1",
        "instrument": "BTC-USD",
        "observed_at": when,
        "provider": "synthetic-retry",
        "payload": payload,
        "payload_sha256": hashlib.sha256(payload.encode()).hexdigest(),
    }


@pytest.fixture
def components(tmp_path, monkeypatch):
    monkeypatch.setenv("OSIRIS_P2P_INGEST_ENABLED", "1")
    monkeypatch.delenv("OSIRIS_OUTBOX_RECEIPT_RETRY", raising=False)
    outbox = ObservationOutbox(str(tmp_path / "outbox.sqlite3"))
    ledger = RecipientReceiptLedger(str(tmp_path / "ledger.sqlite3"))
    scheduler = ReceiptRetryScheduler(
        str(tmp_path / "retry.sqlite3"), ledger, enabled=True,
    )
    return outbox, ledger, scheduler


def tracked(calls):
    def publish(payload, recipient):
        calls.append((payload, recipient))
        return {"status": "tracked", "message_id": MESSAGE, "envelope_sha256": DIGEST}
    return publish


def test_unconfigured_components_report_clean():
    report = collect_delivery_metrics(now=NOW)
    assert report["schema"] == SCHEMA
    assert report["outbox"] == {"configured": False}
    assert report["receipt_ledger"] == {"configured": False}
    assert report["retry_scheduler"] == {"configured": False}
    assert "local_sqlite_observation_only" in report["notes"]
    assert evaluate_operational_alerts(report) == []


def test_aggregates_outbox_ledger_and_scheduler(components):
    outbox, ledger, scheduler = components
    outbox.enqueue(observation("one"))
    outbox.enqueue(observation("two"))
    ledger.register("n" * 22, RECIPIENT, DIGEST, now=NOW - 500)
    calls = []
    scheduler.schedule(observation(), RECIPIENT, now=NOW)
    scheduler.advance(
        publish=tracked(calls), allowed_providers={"synthetic-retry"},
        policy=AdmissionDecision({RECIPIENT}, set()), now=NOW, observed_clock=CLOCK,
    )
    report = collect_delivery_metrics(
        outbox=outbox, ledger=ledger, scheduler=scheduler, now=NOW,
    )
    assert report["outbox"] == {"configured": True, "pending": 2, "quarantined": 0}
    ledger_section = report["receipt_ledger"]
    assert ledger_section["pending"] == 2  # direct + scheduler-registered
    assert ledger_section["acknowledged"] == 0
    assert ledger_section["unconfirmed"] == 0
    assert ledger_section["outstanding_examined"] == 2
    assert ledger_section["oldest_outstanding_age_seconds"] == 500
    scheduler_section = report["retry_scheduler"]
    assert scheduler_section["enabled"] is True
    assert scheduler_section["counts"]["published"] == 1
    assert scheduler_section["dead_letter"] == 0


def test_dead_letter_counts_and_alert(components):
    outbox, ledger, scheduler = components
    calls = []
    scheduler.schedule(observation(), RECIPIENT, now=NOW)
    scheduler.advance(
        publish=tracked(calls), allowed_providers={"synthetic-retry"},
        policy=AdmissionDecision({RECIPIENT}, {RECIPIENT}), now=NOW,
        observed_clock=CLOCK,
    )
    assert calls == []
    health = delivery_health_report(
        outbox=outbox, ledger=ledger, scheduler=scheduler, now=NOW,
    )
    assert health["metrics"]["retry_scheduler"]["dead_letter"] == 1
    names = {alert["alert"] for alert in health["alerts"]}
    assert names == {"dead_letter_rows"}
    alert = health["alerts"][0]
    assert alert["severity"] == "critical"
    assert alert["observed"] == 1


def test_stale_pending_receipt_alert_and_unconfirmed(components):
    _outbox, ledger, _scheduler = components
    ledger.register(MESSAGE, RECIPIENT, DIGEST, now=NOW - 500)
    report = collect_delivery_metrics(ledger=ledger, now=NOW)
    alerts = evaluate_operational_alerts(report)
    assert [alert["alert"] for alert in alerts] == ["stale_pending_receipts"]
    assert alerts[0]["observed"] == 500
    assert alerts[0]["threshold"] == 240
    ledger.mark_unconfirmed(MESSAGE, RECIPIENT)
    alerts = evaluate_operational_alerts(collect_delivery_metrics(ledger=ledger, now=NOW))
    names = {alert["alert"] for alert in alerts}
    assert "unconfirmed_deliveries" in names
    assert "stale_pending_receipts" in names  # still outstanding


def test_young_pending_receipt_does_not_alert(components):
    _outbox, ledger, _scheduler = components
    ledger.register(MESSAGE, RECIPIENT, DIGEST, now=NOW - 60)
    assert evaluate_operational_alerts(
        collect_delivery_metrics(ledger=ledger, now=NOW)
    ) == []


def test_outbox_backlog_and_quarantine_alerts(components, monkeypatch):
    outbox, _ledger, _scheduler = components
    for index in range(3):
        outbox.enqueue(observation(f"row-{index}"))
    report = collect_delivery_metrics(outbox=outbox, now=NOW)
    alerts = evaluate_operational_alerts(report, outbox_pending_threshold=2)
    assert [alert["alert"] for alert in alerts] == ["outbox_backlog"]
    # Quarantine a stale row through a real drain; the fixed clock keeps the
    # three fresh rows deliverable while the 2020 row is quarantined.
    outbox.enqueue(observation("stale", when="2020-01-01T00:00:00Z"))
    outbox.drain(lambda line: None, allowed_providers={"synthetic-retry"}, now=CLOCK)
    report = collect_delivery_metrics(outbox=outbox, now=NOW)
    assert report["outbox"]["quarantined"] == 1
    assert report["outbox"]["pending"] == 0  # drained valid rows are delivered
    alerts = evaluate_operational_alerts(report, outbox_pending_threshold=100)
    assert [alert["alert"] for alert in alerts] == ["quarantined_observations"]


def test_disabled_scheduler_reports_enabled_false(tmp_path):
    ledger = RecipientReceiptLedger(str(tmp_path / "ledger.sqlite3"))
    scheduler = ReceiptRetryScheduler(str(tmp_path / "retry.sqlite3"), ledger)
    report = collect_delivery_metrics(scheduler=scheduler, now=NOW)
    assert report["retry_scheduler"] == {"configured": True, "enabled": False}
    assert evaluate_operational_alerts(report) == []


def test_invalid_clock_and_thresholds(components):
    outbox, ledger, scheduler = components
    with pytest.raises(ValueError, match="invalid_metrics_clock"):
        collect_delivery_metrics(now="now")
    with pytest.raises(ValueError, match="invalid_metrics_clock"):
        collect_delivery_metrics(now=-1)
    report = collect_delivery_metrics(ledger=ledger, now=NOW)
    with pytest.raises(ValueError, match="invalid_unconfirmed_threshold"):
        evaluate_operational_alerts(report, unconfirmed_threshold=-1)
    with pytest.raises(ValueError, match="invalid_metrics_report"):
        evaluate_operational_alerts({"schema": "other"})
    with pytest.raises(ValueError, match="invalid_metrics_report"):
        evaluate_operational_alerts("not-a-report")


def test_invalid_components_rejected():
    with pytest.raises(TypeError, match="invalid_outbox_component"):
        collect_delivery_metrics(outbox=object(), now=NOW)
    with pytest.raises(TypeError, match="invalid_receipt_ledger_component"):
        collect_delivery_metrics(ledger=object(), now=NOW)
    with pytest.raises(TypeError, match="invalid_retry_scheduler_component"):
        collect_delivery_metrics(scheduler=object(), now=NOW)


def test_future_created_rows_clamp_to_zero_age(components):
    _outbox, ledger, _scheduler = components
    ledger.register(MESSAGE, RECIPIENT, DIGEST, now=NOW + 30)  # clock skew
    report = collect_delivery_metrics(ledger=ledger, now=NOW)
    assert report["receipt_ledger"]["oldest_outstanding_age_seconds"] == 0
    assert evaluate_operational_alerts(report) == []
