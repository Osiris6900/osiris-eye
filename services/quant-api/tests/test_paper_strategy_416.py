"""A paper order strategy tag is immutable once the decision is stored."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from osiris_api.main import app
from osiris_api.paper_execution import (
    LIVE_EXECUTION_ENABLED,
    get_paper_order,
    initialize_paper_account,
    submit_paper_order,
)
from osiris_api.risk import RiskLimits

NOW = "2026-11-23T15:00:00+00:00"
FRESH = "2026-11-23T14:59:30+00:00"
LIMITS = RiskLimits(
    max_order_notional=100_000, max_position_notional=100_000,
    max_daily_loss=100_000, max_gross_exposure=100_000,
)


@pytest.fixture
def book(tmp_path, monkeypatch):
    monkeypatch.setenv("OSIRIS_PAPER_EXECUTION_DB", str(tmp_path / "paper.sqlite3"))
    monkeypatch.setenv("OSIRIS_INSTRUMENT_DB_PATH", str(tmp_path / "instruments.sqlite3"))
    monkeypatch.delenv("OSIRIS_ENABLE_LIVE_EXECUTION", raising=False)
    monkeypatch.delenv("OSIRIS_INSTRUMENT_FIXTURE", raising=False)
    initialize_paper_account("100000", LIMITS)


def _buy(key="btc", strategy_id="momentum-v1"):
    return submit_paper_order(
        client_order_id=key, symbol="BTC", side="BUY", quantity="1", price="100",
        fee="0", observed_at=FRESH, now=NOW, risk_service_available=True,
        kill_switch_active=False, strategy_id=strategy_id,
    )


def test_live_execution_stays_disabled():
    assert LIVE_EXECUTION_ENABLED is False


def test_strategy_is_stored_and_replay_must_repeat_it(book):
    first = _buy()
    assert first["status"] == "filled"
    assert first["strategy_id"] == "momentum-v1"
    assert get_paper_order("btc")["strategy_id"] == "momentum-v1"
    replay = _buy()
    assert replay["status"] == "idempotent_replay"
    assert replay["strategy_id"] == "momentum-v1"
    with pytest.raises(ValueError, match="idempotency_key_conflict"):
        _buy(strategy_id="other-book")
    with pytest.raises(ValueError, match="idempotency_key_conflict"):
        _buy(strategy_id=None)
    assert get_paper_order("btc")["strategy_id"] == "momentum-v1"


def test_an_untagged_order_stays_untagged(book):
    order = _buy(key="plain", strategy_id=None)
    assert order["status"] == "filled"
    assert "strategy_id" not in order
    assert "strategy_id" not in get_paper_order("plain")
    with pytest.raises(ValueError, match="idempotency_key_conflict"):
        _buy(key="plain", strategy_id="momentum-v1")


@pytest.mark.parametrize("strategy_id", ["", "has space", "x" * 65, "momentum.v1", 1])
def test_invalid_strategy_is_rejected(book, strategy_id):
    with pytest.raises(ValueError, match="invalid_strategy_id"):
        _buy(strategy_id=strategy_id)


def test_http_stores_the_strategy_tag(book):
    response = TestClient(app).post("/paper/execution/orders", json={
        "client_order_id": "btc", "symbol": "BTC", "side": "BUY",
        "quantity": "1", "price": "100", "fee": "0",
        "observed_at": FRESH, "now": NOW, "strategy_id": "momentum-v1",
    })
    assert response.status_code == 200
    assert response.json()["strategy_id"] == "momentum-v1"
    assert get_paper_order("btc")["strategy_id"] == "momentum-v1"
