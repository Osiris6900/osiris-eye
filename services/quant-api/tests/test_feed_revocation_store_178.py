"""Durable optional feed-revocation regression cases (T07/T09/T17)."""
from __future__ import annotations

from datetime import datetime, timezone
import sqlite3

import pytest

from osiris_api import authorized_feeds
from osiris_api.authorized_feeds import (
    FeedAdapterRejection, ingest_adapter_ticks,
    is_adapter_suspended, restore_adapter, suspend_adapter, suspended_adapters,
)
from osiris_api.feed_revocation_store import ENV

NOW = datetime(2026, 11, 8, 12, 0, 0, tzinfo=timezone.utc)


class SpyAdapter:
    name = "synthetic-authorized"

    def __init__(self):
        self.called = False

    def fetch(self, *, now):
        self.called = True
        return []


@pytest.fixture
def durable(tmp_path, monkeypatch):
    path = tmp_path / "revocation-state.sqlite3"
    monkeypatch.setenv(ENV, str(path))
    return path


def test_suspension_survives_process_local_state_reset(durable):
    assert is_adapter_suspended("synthetic-authorized") is False
    suspend_adapter("synthetic-authorized")
    assert durable.exists()
    assert suspended_adapters() == frozenset({"synthetic-authorized"})
    authorized_feeds._SUSPENDED_ADAPTERS.clear()  # represents process-local reset
    assert is_adapter_suspended("synthetic-authorized") is True

    spy = SpyAdapter()
    with pytest.raises(FeedAdapterRejection, match="adapter_suspended"):
        ingest_adapter_ticks(spy, instruments=["BTC-USD"], now=NOW)
    assert spy.called is False

    restore_adapter("synthetic-authorized")
    assert not is_adapter_suspended("synthetic-authorized")
    ingest_adapter_ticks(spy, instruments=["BTC-USD"], now=NOW)
    assert spy.called is True
    with sqlite3.connect(durable) as conn:
        actions = [row[0] for row in conn.execute(
            "SELECT action FROM feed_revocation_audit ORDER BY event_id"
        )]
        assert actions == ["suspend", "restore"]


@pytest.mark.parametrize("configured", ["", ":memory:", "relative/revocations.db"])
def test_invalid_explicit_store_never_falls_back_to_memory(configured, monkeypatch):
    monkeypatch.setenv(ENV, configured)
    spy = SpyAdapter()
    with pytest.raises(
        FeedAdapterRejection, match="adapter_revocation_store_unavailable",
    ):
        ingest_adapter_ticks(spy, instruments=["BTC-USD"], now=NOW)
    assert spy.called is False


def test_corrupt_persisted_state_refuses_fetch(durable):
    durable.write_text("not-a-sqlite-db", encoding="utf-8")
    spy = SpyAdapter()
    with pytest.raises(FeedAdapterRejection, match="adapter_revocation_store_unavailable"):
        ingest_adapter_ticks(spy, instruments=["BTC-USD"], now=NOW)
    assert spy.called is False
    with pytest.raises(FeedAdapterRejection, match="adapter_revocation_store_unavailable"):
        suspend_adapter("synthetic-authorized")
    with pytest.raises(FeedAdapterRejection, match="adapter_revocation_store_unavailable"):
        restore_adapter("synthetic-authorized")


def test_unknown_adapter_cannot_be_suspended(durable):
    with pytest.raises(FeedAdapterRejection, match="adapter_not_registered"):
        suspend_adapter("unregistered-vendor")
    assert suspended_adapters() == frozenset()


def test_explicit_durable_db_does_not_trust_local_legacy_state(durable):
    authorized_feeds._SUSPENDED_ADAPTERS.add("synthetic-authorized")
    try:
        assert is_adapter_suspended("synthetic-authorized") is False
        suspend_adapter("synthetic-authorized")
        assert is_adapter_suspended("synthetic-authorized") is True
    finally:
        authorized_feeds._SUSPENDED_ADAPTERS.discard("synthetic-authorized")


def test_unconfigured_fallback_is_explicitly_non_durable(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    authorized_feeds._SUSPENDED_ADAPTERS.discard("synthetic-authorized")
    try:
        suspend_adapter("synthetic-authorized")
        assert is_adapter_suspended("synthetic-authorized")
        restore_adapter("synthetic-authorized")
        assert not is_adapter_suspended("synthetic-authorized")
    finally:
        authorized_feeds._SUSPENDED_ADAPTERS.discard("synthetic-authorized")
