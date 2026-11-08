"""Durable paper ledger accounting and idempotency regression tests."""
import pytest

from osiris_api.durable_paper_ledger import initialize_account, snapshot, synthetic_fill


def test_synthetic_fills_survive_new_connections(tmp_path, monkeypatch):
    monkeypatch.setenv("OSIRIS_PAPER_LEDGER_DB", str(tmp_path / "paper.sqlite3"))
    initialize_account("1000")
    assert synthetic_fill("buy-1", "BTC", "BUY", "2", "100", "1")["status"] == "synthetic_fill"
    assert snapshot()["cash"] == "799"
    assert synthetic_fill("buy-1", "BTC", "BUY", "2", "100", "1")["status"] == "idempotent_replay"
    assert snapshot()["fill_count"] == 1
    assert synthetic_fill("sell-1", "BTC", "SELL", "1", "120", "1")["status"] == "synthetic_fill"
    state = snapshot()
    assert state["cash"] == "918"
    assert state["pnl"]["realized"] == "18.5"
    assert state["positions"][0]["quantity"] == "1"
    assert state["pnl"]["unrealized"] is None
    with pytest.raises(ValueError, match="idempotency_key_conflict"):
        synthetic_fill("buy-1", "BTC", "BUY", "3", "100", "1")
    with pytest.raises(ValueError, match="insufficient_paper_position"):
        synthetic_fill("sell-2", "BTC", "SELL", "2", "120")
    assert snapshot()["fill_count"] == 2


def test_reject_unfunded_and_uninitialized(tmp_path, monkeypatch):
    monkeypatch.setenv("OSIRIS_PAPER_LEDGER_DB", str(tmp_path / "empty.sqlite3"))
    with pytest.raises(ValueError, match="account_not_initialized"):
        synthetic_fill("x", "BTC", "BUY", 1, 100)
    initialize_account(10)
    with pytest.raises(ValueError, match="insufficient_paper_cash"):
        synthetic_fill("x", "BTC", "BUY", 1, 100)
    assert snapshot()["cash"] == "10"


def test_malformed_side_and_symbol_are_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("OSIRIS_PAPER_LEDGER_DB", str(tmp_path / "bad.sqlite3"))
    initialize_account("1000")
    with pytest.raises(ValueError, match="invalid_side"):
        synthetic_fill("bad-side", "BTC", None, "1", "100")
    with pytest.raises(ValueError, match="invalid_symbol"):
        synthetic_fill("bad-symbol", "BTC\\nUSD", "BUY", "1", "100")
    assert snapshot()["fill_count"] == 0


def test_sell_fee_cannot_create_negative_cash_proceeds(tmp_path, monkeypatch):
    monkeypatch.setenv("OSIRIS_PAPER_LEDGER_DB", str(tmp_path / "fee.sqlite3"))
    initialize_account("1000")
    synthetic_fill("buy", "BTC", "BUY", "1", "100")
    with pytest.raises(ValueError, match="fee_exceeds_sell_proceeds"):
        synthetic_fill("sell", "BTC", "SELL", "1", "10", "11")
    state = snapshot()
    assert state["fill_count"] == 1
    assert state["positions"][0]["quantity"] == "1"
