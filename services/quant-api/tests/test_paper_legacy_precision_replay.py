"""Pre-marker journals retain their original partial-fill allocation semantics."""
import json
import os
import subprocess
import sys

import pytest

from osiris_api import paper_execution as ledger

NOW = "2026-11-14T04:30:00+00:00"


@pytest.fixture
def legacy_account(tmp_path, monkeypatch):
    monkeypatch.setenv("OSIRIS_PAPER_EXECUTION_DB", str(tmp_path / "old.sqlite3"))
    monkeypatch.delenv("OSIRIS_ENABLE_LIVE_EXECUTION", raising=False)
    append = ledger._append_audit

    def old_initialization(conn, event, key, payload):
        if event == "account_initialized":
            payload = {k: v for k, v in payload.items() if k != "accounting_model"}
        return append(conn, event, key, payload)

    with monkeypatch.context() as setup:
        setup.setattr(ledger, "_append_audit", old_initialization)
        ledger.initialize_paper_account("1000")
    ledger.submit_paper_order(client_order_id="buy", symbol="BTC", side="BUY",
        quantity="1", price="1", fee="0.3", observed_at=NOW, now=NOW, working=True)


def test_pre_marker_partial_history_replays_and_can_continue(legacy_account):
    result = ledger.apply_paper_fill(client_order_id="buy", client_fill_id="old-partial",
        quantity="0.50000000000000000000000000001", observed_at=NOW, now=NOW)
    # These are the recorded outputs of the former 28-digit writer. The raw
    # fill quantity stays intact; its historical order allocation stays rounded.
    assert result["filled_quantity"] == "0.5"
    assert result["leaves_quantity"] == "0.5"
    assert ledger.snapshot()["recent_fills"][0]["fee"] == "0.15"
    assert ledger.recover()["passed"] is True
    child = subprocess.run([sys.executable, "-c",
        "import json;from osiris_api import paper_execution as p;print(json.dumps(p.recover()))"],
        check=True, capture_output=True, text=True, timeout=30, env=os.environ.copy())
    assert json.loads(child.stdout)["passed"] is True
    closed = ledger.apply_paper_fill(client_order_id="buy", client_fill_id="continued",
        quantity="0.5", observed_at=NOW, now=NOW)
    assert closed["state"] == "filled"
    assert closed["filled_quantity"] == "1"
    assert closed["leaves_quantity"] == "0"
    assert ledger.snapshot()["recent_fills"][0]["fee"] == "0.15"
    assert ledger.recover()["passed"] is True
