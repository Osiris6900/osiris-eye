"""Visibility and rollback boundaries for related research-store writes."""
import pytest

from osiris_api.data_store import ResearchStore


def record(key):
    return {
        "dataset": "prices",
        "record_key": key,
        "source": "synthetic",
        "event_time": "2026-11-06T04:00:00Z",
        "available_at": "2026-11-06T04:00:01Z",
        "payload": {"close": 42},
    }


def test_related_writes_become_visible_together_after_commit(tmp_path):
    path = str(tmp_path / "research.sqlite3")
    writer = ResearchStore(path)
    reader = ResearchStore(path)

    with writer.transaction() as connection:
        first = writer._put_in_transaction(connection, **record("first"))
        writer._put_in_transaction(connection, **record("second"))
        assert first["event_time"] == "2026-11-06T04:00:00+00:00"
        assert first["payload"] == {"close": 42}
        assert reader.list("prices") == []

    assert {row["record_key"] for row in reader.list("prices")} == {"first", "second"}


def test_failure_rolls_back_all_related_writes_and_releases_writer(tmp_path):
    path = str(tmp_path / "research.sqlite3")
    store = ResearchStore(path)

    with pytest.raises(RuntimeError, match="related write failed"):
        with store.transaction() as connection:
            store._put_in_transaction(connection, **record("not-committed"))
            raise RuntimeError("related write failed")

    reopened = ResearchStore(path)
    assert reopened.list("prices") == []
    reopened.put(**record("retry"))
    assert [row["record_key"] for row in reopened.list("prices")] == ["retry"]


def test_duplicate_keeps_prior_data_and_rolls_back_new_related_write(tmp_path):
    store = ResearchStore(str(tmp_path / "research.sqlite3"))
    store.put(**record("existing"))

    with pytest.raises(ValueError, match="duplicate observation key"):
        with store.transaction() as connection:
            store._put_in_transaction(connection, **record("new"))
            store._put_in_transaction(connection, **record("existing"))

    assert [row["record_key"] for row in store.list("prices")] == ["existing"]


def test_validation_failure_rolls_back_preceding_related_write(tmp_path):
    store = ResearchStore(str(tmp_path / "research.sqlite3"))
    invalid = {**record("invalid"), "available_at": "2026-11-06T03:59:59Z"}

    with pytest.raises(ValueError, match="available_at cannot precede event_time"):
        with store.transaction() as connection:
            store._put_in_transaction(connection, **record("valid"))
            store._put_in_transaction(connection, **invalid)

    assert store.list("prices") == []
