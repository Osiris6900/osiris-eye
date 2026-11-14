"""Displayed liquidity conservation, source binding and real ledger recovery."""
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
import json
import os
import sqlite3
import subprocess
import sys

import pytest

from osiris_api import paper_execution as ledger
from osiris_api.control_plane.database import Database
from osiris_api.control_plane.depth import DepthService
from osiris_api.paper_depth_execution import execute_depth_fill

NOW = datetime(2026, 11, 13, 21, 30, tzinfo=UTC).timestamp()


class Transport:
    def __init__(self):
        self.sequence, self.ask_size, self.bid_size = 1, "10", "10"

    def request(self, method, url):
        assert method == "GET" and url.endswith("/products/BTC-USD/book?level=2")
        return {"sequence": self.sequence,
                "asks": [["101", self.ask_size, 1], ["102", "20", 1]],
                "bids": [["99", self.bid_size, 1], ["98", "10", 1]]}


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setenv("OSIRIS_PAPER_EXECUTION_DB", str(tmp_path / "paper.sqlite3"))
    monkeypatch.delenv("OSIRIS_ENABLE_LIVE_EXECUTION", raising=False)
    ledger.initialize_paper_account("10000")
    now, transport = [NOW], Transport()
    service = DepthService(Database(str(tmp_path / "control.sqlite3")),
                           entitlement_reference="synthetic-depth-fixture-only",
                           transport=transport, clock=lambda: now[0])
    service.capture("BTC-USD")
    now[0] += 0.5
    return service, now, transport


def iso(now):
    return datetime.fromtimestamp(now, UTC).isoformat()


def order(key, now, *, quantity="3", side="BUY", price="102", fee="0.3"):
    return ledger.submit_paper_order(client_order_id=key, symbol="BTC-USD", side=side,
        quantity=quantity, price=price, fee=fee, observed_at=iso(now), now=iso(now),
        risk_service_available=True, kill_switch_active=False, working=True)


def fill(service, order_id, plan_id, **kwargs):
    return execute_depth_fill(service, client_order_id=order_id, client_plan_id=plan_id,
                              **{"risk_service_available": True, **kwargs})


def test_displayed_levels_partial_fees_exact_pnl_and_canonical_reconciliation(session):
    service, now, transport = session
    assert order("buy", now[0])["state"] == "accepted"
    first = fill(service, "buy", "buy-1")
    assert first["fill"]["quantity"] == "1" and first["fill"]["price"] == "101"
    assert first["state"] == "partially_filled"
    second = fill(service, "buy", "buy-2")
    assert second["fill"]["quantity"] == "2" and second["fill"]["price"] == "102"
    assert second["state"] == "filled"
    assert ledger.snapshot()["cash"] == "9694.7"
    assert order("sell", now[0], side="SELL", price="98")["state"] == "accepted"
    fill(service, "sell", "sell-1")
    fill(service, "sell", "sell-2")
    assert fill(service, "sell", "sell-empty")["depth"]["outcome"] == "no_executable_liquidity"
    # Only observed net growth restores consumed virtual volume.
    transport.sequence, transport.bid_size = 2, "20"
    now[0] += 1
    service.capture("BTC-USD")
    now[0] += 0.5
    last = fill(service, "sell", "sell-3")
    assert last["state"] == "filled"
    current = ledger.snapshot()
    assert current["cash"] == "9990.4"
    assert current["realized_pnl"] == "-9.6"
    assert current["positions"] == []
    assert ledger.recover()["passed"] is True
    assert last["broker_order_submitted"] is False


def test_same_or_unchanged_new_snapshot_never_restores_consumed_liquidity(session):
    service, now, transport = session
    order("buy", now[0], price="101")
    fill(service, "buy", "first")
    assert fill(service, "buy", "empty-1")["depth"]["outcome"] == "no_executable_liquidity"
    transport.sequence = 2
    now[0] += 1
    service.capture("BTC-USD")
    now[0] += 0.5
    assert fill(service, "buy", "empty-2")["depth"]["outcome"] == "no_executable_liquidity"
    assert ledger.snapshot()["fill_count"] == 1
    transport.sequence, transport.ask_size = 3, "20"
    now[0] += 1
    service.capture("BTC-USD")
    now[0] += 0.5
    assert fill(service, "buy", "growth")["fill"]["quantity"] == "1"


