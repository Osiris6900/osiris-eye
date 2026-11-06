"""Tests for the own-sensor watch layer."""
import json
from datetime import datetime, timezone

import pytest

from osiris_api.data_store import ResearchStore
from osiris_api.own_sensor_watch import (
    collect_sensor_observations,
    watch_and_ingest_sensors,
)
from osiris_api.p2p_ingestion import DATASET, ENABLE_ENV

NOW = datetime(2026, 11, 6, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path):
    return ResearchStore(str(tmp_path / "sensors.sqlite3"))


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv(ENABLE_ENV, "1")


def _seismic():
    return {"value": 0.42, "x": 0.1, "y": -0.4, "z": 9.8}


def _magnetic():
    return {"value": 47.5}


def _radio():
    return {"frequency_mhz": 137.62, "strength_db": -68.0}


# --- collection -----------------------------------------------------------------

def test_collect_builds_contract_observations():
    result = collect_sensor_observations(
        readers={"seismic": _seismic, "magnetic": _magnetic, "radio": _radio},
        node_label="alpha", now=NOW,
    )
    assert result["errors"] == []
    assert result["live_execution"] is False
    observations = result["observations"]
    assert len(observations) == 3
    first = observations[0]
    assert first["schema"] == "osiris.observation.v1"
    assert first["observed_at"] == NOW.isoformat()
    assert first["provider"] == "node-self:alpha"
    instruments = {obs["instrument"] for obs in observations}
    assert instruments == {"SENSE:MAGNETIC:alpha", "SENSE:RADIO:alpha",
                           "SENSE:SEISMIC:alpha"}
    event = json.loads(first["payload"])
    assert event["kind"] == "sensor_magnetic"
    assert event["measurement"] == "self_measured"
    assert event["instrument_grade"] == "citizen_sensor"
    assert event["market_channel"] == "infrastructure"
    assert event["event_time"] == NOW.isoformat()
    assert "precision_note" in event


def test_collect_seismic_keeps_axes():
    result = collect_sensor_observations(
        readers={"seismic": _seismic}, node_label="alpha", now=NOW,
    )
    event = json.loads(result["observations"][0]["payload"])
    assert event["reading"] == {"value": 0.42, "x": 0.1, "y": -0.4, "z": 9.8}
    assert event["unit"] == "m/s^2"


def test_collect_requires_readers():
    with pytest.raises(ValueError, match="no_sensor_readers"):
        collect_sensor_observations(readers={}, node_label="alpha", now=NOW)


def test_collect_rejects_unknown_kind():
    with pytest.raises(ValueError, match="unknown_sensor_kind:lidar"):
        collect_sensor_observations(readers={"lidar": _seismic},
                                    node_label="alpha", now=NOW)


def test_collect_rejects_too_many_readers():
    readers = {kind: _seismic for kind in
               ("seismic", "magnetic", "pressure", "radio")}
    readers.update({f"extra{index}": _seismic for index in range(5)})
    with pytest.raises(ValueError, match="too_many_sensor_readers"):
        collect_sensor_observations(readers=readers, node_label="alpha", now=NOW)


def test_collect_rejects_non_callable_reader():
    with pytest.raises(ValueError, match="invalid_sensor_reader:seismic"):
        collect_sensor_observations(readers={"seismic": 42},
                                    node_label="alpha", now=NOW)


def test_collect_validates_node_label():
    for bad in ("", "x" * 33, "node 1", "node!"):
        with pytest.raises(ValueError, match="invalid_node_label"):
            collect_sensor_observations(readers={"seismic": _seismic},
                                        node_label=bad, now=NOW)


def test_reader_failure_isolated():
    def broken():
        raise RuntimeError("driver unplugged")

    result = collect_sensor_observations(
        readers={"seismic": broken, "magnetic": _magnetic},
        node_label="alpha", now=NOW,
    )
    assert len(result["observations"]) == 1
    assert result["errors"] == [{"sensor": "seismic", "error": "driver unplugged"}]


def test_out_of_bounds_reading_rejected():
    result = collect_sensor_observations(
        readers={"seismic": lambda: {"value": 100.0}}, node_label="alpha", now=NOW,
    )
    assert result["observations"] == []
    assert result["errors"][0]["error"] == "reading_out_of_bounds:seismic"


def test_invalid_reading_rejected():
    for raw in ({"value": "abc"}, {"wrong_field": 1.0}, "not-a-dict",
                {"value": float("nan")}, {"value": 1.0, "x": 999.0}):
        result = collect_sensor_observations(
            readers={"seismic": lambda raw=raw: raw}, node_label="alpha", now=NOW,
        )
        assert result["observations"] == []
        assert result["errors"][0]["error"].startswith(
            ("invalid_sensor_reading", "reading_out_of_bounds"))


# --- ingestion cycle --------------------------------------------------------------

def test_watch_and_ingest_end_to_end(store, enabled):
    readers = {"seismic": _seismic, "magnetic": _magnetic}
    first = watch_and_ingest_sensors(
        store, readers=readers, node_label="alpha", now=NOW,
    )
    assert first["cycle"] == "own_sensor_watch"
    assert first["fetched"] == 2
    assert first["ingested"] == 2
    assert first["rejected"] == []
    assert first["live_execution"] is False
    assert first["broker_order_submitted"] is False
    rows = store.list(DATASET, limit=10)
    assert len(rows) == 2
    second = watch_and_ingest_sensors(
        store, readers=readers, node_label="alpha", now=NOW,
    )
    assert second["ingested"] == 0
    assert second["duplicates_skipped"] == 2


def test_watch_rejects_unlisted_provider(store, enabled):
    report = watch_and_ingest_sensors(
        store, readers={"seismic": _seismic}, node_label="alpha",
        allowed_providers={"node-self:someone-else"}, now=NOW,
    )
    assert report["ingested"] == 0
    assert len(report["rejected"]) == 1
    assert store.list(DATASET, limit=10) == []


def test_watch_kill_switch_fail_closed(store):
    report = watch_and_ingest_sensors(
        store, readers={"seismic": _seismic}, node_label="alpha", now=NOW,
    )
    assert report["ingested"] == 0
    assert report["rejected"][0]["reason"] == "ingestion_disabled_kill_switch"
    assert store.list(DATASET, limit=10) == []


def test_watch_reports_sensor_errors(store, enabled):
    def broken():
        raise OSError("usb detached")

    report = watch_and_ingest_sensors(
        store, readers={"seismic": broken, "magnetic": _magnetic},
        node_label="alpha", now=NOW,
    )
    assert report["ingested"] == 1
    assert report["fetched"] == 1
    assert report["sensor_errors"][0]["sensor"] == "seismic"
