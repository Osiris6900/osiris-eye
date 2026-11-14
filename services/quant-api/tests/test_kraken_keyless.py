"""Explicit keyless USD venue, closed candles and canonical paper safeguards."""
from datetime import UTC, datetime, timedelta
import json
from unittest.mock import patch

import pytest

from osiris_api import paper_execution as ledger
from osiris_api.paper_pipeline import run_paper_pipeline
from osiris_api.public_market_data import KrakenPublicMarketDataAdapter

NOW = datetime(2026, 11, 13, 12, 0, 10, tzinfo=UTC)


def payload():
    last = NOW.replace(second=0)
    rows = [[int((last - timedelta(minutes=4-i)).timestamp()), str(100+i), str(102+i),
             str(99+i), str(101+i), str(101+i), "10", 5] for i in range(5)]
    return {"error": [], "result": {"XXBTZUSD": rows, "last": rows[-1][0]}}


@pytest.fixture
def account(tmp_path, monkeypatch):
    monkeypatch.setenv("OSIRIS_PAPER_EXECUTION_DB", str(tmp_path / "paper.sqlite3"))
    monkeypatch.delenv("OSIRIS_ENABLE_LIVE_EXECUTION", raising=False)
    ledger.initialize_paper_account("1000")


def test_kraken_normalizes_explicit_usd_pair_and_excludes_current_candle():
    with patch("osiris_api.public_market_data._get", return_value=json.dumps(payload()).encode()) as get:
        bars = KrakenPublicMarketDataAdapter().fetch_bars("BTC-USD", "2026-11-13T11:56:00Z", "2026-11-13T12:00:00Z")
    assert len(bars) == 4
    assert all(bar["symbol"] == "BTC-USD" and bar["is_closed"] is True for bar in bars)
    assert bars[-1]["candle_end_time"] == "2026-11-13T12:00:00+00:00"
    assert "pair=XBTUSD" in get.call_args.args[0]
    assert "Authorization" not in get.call_args.kwargs


@pytest.mark.parametrize("kind", ["error", "pair", "shape", "crossed", "duplicate", "nan", "timestamp"])
def test_kraken_refuses_malformed_or_ambiguous_sources(kind):
    data = payload()
    rows = data["result"]["XXBTZUSD"]
    if kind == "error":
        data["error"] = ["EQuery:Unknown asset pair"]
    elif kind == "pair":
        data["result"]["XBTUSDT"] = data["result"].pop("XXBTZUSD")
    elif kind == "shape":
        rows[0] = []
    elif kind == "crossed":
        rows[0][2] = "1"
    elif kind == "duplicate":
        rows[1] = list(rows[0])
    elif kind == "nan":
        rows[0][4] = "NaN"
    elif kind == "timestamp":
        rows[0][0] += 1
    with patch("osiris_api.public_market_data._get", return_value=json.dumps(data).encode()):
        with pytest.raises(ValueError):
            KrakenPublicMarketDataAdapter().fetch_bars("BTC-USD", "2026-11-13T11:56:00Z", "2026-11-13T12:00:00Z")


@pytest.mark.parametrize("symbol", ["BTC-USDT", "ETHUSDT", "XBTUSD", "BTC-USD/../../orders"])
def test_kraken_does_not_substitute_quote_currency_or_accept_urls(symbol):
    with patch("osiris_api.public_market_data._get") as get:
        with pytest.raises(ValueError):
            KrakenPublicMarketDataAdapter().fetch_bars(symbol, "2026-11-13T11:56:00Z", "2026-11-13T12:00:00Z")
        get.assert_not_called()


def records():
    return [dict(symbol="BTC-USD", event_time=(NOW.replace(second=0) - timedelta(minutes=4-i)).isoformat(),
                 available_at=NOW.isoformat(), open=100.0+i, high=100.0+i, low=100.0+i,
                 close=100.0+i, volume=10.0) for i in range(4)]


def test_explicit_kraken_runs_through_same_risk_fill_ledger_and_replay(account):
    with patch.object(KrakenPublicMarketDataAdapter, "fetch_bars", return_value=records()):
        result = run_paper_pipeline(provider="kraken", quantity="1", now=NOW)
        replay = run_paper_pipeline(provider="kraken", quantity="1", now=NOW)
    assert result["provider"] == "kraken"
    assert result["order"]["status"] == "filled"
    assert result["reconciliation"]["passed"] is True
    assert result["broker_order_submitted"] is False
    assert replay["replayed"] is True
    assert ledger.snapshot()["fill_count"] == 1


def test_source_switch_cannot_duplicate_or_relabel_a_candle_decision(account):
    from osiris_api.public_market_data import CoinbasePublicMarketDataAdapter
    with patch.object(CoinbasePublicMarketDataAdapter, "fetch_bars", return_value=records()):
        run_paper_pipeline(quantity="1", now=NOW)
    with patch.object(KrakenPublicMarketDataAdapter, "fetch_bars", return_value=records()):
        with pytest.raises(ValueError, match="pipeline_decision_conflict"):
            run_paper_pipeline(provider="kraken", quantity="1", now=NOW)
    assert ledger.snapshot()["fill_count"] == 1


def test_unavailable_venue_never_falls_back_or_mutates_orders(account):
    with patch.object(KrakenPublicMarketDataAdapter, "fetch_bars", side_effect=RuntimeError("unavailable")), \
         patch("osiris_api.paper_pipeline.CoinbasePublicMarketDataAdapter") as other:
        with pytest.raises(RuntimeError):
            run_paper_pipeline(provider="kraken", quantity="1", now=NOW)
        other.assert_not_called()
    assert ledger.snapshot()["order_count"] == ledger.snapshot()["fill_count"] == 0


def test_public_response_read_is_bounded():
    from osiris_api.public_market_data import MAX_PUBLIC_RESPONSE_BYTES, _get

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, count):
            assert count == MAX_PUBLIC_RESPONSE_BYTES + 1
            return b"x" * count

    with patch("osiris_api.public_market_data.urlopen", return_value=Response()):
        with pytest.raises(ValueError, match="response_too_large"):
            _get("https://api.exchange.coinbase.com/products/BTC-USD")


def test_disabled_source_is_refused_before_network_or_orders(account, monkeypatch):
    monkeypatch.setenv("OSIRIS_PUBLIC_KRAKEN_ENABLED", "false")
    with patch.object(KrakenPublicMarketDataAdapter, "fetch_bars") as fetch:
        with pytest.raises(ValueError, match="provider_disabled"):
            run_paper_pipeline(provider="kraken", now=NOW)
        fetch.assert_not_called()
    assert ledger.snapshot()["order_count"] == 0
