"""Store and pending delivery must recover together across failures and process death."""
import hashlib
import json
import sqlite3
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from osiris_api.data_store import ResearchStore
from osiris_api.observation_outbox import ObservationOutbox
from osiris_api.p2p_ingestion import (
    DATASET,
    ENABLE_ENV as INGEST_ENV,
    IngestionRejection,
    ingest_p2p_observation,
)
from osiris_api.unified_observation_bridge import (
    ENABLE_ENV,
    UnifiedBridgeRefused,
    broadcast_unified_cycle,
)

NOW = datetime(2026, 11, 6, 4, tzinfo=timezone.utc)
PROVIDERS = {"binance-public"}


def observation(*, provider="binance-public", instrument="BTCUSDT"):
    payload = '{"value":42}'
    return {
        "schema": "osiris.observation.v1",
        "provider": provider,
        "instrument": instrument,
        "observed_at": NOW.isoformat(),
        "payload": payload,
        "payload_sha256": hashlib.sha256(payload.encode()).hexdigest(),
    }


def collectors(observations=()):
    return {
        "market": (
            lambda **kwargs: {"observations": list(observations), "errors": []},
            frozenset(PROVIDERS),
        ),
    }


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    monkeypatch.setenv(INGEST_ENV, "1")
    monkeypatch.setenv(ENABLE_ENV, "1")


@pytest.fixture
def shared(tmp_path):
    path = str(tmp_path / "research.sqlite3")
    return ResearchStore(path), ObservationOutbox(path)


def ingest(store, outbox, value=None):
    return ingest_p2p_observation(
        store,
        observation() if value is None else value,
        allowed_providers=PROVIDERS,
        now=NOW,
        outbox=outbox,
    )


def test_writer_only_runs_after_observation_and_queue_are_committed(shared):
    store, outbox = shared
    lines = []

    def writer(line):
        # A separate connection cannot see an uncommitted insertion.
        with sqlite3.connect(store.database_path) as db:
            assert db.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 1
            assert db.execute(
                "SELECT delivered FROM pending_observations"
            ).fetchall() == [(0,)]
        lines.append(line)

    result = broadcast_unified_cycle(
        writer, store=store, outbox=outbox,
        collectors=collectors([observation()]), now=NOW,
    )
    assert result["ingested"] == 1
    assert result["broadcast"] == 1
    assert json.loads(lines[0]) == observation()
    assert len(store.list(DATASET)) == 1
    assert outbox.pending_count() == 0


def test_queue_insert_failure_rolls_back_observation(shared):
    store, outbox = shared
    with sqlite3.connect(store.database_path) as db:
        db.execute("""
            CREATE TRIGGER fail_queue_insert BEFORE INSERT ON pending_observations
            BEGIN SELECT RAISE(ABORT, 'queue_insert_failed'); END
        """)
    with pytest.raises((sqlite3.IntegrityError, IngestionRejection), match="queue_insert_failed"):
        ingest(store, outbox)
    assert ResearchStore(store.database_path).list(DATASET) == []
    assert ObservationOutbox(outbox.path).pending_count() == 0


def test_observation_insert_failure_leaves_no_queued_delivery(shared):
    store, outbox = shared
    with sqlite3.connect(store.database_path) as db:
        db.execute("""
            CREATE TRIGGER fail_observation_insert BEFORE INSERT ON observations
            BEGIN SELECT RAISE(ABORT, 'store_insert_failed'); END
        """)
    with pytest.raises(IngestionRejection, match="store_rejected"):
        ingest(store, outbox)
    assert store.list(DATASET) == []
    assert outbox.pending_count() == 0


