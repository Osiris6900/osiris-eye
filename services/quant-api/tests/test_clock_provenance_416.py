"""Server receive time, timestamp provenance, and host clock health.

Package 40. Runtime HTTP paths cannot supply ``now`` or a receive time.
Tests inject ``TrustedClock`` or the library ``now`` parameter. Host health
is parsed from local command output and never opens a socket.
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from osiris_api.main import app
from osiris_api.paper_execution import (
    LIVE_EXECUTION_ENABLED,
    initialize_paper_account,
    mark_to_market,
    snapshot,
    submit_paper_order,
)
from osiris_api.risk import RiskLimits

NOW = "2026-11-22T12:00:00+00:00"
FRESH = "2026-11-22T11:59:30+00:00"


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "clock.sqlite3"
    monkeypatch.setenv("OSIRIS_PAPER_EXECUTION_DB", str(path))
    monkeypatch.delenv("OSIRIS_ENABLE_LIVE_EXECUTION", raising=False)
    return path


def _init():
    initialize_paper_account("1000", RiskLimits(
        max_order_notional=10_000, max_position_notional=10_000,
        max_daily_loss=10_000, max_gross_exposure=10_000,
    ))


def _freeze(iso: str):
    from osiris_api.control_plane.clock import TrustedClock, install_runtime_clock

    instant = datetime.fromisoformat(iso)
    install_runtime_clock(TrustedClock(lambda: instant, source_id="osiris.test"))


def _order(key, **overrides):
    payload = dict(
        client_order_id=key, symbol="BTC", side="BUY", quantity="1", price="100",
        fee="0", observed_at=FRESH, now=NOW,
    )
    payload.update(overrides)
    return submit_paper_order(**payload)


def _provenance(event: str):
    import json
    for row in snapshot()["audit"]:
        if row["event"] == event:
            payload = json.loads(row["payload"])
            if "timestamp_provenance" in payload:
                return payload["timestamp_provenance"]
    return None


def test_http_cannot_freshen_an_old_print_by_supplying_now(db):
    client = TestClient(app)
    opened = client.post("/paper/execution/initialize", json={"initial_cash": "1000"})
    assert opened.status_code == 200
    forged = client.post("/paper/execution/orders", json={
        "client_order_id": "forged-now", "symbol": "BTC", "side": "BUY",
        "quantity": "1", "price": "100",
        "observed_at": "2020-01-01T00:00:00+00:00",
        "now": "2020-01-01T00:00:01+00:00",
    })
    assert forged.status_code == 422
    assert forged.json()["detail"] == "caller_clock_rejected"
    assert snapshot()["fill_count"] == 0
    assert LIVE_EXECUTION_ENABLED is False


def test_http_receive_time_comes_from_the_injected_server_clock(db):
    _freeze(NOW)
    client = TestClient(app)
    assert client.post("/paper/execution/initialize", json={"initial_cash": "1000"}).status_code == 200
    filled = client.post("/paper/execution/orders", json={
        "client_order_id": "server-clock", "symbol": "BTC", "side": "BUY",
        "quantity": "1", "price": "100", "observed_at": FRESH,
    })
    assert filled.status_code == 200
    assert filled.json()["status"] == "filled"
    assert filled.json()["live_execution"] is False
    provenance = _provenance("order_decided")
    assert provenance["source_event_time"].startswith("2026-11-22T11:59:30")
    assert provenance["server_receive_time"].startswith("2026-11-22T12:00:00")
    assert provenance["clock_source_id"] == "osiris.test"
    assert provenance["trusted"] is True
    assert provenance["flag"] is None
    stale = client.post("/paper/execution/orders", json={
        "client_order_id": "old-print", "symbol": "BTC", "side": "BUY",
        "quantity": "1", "price": "100",
        "observed_at": "2020-01-01T00:00:00+00:00",
        "received_at": NOW,
    })
    assert stale.status_code == 422
    assert stale.json()["detail"] == "caller_clock_rejected"
    assert snapshot()["fill_count"] == 1


@pytest.mark.parametrize("field", ["now", "received_at", "received_time", "server_receive_time", "receive_time"])
def test_mark_and_fill_routes_reject_caller_clock_fields(db, field):
    _freeze(NOW)
    client = TestClient(app)
    assert client.post("/paper/execution/initialize", json={"initial_cash": "1000"}).status_code == 200
    opened = client.post("/paper/execution/orders", json={
        "client_order_id": "resting", "symbol": "BTC", "side": "BUY",
        "quantity": "1", "price": "100", "observed_at": FRESH, "working": True,
    })
    assert opened.status_code == 200
    marked = client.post("/paper/execution/marks", json={
        "marks": {"BTC": "90"}, "observed_at": FRESH, field: NOW,
    })
    assert marked.status_code == 422
    assert marked.json()["detail"] == "caller_clock_rejected"
    filled = client.post("/paper/execution/orders/resting/fills", json={
        "client_fill_id": "fill-1", "quantity": "1", "observed_at": FRESH, field: NOW,
    })
    assert filled.status_code == 422
    assert filled.json()["detail"] == "caller_clock_rejected"
    assert snapshot()["fill_count"] == 0


def test_future_regression_and_implausible_latency_are_timestamp_untrusted(db):
    _init()
    assert _order("first")["status"] == "filled"
    future = _order("future", observed_at="2026-11-22T12:00:30+00:00")
    assert future["status"] == "risk_rejected"
    assert future["reason"] == "timestamp_untrusted"
    assert future["limits_evaluated"] is False
    backward = (datetime.fromisoformat(FRESH) - timedelta(seconds=30)).isoformat()
    regressed = _order("back", observed_at=backward)
    assert regressed["reason"] == "timestamp_untrusted"
    ancient = _order("ancient", observed_at="2026-11-20T12:00:00+00:00")
    assert ancient["reason"] == "timestamp_untrusted"
    assert snapshot()["fill_count"] == 1
    assert snapshot()["cash"] == "900"
    with pytest.raises(ValueError, match="timestamp_untrusted"):
        mark_to_market({"BTC": "90"}, observed_at=backward, now=NOW)
    assert snapshot()["marks"] == []


def test_degraded_clock_fails_closed_for_new_orders_and_is_on_the_ops_views(db):
    from osiris_api.admin_centre import operations_status
    from osiris_api.control_plane.clock import install_clock_health

    _init()
    degraded = {
        "status": "degraded", "reason": "sync_unknown", "clock_source_id": "unknown",
        "offset_ms": None, "uncertainty_ms": None, "orders_fail_closed": True,
        "live_trading_authorized": False, "network_probe": False,
    }
    install_clock_health(degraded)
    blocked = _order("halted")
    assert blocked["status"] == "risk_rejected"
    assert blocked["reason"] == "clock_degraded"
    assert blocked["limits_evaluated"] is False
    assert blocked["fill"] is None
    assert snapshot()["fill_count"] == 0
    assert snapshot()["cash"] == "1000"
    health = TestClient(app).get("/health").json()
    assert health["live_execution"] is False
    assert health["clock_health"]["status"] == "degraded"
    assert health["clock_health"]["orders_fail_closed"] is True
    assert health["clock_health"]["live_trading_authorized"] is False
    assert health["clock_health"]["network_probe"] is False
    ops = operations_status(
        {"generated_at": NOW, "ingestion_enabled": False}, None, paper_allowed=False,
    )
    assert ops["read_only"] is True
    assert ops["clock_health"]["status"] == "degraded"
    assert ops["live_trading_authorized"] is False


def test_assess_flags_future_regression_and_implausible_latency():
    from osiris_api.control_plane.clock import assess_timestamp_provenance

    receive = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)
    last = receive - timedelta(seconds=10)
    future = assess_timestamp_provenance(
        event_time=receive + timedelta(seconds=3), receive_time=receive, last_event_time=last,
    )
    assert future["flag"] == "timestamp_untrusted"
    assert "future_event" in future["causes"]
    backward = assess_timestamp_provenance(
        event_time=receive - timedelta(seconds=40), receive_time=receive, last_event_time=last,
    )
    assert backward["flag"] == "timestamp_untrusted"
    assert "event_regression" in backward["causes"]
    slow = assess_timestamp_provenance(
        event_time=receive - timedelta(days=3), receive_time=receive,
    )
    assert slow["flag"] == "timestamp_untrusted"
    assert slow["causes"] == ["implausible_latency"]
    historical = assess_timestamp_provenance(
        event_time=receive - timedelta(seconds=301),
        receive_time=receive - timedelta(seconds=301),
        last_event_time=receive,
        last_receive_time=receive,
    )
    assert historical["trusted"] is True
    fresh = assess_timestamp_provenance(
        event_time=receive - timedelta(seconds=1), receive_time=receive, last_event_time=last,
        clock_source_id="chrony", clock_offset_ms=0.2, clock_uncertainty_ms=0.4,
    )
    assert fresh["trusted"] is True
    assert fresh["flag"] is None
    assert fresh["clock_source_id"] == "chrony"
    assert fresh["clock_offset_ms"] == 0.2
    assert fresh["clock_uncertainty_ms"] == 0.4
    assert fresh["live_trading_authorized"] is False


def test_host_clock_health_parses_local_status_without_a_socket(monkeypatch):
    import socket
    from osiris_api.control_plane import clock as clock_mod

    def boom(*_args, **_kwargs):
        raise AssertionError("clock health opened a socket")

    monkeypatch.setattr(socket, "socket", boom)
    calls = []

    def runner(argv):
        calls.append(tuple(argv))
        if argv[:2] == ["chronyc", "-n"]:
            return 0, "\n".join((
                "System time     : 0.000010000 seconds fast of NTP time",
                "RMS offset      : 0.000020000 seconds",
                "Leap status     : Normal",
            ))
        return 127, ""

    health = clock_mod.read_host_clock_health(runner=runner, max_offset_ms=250)
    assert calls == [("chronyc", "-n", "tracking")]
    assert health["status"] == "healthy"
    assert health["clock_source_id"] == "chrony"
    assert health["offset_ms"] == pytest.approx(0.01)
    assert health["uncertainty_ms"] == pytest.approx(0.02)
    assert health["orders_fail_closed"] is False
    assert health["network_probe"] is False
    assert health["live_trading_authorized"] is False
    assert clock_mod.probe not in {runner}

    def unsynced(argv):
        if argv[0] == "chronyc":
            return 0, "System time     : 0.000001000 seconds slow of NTP time\nLeap status     : Not synchronised\n"
        return 127, ""

    unknown = clock_mod.read_host_clock_health(runner=unsynced)
    assert unknown["status"] == "degraded"
    assert unknown["reason"] == "sync_unknown"
    assert unknown["orders_fail_closed"] is True

    def fast(argv):
        if argv[0] == "chronyc":
            return 0, "System time     : 1.500000000 seconds fast of NTP time\nLeap status     : Normal\n"
        return 127, ""

    exceeded = clock_mod.read_host_clock_health(runner=fast, max_offset_ms=250)
    assert exceeded["reason"] == "offset_exceeded"
    assert exceeded["orders_fail_closed"] is True

    def timedate(argv):
        if argv == ["chronyc", "-n", "tracking"]:
            return 127, ""
        if argv == ["timedatectl", "show"]:
            return 0, "NTPSynchronized=yes\n"
        if argv == ["timedatectl", "show-timesync"]:
            return 0, "OffsetUSec=1500\nRootDistanceUSec=2500\n"
        return 127, ""

    systemd = clock_mod.read_host_clock_health(runner=timedate, max_offset_ms=250)
    assert systemd["status"] == "healthy"
    assert systemd["clock_source_id"] == "timedatectl"
    assert systemd["offset_ms"] == pytest.approx(1.5)
    assert systemd["uncertainty_ms"] == pytest.approx(2.5)

    def ntp(argv):
        if argv[0] in {"chronyc", "timedatectl"}:
            return 127, ""
        assert argv == ["ntpq", "-c", "rv", "127.0.0.1"]
        return 0, "status=0614 leap_none, sync_ntp, offset=0.200, rootdisp=0.400"

    local_ntp = clock_mod.read_host_clock_health(runner=ntp, max_offset_ms=250)
    assert local_ntp["status"] == "healthy"
    assert local_ntp["clock_source_id"] == "ntp"
    assert local_ntp["offset_ms"] == pytest.approx(0.2)

    missing = clock_mod.read_host_clock_health(runner=lambda _argv: (127, ""))
    assert missing["status"] == "degraded"
    assert missing["reason"] == "sync_unknown"
    assert missing["orders_fail_closed"] is True

    def refuse(argv):
        raise AssertionError(argv)

    monkeypatch.setattr(clock_mod.subprocess, "run", refuse)
    assert clock_mod._run_local(["ntpq", "time.google.com"]) == (127, "")
    assert all(command[0] != "probe" for _name, command in clock_mod.LOCAL_STATUS_COMMANDS)
    assert not any("." in part and part != "127.0.0.1" for _name, command in clock_mod.LOCAL_STATUS_COMMANDS for part in command)
