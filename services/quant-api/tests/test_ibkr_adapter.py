from datetime import datetime, timezone
import sys
import types

import pytest

from osiris_api.ibkr_adapter import (
    IBKRPaperMarketDataAdapter,
    IBKRConfigurationError,
    parse_ibkr_contract,
)


class Contract:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs


def fake_ib_module(ib_class=None):
    return types.SimpleNamespace(
        IB=ib_class,
        Stock=Contract,
        Forex=Contract,
        Future=Contract,
        Option=Contract,
        Index=Contract,
    )


def test_contract_specs_require_explicit_security_type_and_exchange():
    module = fake_ib_module()
    contract, asset_class = parse_ibkr_contract("STK:AAPL:SMART:USD", module)
    assert isinstance(contract, Contract)
    assert asset_class == "equity"
    _, asset_class = parse_ibkr_contract("CASH:EURUSD:IDEALPRO", module)
    assert asset_class == "fx"
    _, asset_class = parse_ibkr_contract("FUT:ES:202612:CME:USD:50", module)
    assert asset_class == "future"
    _, asset_class = parse_ibkr_contract("OPT:AAPL:20261218:250:C:SMART:100:USD", module)
    assert asset_class == "option"
    _, asset_class = parse_ibkr_contract("INDX:SPX:CBOE:USD", module)
    assert asset_class == "index"


@pytest.mark.parametrize("spec", ["AAPL", "STK:AAPL", "CASH:EUR:IDEALPRO", "OPT:AAPL:x:-1:C:SMART:100:USD"])
def test_contract_parser_rejects_ambiguous_or_invalid_contracts(spec):
    with pytest.raises(ValueError):
        parse_ibkr_contract(spec, fake_ib_module())


def test_adapter_refuses_live_port_and_non_paper_account():
    with pytest.raises(IBKRConfigurationError):
        IBKRPaperMarketDataAdapter(
            host="127.0.0.1", port=7496, client_id=1, account="DU123"
        )
    with pytest.raises(IBKRConfigurationError):
        IBKRPaperMarketDataAdapter(
            host="127.0.0.1", port=7497, client_id=1, account="U123"
        )


def test_environment_requires_explicit_paper_mode_and_du_account():
    with pytest.raises(IBKRConfigurationError):
        IBKRPaperMarketDataAdapter.from_environment({})
    config = {
        "OSIRIS_IBKR_MODE": "paper",
        "OSIRIS_IBKR_PORT": "7497",
        "OSIRIS_IBKR_CLIENT_ID": "7",
        "OSIRIS_IBKR_ACCOUNT": "DU123456",
    }
    adapter = IBKRPaperMarketDataAdapter.from_environment(config)
    assert adapter.mode == "sandbox"
    assert adapter.port == 7497


def test_fetch_bars_connects_read_only_checks_paper_account_and_disconnects(monkeypatch):
    class FakeBar:
        date = datetime(2026, 11, 2, 10, 0, tzinfo=timezone.utc)
        open, high, low, close, volume = 100, 102, 99, 101, 1234

    class FakeIB:
        connected = False
        def connect(self, host, port, **kwargs):
            assert port == 7497
            assert kwargs["readonly"] is True
            self.connected = True
        def managedAccounts(self):
            return ["DU123456"]
        def reqHistoricalData(self, contract, **kwargs):
            assert kwargs["whatToShow"] == "TRADES"
            assert kwargs["keepUpToDate"] is False
            return [FakeBar()]
        def isConnected(self):
            return self.connected
        def disconnect(self):
            self.connected = False

    monkeypatch.setitem(sys.modules, "ib_async", fake_ib_module(FakeIB))
    adapter = IBKRPaperMarketDataAdapter(
        host="127.0.0.1", port=7497, client_id=7, account="DU123456"
    )
    bars = adapter.fetch_bars(
        "STK:AAPL:SMART:USD",
        "2026-11-02T09:00:00Z",
        "2026-11-02T11:00:00Z",
    )
    assert len(bars) == 1
    assert bars[0]["symbol"] == "STK:AAPL:SMART:USD"
    assert bars[0]["close"] == 101.0
