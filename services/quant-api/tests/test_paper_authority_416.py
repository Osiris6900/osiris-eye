"""A second paper writer cannot commit over a live lease.

Unset tokens share one file-scoped owner, so a restarted process can still
write the same database. A different token is split-brain and changes nothing.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time

import pytest
from fastapi.testclient import TestClient

from osiris_api.main import app
from osiris_api.paper_authority import SHARED_OWNER, paper_writer
from osiris_api.paper_execution import (
    LIVE_EXECUTION_ENABLED,
    apply_paper_fill,
    cancel_paper_order,
    initialize_paper_account,
    mark_to_market,
    set_kill_switch,
    snapshot,
    submit_paper_order,
)
from osiris_api.paper_pipeline_status import record_pipeline_run
from osiris_api.risk import RiskLimits

NOW = "2026-11-22T15:00:00+00:00"
FRESH = "2026-11-22T14:59:30+00:00"
CLOCK = [1_700_000_000.0]


@pytest.fixture
def book(tmp_path, monkeypatch):
    monkeypatch.setenv("OSIRIS_PAPER_EXECUTION_DB", str(tmp_path / "paper.sqlite3"))
    monkeypatch.setenv("OSIRIS_INSTRUMENT_DB_PATH", str(tmp_path / "instruments.sqlite3"))
    monkeypatch.delenv("OSIRIS_PAPER_WRITER_TOKEN", raising=False)
    monkeypatch.delenv("OSIRIS_ENABLE_LIVE_EXECUTION", raising=False)
    monkeypatch.delenv("OSIRIS_INSTRUMENT_FIXTURE", raising=False)
    CLOCK[0] = 1_700_000_000.0
    with paper_writer("alpha", clock=lambda: CLOCK[0]):
        initialize_paper_account("100000", RiskLimits(
            max_order_notional=100_000, max_position_notional=100_000,
            max_daily_loss=100_000, max_gross_exposure=100_000,
        ))
    return tmp_path


def _order(key="order-1", *, working=False):
    return submit_paper_order(
        client_order_id=key, symbol="BTC", side="BUY", quantity="1", price="100",
        fee="0", observed_at=FRESH, now=NOW, risk_service_available=True,
        kill_switch_active=False, working=working,
    )


def test_live_execution_stays_disabled():
    assert LIVE_EXECUTION_ENABLED is False


def test_foreign_writer_cannot_change_the_book(book):
    with paper_writer("alpha", clock=lambda: CLOCK[0]):
        first = _order()
    assert first["status"] == "filled"
    before = snapshot()
    assert before["authority"]["established"] is True
    assert before["authority"]["epoch"] == 1
    assert before["authority"]["live_execution"] is False
    assert "alpha" not in json.dumps(before["authority"])
    assert before["authority"]["owner_fingerprint"] == hashlib.sha256(b"alpha").hexdigest()[:16]
    with paper_writer("beta", clock=lambda: CLOCK[0]):
        with pytest.raises(ValueError, match="split_brain_rejected"):
            _order("beta-order")
        with pytest.raises(ValueError, match="split_brain_rejected"):
            apply_paper_fill(
                client_order_id="order-1", client_fill_id="extra", quantity="1",
                observed_at=FRESH, now=NOW,
            )
        with pytest.raises(ValueError, match="split_brain_rejected"):
            mark_to_market({"BTC": "110"}, observed_at=FRESH, now=NOW)
        with pytest.raises(ValueError, match="split_brain_rejected"):
            set_kill_switch(True)
        assert snapshot()["cash"] == before["cash"]
        assert snapshot()["kill_switch_active"] is False
    assert snapshot()["authority"]["epoch"] == 1
    assert snapshot()["order_count"] == before["order_count"]
    assert snapshot()["fill_count"] == 1


def test_expired_lease_lets_the_next_writer_take_the_epoch(book):
    with paper_writer("alpha", clock=lambda: CLOCK[0]):
        opened = _order("resting", working=True)
    assert opened["state"] == "accepted"
    CLOCK[0] += 31
    with paper_writer("beta", clock=lambda: CLOCK[0]):
        cancelled = cancel_paper_order(client_order_id="resting")
    assert cancelled["state"] == "cancelled"
    assert snapshot()["authority"]["epoch"] == 2
    with paper_writer("alpha", clock=lambda: CLOCK[0]):
        with pytest.raises(ValueError, match="split_brain_rejected"):
            _order("alpha-late")
    assert snapshot()["fill_count"] == 0
    assert snapshot()["authority"]["epoch"] == 2


def test_same_writer_renews_without_a_new_epoch(book):
    with paper_writer("alpha", clock=lambda: CLOCK[0]):
        _order("one")
        first_expiry = snapshot()["authority"]["expires_at"]
        CLOCK[0] += 10
        _order("two")
    authority = snapshot()["authority"]
    assert authority["epoch"] == 1
    assert authority["expires_at"] == CLOCK[0] + 30
    assert authority["expires_at"] > first_expiry


def test_clock_rollback_and_crash_leave_the_last_lease(book):
    with paper_writer("alpha", clock=lambda: CLOCK[0]):
        _order("kept")
        before = snapshot()
        CLOCK[0] -= 1
        with pytest.raises(ValueError, match="authority_clock_rollback"):
            _order("rolled")
        CLOCK[0] += 1
        with pytest.raises(RuntimeError, match="simulated_crash"):
            submit_paper_order(
                client_order_id="crashed", symbol="BTC", side="BUY", quantity="1",
                price="100", fee="0", observed_at=FRESH, now=NOW,
                risk_service_available=True, kill_switch_active=False,
                simulate_crash=True,
            )
    assert snapshot() == before


def test_invalid_token_and_clock_fail_closed(book, monkeypatch):
    before = snapshot()
    monkeypatch.setenv("OSIRIS_PAPER_WRITER_TOKEN", "bad token")
    with pytest.raises(ValueError, match="invalid_writer_token"):
        _order("nope")
    with paper_writer("alpha", clock=lambda: float("nan")):
        with pytest.raises(ValueError, match="invalid_authority_clock"):
            _order("nan-clock")
    assert snapshot() == before


def test_shared_file_owner_matches_an_unset_token(book):
    with paper_writer(SHARED_OWNER, clock=lambda: CLOCK[0]):
        # The fixture writer was alpha, whose lease is still live.
        with pytest.raises(ValueError, match="split_brain_rejected"):
            _order("shared-too-soon")
    CLOCK[0] += 31
    with paper_writer(SHARED_OWNER, clock=lambda: CLOCK[0]):
        assert _order("shared-later")["status"] == "filled"


def test_pipeline_record_uses_the_same_fence(book):
    result = {
        "symbol": "BTC", "provider": "coinbase", "decision_time": NOW,
        "workflow": {"risk": {"approved_for_paper": False}},
        "market": {"observed_at": FRESH, "max_age_seconds": 300},
        "order": {"status": "not_submitted"},
    }
    with paper_writer("alpha", clock=lambda: CLOCK[0]):
        record_pipeline_run(result)
    with paper_writer("beta", clock=lambda: CLOCK[0]):
        with pytest.raises(ValueError, match="split_brain_rejected"):
            record_pipeline_run(result)
    assert snapshot()["authority"]["epoch"] == 1


def test_http_body_cannot_choose_the_writer(book, monkeypatch):
    monkeypatch.setenv("OSIRIS_PAPER_WRITER_TOKEN", "alpha")
    response = TestClient(app).post("/paper/execution/orders", json={
        "client_order_id": "http-1", "symbol": "BTC", "side": "BUY",
        "quantity": "1", "price": "100", "fee": "0",
        "observed_at": FRESH, "now": NOW, "writer": "beta",
        "risk_service_available": True, "kill_switch_active": False,
    })
    assert response.status_code == 200
    assert response.json()["status"] == "filled"
    assert response.json()["live_execution"] is False


def test_other_process_with_another_token_cannot_commit(book):
    current = time.time()
    with paper_writer("alpha", clock=lambda: current):
        _order("parent")
    parent = snapshot()
    child = r"""
import os, sys
os.environ["OSIRIS_PAPER_EXECUTION_DB"] = sys.argv[1]
os.environ["OSIRIS_INSTRUMENT_DB_PATH"] = sys.argv[2]
os.environ["OSIRIS_PAPER_WRITER_TOKEN"] = "child-writer"
from osiris_api.paper_execution import submit_paper_order
try:
    submit_paper_order(
        client_order_id="child", symbol="BTC", side="BUY", quantity="1", price="100",
        fee="0", observed_at=sys.argv[3], now=sys.argv[4], risk_service_available=True,
        kill_switch_active=False,
    )
except ValueError as exc:
    if str(exc) != "split_brain_rejected":
        raise
    raise SystemExit(0)
raise SystemExit(2)
"""
    env = os.environ.copy()
    env["OSIRIS_PAPER_WRITER_TOKEN"] = "child-writer"
    completed = subprocess.run(
        [sys.executable, "-c", child, str(book / "paper.sqlite3"),
         str(book / "instruments.sqlite3"), FRESH, NOW],
        check=False, capture_output=True, text=True, env=env, timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert snapshot()["fill_count"] == parent["fill_count"]
    assert snapshot()["authority"] == parent["authority"]