_CRASH_CHILD = r"""
import json
import os
import sys
from datetime import datetime
from osiris_api.data_store import ResearchStore
from osiris_api.observation_outbox import ObservationOutbox
from osiris_api.p2p_ingestion import ingest_p2p_observation

path, stage, encoded = sys.argv[1:]
store = ResearchStore(path)
outbox = ObservationOutbox(path)
original = ObservationOutbox._enqueue_in_transaction

if stage != 'after-commit':
    def crash(self, connection, value):
        if stage == 'after-enqueue':
            original(self, connection, value)
        os._exit(71)
    ObservationOutbox._enqueue_in_transaction = crash

value = json.loads(encoded)
ingest_p2p_observation(
    store, value, allowed_providers={'binance-public'}, outbox=outbox,
    now=datetime.fromisoformat(value['observed_at']),
)
os._exit(72)
"""


@pytest.mark.parametrize("stage", ["before-enqueue", "after-enqueue"])
def test_process_death_before_commit_recovers_neither_half(tmp_path, stage):
    path = str(tmp_path / "research.sqlite3")
    child = subprocess.run(
        [sys.executable, "-c", _CRASH_CHILD, path, stage, json.dumps(observation())],
        capture_output=True, text=True, timeout=15, check=False,
    )
    assert child.returncode == 71, child.stderr
    assert ResearchStore(path).list(DATASET) == []
    assert ObservationOutbox(path).pending_count() == 0


def test_process_death_after_commit_retains_retry_without_reinsertion(tmp_path):
    path = str(tmp_path / "research.sqlite3")
    child = subprocess.run(
        [sys.executable, "-c", _CRASH_CHILD, path, "after-commit", json.dumps(observation())],
        capture_output=True, text=True, timeout=15, check=False,
    )
    assert child.returncode == 72, child.stderr
    store, outbox = ResearchStore(path), ObservationOutbox(path)
    assert len(store.list(DATASET)) == 1
    assert outbox.pending_count() == 1
    lines = []
    result = broadcast_unified_cycle(
        lines.append, store=store, outbox=outbox,
        collectors=collectors([observation()]), now=NOW,
    )
    assert result["broadcast"] == 1
    assert result["duplicates_skipped"] == 1
    assert result["ingested"] == 0
    assert outbox.pending_count() == 0
    assert len(store.list(DATASET)) == 1
    assert [json.loads(line) for line in lines] == [observation()]