def test_passive_limit_orders_never_receive_invented_queue_fills(session):
    service, now, _ = session
    order("passive", now[0], price="100")
    result = fill(service, "passive", "no-cross")
    assert result["depth"]["outcome"] == "no_executable_liquidity"
    assert result["fill"] is None
    assert ledger.snapshot()["fill_count"] == 0


def test_concurrent_plan_replays_commit_one_fill_and_no_double_liquidity(session):
    service, now, _ = session
    order("buy", now[0])
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: fill(service, "buy", "same-plan"), range(8)))
    assert ledger.snapshot()["fill_count"] == 1
    assert len({result["fill"]["client_fill_id"] for result in results}) == 1
    assert sum(result["status"] == "idempotent_replay" for result in results) == 7


def test_restart_replays_recorded_plan_once_with_no_new_approval(session):
    service, now, _ = session
    order("buy", now[0])
    first = fill(service, "buy", "stable")
    code = '''
import json,sys
from osiris_api.control_plane.database import Database
from osiris_api.control_plane.depth import DepthService
from osiris_api.paper_depth_execution import execute_depth_fill
service=DepthService(Database(sys.argv[1]),entitlement_reference='synthetic-depth-fixture-only')
result=execute_depth_fill(service,client_order_id='buy',client_plan_id='stable')
print(json.dumps(result))
'''
    child = subprocess.run([sys.executable, "-c", code, service.db.path], check=True,
                           capture_output=True, text=True, timeout=30)
    replay = json.loads(child.stdout)
    assert replay["fill"] == first["fill"]
    assert replay["status"] == "idempotent_replay"
    assert replay["execution_authorized"] is False
    assert ledger.snapshot()["fill_count"] == 1


def test_crash_rolls_back_cash_liquidity_source_admission_and_fill(session):
    service, now, _ = session
    order("buy", now[0])
    before = ledger.snapshot()
    with pytest.raises(RuntimeError, match="simulated_crash"):
        fill(service, "buy", "crash", simulate_crash=True)
    assert ledger.snapshot() == before
    assert fill(service, "buy", "crash")["fill"]["quantity"] == "1"
    assert ledger.snapshot()["fill_count"] == 1


@pytest.mark.parametrize("kind", ["kill", "risk", "stale", "latency", "cancel"])
def test_current_controls_and_time_guards_refuse_new_economics(session, kind):
    service, now, _ = session
    order("buy", now[0])
    kwargs = {}
    if kind == "kill":
        ledger.set_kill_switch(True)
    elif kind == "risk":
        kwargs["risk_service_available"] = False
    elif kind == "stale":
        now[0] += 10
    elif kind == "latency":
        kwargs["min_latency_ms"] = 1000
    elif kind == "cancel":
        ledger.cancel_paper_order(client_order_id="buy")
    cash = ledger.snapshot()["cash"]
    with pytest.raises(ValueError):
        fill(service, "buy", "refused", **kwargs)
    assert ledger.snapshot()["cash"] == cash
    assert ledger.snapshot()["fill_count"] == 0


def test_policy_or_plan_rebinding_is_refused(session):
    service, now, _ = session
    order("buy", now[0])
    fill(service, "buy", "first")
    with pytest.raises(ValueError, match="depth_plan_id_conflict"):
        fill(service, "buy", "first", participation_bps=2000)
    with pytest.raises(ValueError, match="depth_snapshot_or_policy_rollback"):
        fill(service, "buy", "different", participation_bps=2000)


def test_control_and_financial_database_alias_is_refused_before_locking(session):
    service, now, _ = session
    order("buy", now[0])
    service.db.path = os.environ["OSIRIS_PAPER_EXECUTION_DB"]
    with pytest.raises(ValueError, match="separate_depth_and_execution_databases_required"):
        fill(service, "buy", "same-database")
    assert ledger.snapshot()["fill_count"] == 0


def test_mutable_financial_rows_cannot_change_a_source_bound_fill(session):
    service, now, _ = session
    order("buy", now[0])
    fill(service, "buy", "first")
    with sqlite3.connect(os.environ["OSIRIS_PAPER_EXECUTION_DB"]) as conn:
        conn.execute("UPDATE paper_exec_fills SET quantity='2'")
    assert ledger.reconciliation_report()["passed"] is False
    with pytest.raises(ValueError, match="reconciliation"):
        fill(service, "buy", "next")
