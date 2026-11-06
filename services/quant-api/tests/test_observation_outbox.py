"""Durable outbox failure/restart tests."""
import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from osiris_api.observation_outbox import ObservationOutbox
from osiris_api.p2p_ingestion import ENABLE_ENV, IngestionRejection

NOW = datetime(2026, 11, 6, 4, tzinfo=UTC)
PROVIDERS = frozenset({"binance-public"})


@pytest.fixture(autouse=True)
def enable_ingestion(monkeypatch):
    monkeypatch.setenv(ENABLE_ENV, "1")


def observation():
    payload = '{"value":42}'
    return {
        "schema": "osiris.observation.v1",
        "provider": "binance-public",
        "instrument": "BTCUSDT",
        "observed_at": "2026-11-06T04:00:00Z",
        "payload": payload,
        "payload_sha256": hashlib.sha256(payload.encode()).hexdigest(),
    }


def test_writer_failure_survives_reopen_and_retry(tmp_path):
    path = str(tmp_path / "outbox.sqlite3")
    outbox = ObservationOutbox(path)
    assert outbox.enqueue(observation())
    assert not outbox.enqueue(observation())
    def broken(_):
        raise BrokenPipeError("node disconnected")
    with pytest.raises(BrokenPipeError):
        outbox.drain(broken, allowed_providers=PROVIDERS, now=NOW)
    restarted = ObservationOutbox(path)
    assert restarted.pending_count() == 1
    lines = []
    assert restarted.drain(lines.append, allowed_providers=PROVIDERS, now=NOW) == 1
    assert json.loads(lines[0])["instrument"] == "BTCUSDT"
    assert restarted.pending_count() == 0
    assert restarted.drain(lines.append, allowed_providers=PROVIDERS, now=NOW) == 0


def test_invalid_observation_not_queued(tmp_path):
    outbox = ObservationOutbox(str(tmp_path / "outbox.sqlite3"))
    bad = observation()
    bad["payload"] = "tampered"
    with pytest.raises(ValueError, match="payload_hash_mismatch"):
        outbox.enqueue(bad)
    assert outbox.pending_count() == 0


def test_invalid_payload_encoding_not_queued(tmp_path):
    outbox = ObservationOutbox(str(tmp_path / "outbox.sqlite3"))
    bad = {**observation(), "payload": "\ud800"}
    with pytest.raises(IngestionRejection, match="invalid_payload_encoding"):
        outbox.enqueue(bad)
    assert outbox.pending_count() == 0


def test_outbox_requires_disk(tmp_path):
    with pytest.raises(ValueError, match="durable_file_path_required"):
        ObservationOutbox(":memory:")


@pytest.mark.parametrize("populated", [False, True])
def test_disabled_ingestion_preserves_pending_records(tmp_path, monkeypatch, populated):
    outbox = ObservationOutbox(str(tmp_path / "outbox.sqlite3"))
    if populated:
        outbox.enqueue(observation())
    monkeypatch.delenv(ENABLE_ENV)
    lines = []
    with pytest.raises(IngestionRejection, match="ingestion_disabled_kill_switch"):
        outbox.drain(lines.append, allowed_providers=PROVIDERS, now=NOW)
    assert lines == []
    assert outbox.pending_count() == int(populated)
    assert outbox.rejected_count() == 0
    monkeypatch.setenv(ENABLE_ENV, "1")
    assert outbox.drain(lines.append, allowed_providers=PROVIDERS, now=NOW) == int(populated)


@pytest.mark.parametrize("providers", [[], ["", "  "]])
@pytest.mark.parametrize("populated", [False, True])
def test_missing_provider_configuration_preserves_queue(tmp_path, providers, populated):
    outbox = ObservationOutbox(str(tmp_path / "outbox.sqlite3"))
    if populated:
        outbox.enqueue(observation())
    lines = []
    with pytest.raises(IngestionRejection, match="no_authorized_providers"):
        outbox.drain(lines.append, allowed_providers=providers, now=NOW)
    assert lines == []
    assert outbox.pending_count() == int(populated)
    assert outbox.rejected_count() == 0


def test_switch_rechecked_before_every_write(tmp_path, monkeypatch):
    outbox = ObservationOutbox(str(tmp_path / "outbox.sqlite3"))
    outbox.enqueue(observation())
    second = {**observation(), "instrument": "ETHUSDT"}
    outbox.enqueue(second)
    lines = []

    def stop_after_first(line):
        lines.append(line)
        monkeypatch.delenv(ENABLE_ENV)

    assert outbox.drain(stop_after_first, allowed_providers=PROVIDERS, now=NOW) == 1
    assert len(lines) == 1
    assert outbox.pending_count() == 1
    assert outbox.rejected_count() == 0


def test_retry_rechecks_wall_clock_after_a_slow_write(tmp_path, monkeypatch):
    from osiris_api import p2p_ingestion

    current = [NOW]

    class DeliveryClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return current[0].astimezone(tz)

    monkeypatch.setattr(p2p_ingestion, "datetime", DeliveryClock)
    outbox = ObservationOutbox(str(tmp_path / "outbox.sqlite3"))
    outbox.enqueue(observation())
    outbox.enqueue({**observation(), "instrument": "EXPIRES_DURING_WRITE"})
    lines = []

    def slow_writer(line):
        lines.append(line)
        current[0] += timedelta(minutes=6)

    assert outbox.drain(slow_writer, allowed_providers=PROVIDERS) == 1
    assert [json.loads(line)["instrument"] for line in lines] == ["BTCUSDT"]
    assert outbox.pending_count() == 0
    assert outbox.rejected_count() == 1