def test_concurrent_duplicate_ingestion_creates_one_observation_and_delivery(shared):
    store, outbox = shared
    ready = threading.Barrier(2)

    def attempt(_index):
        ready.wait(timeout=5)
        try:
            ingest(store, outbox)
        except IngestionRejection as exc:
            assert str(exc).startswith("store_rejected:duplicate")
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as pool:
        accepted = list(pool.map(attempt, range(2)))
    assert sorted(accepted) == [False, True]
    assert len(store.list(DATASET)) == 1
    assert outbox.pending_count() == 1
    with sqlite3.connect(store.database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM pending_observations").fetchone()[0] == 1


@pytest.mark.parametrize("state", ["delivered", "quarantined"])
def test_duplicate_ingestion_does_not_reactivate_completed_queue_entry(shared, state):
    store, outbox = shared
    ingest(store, outbox)
    retry_time = NOW if state == "delivered" else NOW + timedelta(seconds=301)
    outbox.drain(lambda _line: None, allowed_providers=PROVIDERS, now=retry_time)
    with pytest.raises(IngestionRejection, match="store_rejected:duplicate"):
        ingest(store, outbox)
    assert len(store.list(DATASET)) == 1
    assert outbox.pending_count() == 0
    assert outbox.rejected_count() == int(state == "quarantined")
    with sqlite3.connect(store.database_path) as db:
        assert db.execute("SELECT delivered FROM pending_observations").fetchall() == [
            (int(state == "delivered"),)
        ]


@pytest.mark.parametrize("state", ["pending", "delivered", "quarantined"])
def test_preexisting_outbox_identity_refuses_fresh_store_insert_without_reset(shared, state):
    store, outbox = shared
    # Older/manual queue use may have an identity with no research-store row.
    assert outbox.enqueue(observation())
    if state != "pending":
        retry_time = NOW if state == "delivered" else NOW + timedelta(seconds=301)
        outbox.drain(lambda _line: None, allowed_providers=PROVIDERS, now=retry_time)

    with pytest.raises(IngestionRejection, match="outbox_observation_conflict"):
        ingest(store, outbox)
    assert store.list(DATASET) == []
    assert outbox.pending_count() == int(state == "pending")
    assert outbox.rejected_count() == int(state == "quarantined")
    with sqlite3.connect(store.database_path) as db:
        assert db.execute("SELECT delivered FROM pending_observations").fetchall() == [
            (int(state == "delivered"),)
        ]


def test_outbox_key_collision_rolls_back_new_observation(shared):
    store, outbox = shared
    existing = observation(provider="a:b", instrument="c")
    conflicting = observation(provider="a", instrument="b:c")
    assert outbox.enqueue(existing)
    with pytest.raises((ValueError, IngestionRejection), match="outbox_observation_conflict"):
        ingest_p2p_observation(
            store, conflicting, allowed_providers={"a"}, now=NOW, outbox=outbox,
        )
    assert store.list(DATASET) == []
    assert outbox.pending_count() == 1
    with sqlite3.connect(store.database_path) as db:
        stored = db.execute("SELECT observation_json FROM pending_observations").fetchone()[0]
    assert json.loads(stored) == existing


def test_mismatched_bridge_configuration_refused_before_collecting_or_draining(tmp_path):
    store = ResearchStore(str(tmp_path / "research.sqlite3"))
    outbox = ObservationOutbox(str(tmp_path / "separate.sqlite3"))
    outbox.enqueue(observation())
    calls, lines = [], []

    def collect(**_kwargs):
        calls.append(True)
        return {"observations": [observation()], "errors": []}

    with pytest.raises(UnifiedBridgeRefused, match="outbox_store_database_mismatch"):
        broadcast_unified_cycle(
            lines.append, store=store, outbox=outbox,
            collectors={"market": (collect, frozenset(PROVIDERS))}, now=NOW,
        )
    assert calls == []
    assert lines == []
    assert store.list(DATASET) == []
    assert outbox.pending_count() == 1
    assert outbox.rejected_count() == 0


@pytest.mark.parametrize("database_path", ["separate", ":memory:"])
def test_direct_atomic_ingestion_requires_same_durable_database(tmp_path, database_path):
    store_path = str(tmp_path / "research.sqlite3") if database_path == "separate" else database_path
    store = ResearchStore(store_path)
    outbox = ObservationOutbox(str(tmp_path / "outbox.sqlite3"))
    with pytest.raises(IngestionRejection, match="outbox_store_database_mismatch"):
        ingest(store, outbox)
    assert outbox.pending_count() == 0
    if database_path != ":memory:":
        assert store.list(DATASET) == []


@pytest.mark.parametrize("alias", ["relative", "symlink"])
def test_shared_database_path_aliases_are_accepted(tmp_path, monkeypatch, alias):
    path = tmp_path / "research.sqlite3"
    store = ResearchStore(str(path))
    if alias == "relative":
        monkeypatch.chdir(tmp_path)
        outbox_path = "research.sqlite3"
    else:
        outbox_path = tmp_path / "linked.sqlite3"
        outbox_path.symlink_to(path)
        outbox_path = str(outbox_path)
    outbox = ObservationOutbox(outbox_path)
    assert ingest(store, outbox)["ingested"] is True
    assert len(store.list(DATASET)) == 1
    assert outbox.pending_count() == 1


@pytest.mark.parametrize("failure", ["tampered", "unauthorized", "disabled"])
def test_admission_failure_creates_neither_store_nor_queue_row(shared, monkeypatch, failure):
    store, outbox = shared
    value = observation()
    if failure == "tampered":
        value["payload"] = "tampered"
    elif failure == "unauthorized":
        value["provider"] = "unapproved"
    else:
        monkeypatch.setenv(INGEST_ENV, "0")
    with pytest.raises(IngestionRejection):
        ingest(store, outbox, value)
    assert store.list(DATASET) == []
    assert outbox.pending_count() == 0
