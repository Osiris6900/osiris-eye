"""Inject a healthy host clock so paper orders do not depend on chrony or NTP.

Child processes inherit ``OSIRIS_CLOCK_HEALTH_JSON``. Production leaves it unset
and reads the host. That variable cannot set ``live_trading_authorized``.
"""
import json

import pytest

_HEALTHY = {
    "status": "healthy",
    "reason": "synchronized",
    "clock_source_id": "osiris.test-health",
    "offset_ms": 0.0,
    "uncertainty_ms": 1.0,
    "orders_fail_closed": False,
    "live_trading_authorized": False,
    "network_probe": False,
}


@pytest.fixture(autouse=True)
def _healthy_paper_clock(monkeypatch):
    from osiris_api.control_plane.clock import (
        CLOCK_HEALTH_ENV,
        install_clock_health,
        reset_clock_state,
    )

    install_clock_health(_HEALTHY)
    monkeypatch.setenv(CLOCK_HEALTH_ENV, json.dumps(_HEALTHY))
    yield
    reset_clock_state()