@pytest.mark.parametrize(("change", "reason"), [
    ({"observed_at": (NOW - timedelta(minutes=6)).isoformat()}, "observation_stale"),
    ({"observed_at": (NOW + timedelta(minutes=2)).isoformat()}, "observation_future_dated"),
    ({"provider": "removed-provider"}, "provider_not_authorized"),
    ({"payload": "tampered"}, "payload_hash_mismatch"),
    ({"payload": "\ud800"}, "invalid_payload_encoding"),
    ({"schema": "unexpected"}, "unsupported_schema"),
])
def test_invalid_retry_quarantined_and_does_not_block_healthy_row(tmp_path, change, reason):
    path = str(tmp_path / "outbox.sqlite3")
    outbox = ObservationOutbox(path)
    outbox.enqueue(observation())
    # Simulate policy change or disk tampering after contract-valid enqueue.
    invalid = {**observation(), **change}
    with sqlite3.connect(path) as db:
        db.execute("UPDATE pending_observations SET observation_json=?", (json.dumps(invalid),))
    outbox.enqueue({**observation(), "instrument": "GOOD"})
    lines = []
    assert outbox.drain(lines.append, allowed_providers=PROVIDERS, now=NOW) == 1
    assert [json.loads(line)["instrument"] for line in lines] == ["GOOD"]
    assert outbox.pending_count() == 0
    assert outbox.rejected_count() == 1
    restarted = ObservationOutbox(path)
    assert restarted.rejected_count() == 1
    assert restarted.drain(lines.append, allowed_providers=PROVIDERS, now=NOW) == 0
    with sqlite3.connect(path) as db:
        assert db.execute(
            "SELECT rejected_reason FROM pending_observations WHERE rejected_reason IS NOT NULL"
        ).fetchone()[0] == reason


@pytest.mark.parametrize(("corrupted", "reason"), [
    ("not json", "invalid_observation_json"),
    ("[]", "observation_must_be_object"),
    ("null", "observation_must_be_object"),
])
def test_corrupt_json_quarantined_without_blocking_good_rows(tmp_path, corrupted, reason):
    path = str(tmp_path / "outbox.sqlite3")
    outbox = ObservationOutbox(path)
    outbox.enqueue(observation())
    with sqlite3.connect(path) as db:
        db.execute("UPDATE pending_observations SET observation_json=?", (corrupted,))
    outbox.enqueue({**observation(), "instrument": "GOOD"})
    lines = []
    assert outbox.drain(lines.append, allowed_providers=PROVIDERS, now=NOW) == 1
    assert json.loads(lines[0])["instrument"] == "GOOD"
    assert outbox.rejected_count() == 1
    with sqlite3.connect(path) as db:
        assert db.execute(
            "SELECT rejected_reason FROM pending_observations WHERE rejected_reason IS NOT NULL"
        ).fetchone()[0] == reason


def test_legacy_schema_migrates_without_losing_pending_or_delivered_rows(tmp_path):
    path = str(tmp_path / "outbox.sqlite3")
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TABLE pending_observations (
            observation_key TEXT PRIMARY KEY,
            observation_json TEXT NOT NULL,
            delivered INTEGER NOT NULL DEFAULT 0
        )""")
        db.executemany(
            "INSERT INTO pending_observations VALUES (?, ?, ?)",
            [("pending", json.dumps(observation()), 0),
             ("delivered", json.dumps({**observation(), "instrument": "ALREADY_SENT"}), 1)],
        )
    migrated = ObservationOutbox(path)
    assert migrated.pending_count() == 1
    assert migrated.rejected_count() == 0
    lines = []
    assert migrated.drain(lines.append, allowed_providers=PROVIDERS, now=NOW) == 1
    assert [json.loads(line)["instrument"] for line in lines] == ["BTCUSDT"]
    restarted = ObservationOutbox(path)
    assert restarted.pending_count() == 0
    assert restarted.drain(lines.append, allowed_providers=PROVIDERS, now=NOW) == 0


@pytest.mark.parametrize("limit", [True, False, 1.0, 10.5, 0, -1, 1001, "1", None])
def test_invalid_limit_rejected_before_delivery(tmp_path, limit):
    outbox = ObservationOutbox(str(tmp_path / "outbox.sqlite3"))
    outbox.enqueue(observation())
    lines = []
    with pytest.raises(ValueError, match="invalid_drain_limit"):
        outbox.drain(lines.append, allowed_providers=PROVIDERS, now=NOW, limit=limit)
    assert lines == []
    assert outbox.pending_count() == 1


def test_limit_bounds_examined_rows_including_quarantine(tmp_path):
    outbox = ObservationOutbox(str(tmp_path / "outbox.sqlite3"))
    outbox.enqueue({**observation(), "provider": "removed-provider"})
    outbox.enqueue(observation())
    lines = []
    assert outbox.drain(lines.append, allowed_providers=PROVIDERS, now=NOW, limit=1) == 0
    assert outbox.pending_count() == 1
    assert outbox.rejected_count() == 1
    assert outbox.drain(lines.append, allowed_providers=PROVIDERS, now=NOW, limit=1) == 1
    assert outbox.pending_count() == 0
