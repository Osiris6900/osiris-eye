"""Durable outbox failure/restart tests."""
import hashlib
import json

import pytest

from osiris_api.observation_outbox import ObservationOutbox


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
        outbox.drain(broken)
    restarted = ObservationOutbox(path)
    assert restarted.pending_count() == 1
    lines = []
    assert restarted.drain(lines.append) == 1
    assert json.loads(lines[0])["instrument"] == "BTCUSDT"
    assert restarted.pending_count() == 0
    assert restarted.drain(lines.append) == 0


def test_invalid_observation_not_queued(tmp_path):
    outbox = ObservationOutbox(str(tmp_path / "outbox.sqlite3"))
    bad = observation()
    bad["payload"] = "tampered"
    with pytest.raises(ValueError, match="payload_hash_mismatch"):
        outbox.enqueue(bad)
    assert outbox.pending_count() == 0


def test_outbox_requires_disk(tmp_path):
    with pytest.raises(ValueError, match="durable_file_path_required"):
        ObservationOutbox(":memory:")
