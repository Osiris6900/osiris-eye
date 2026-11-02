"""Read-only Interactive Brokers paper-account historical data adapter.

The adapter refuses non-paper accounts and never exposes order-placement methods.
It connects lazily so importing the API does not require TWS/Gateway to be running.
"""
from __future__ import annotations

from datetime import datetime, timezone
import os
from typing import Any


class IBKRConfigurationError(RuntimeError):
    """IBKR connection settings are incomplete or unsafe."""


def parse_ibkr_contract(spec: str, ib_module: Any) -> tuple[Any, str]:
    """Parse explicit contract notation; no ambiguous symbol auto-discovery.

    STK:AAPL:SMART:USD
    CASH:EURUSD:IDEALPRO
    FUT:ES:202612:CME:USD:50
    OPT:AAPL:20261218:250:C:SMART:100:USD
    INDX:SPX:CBOE:USD
    """
    if not isinstance(spec, str) or len(spec) > 64:
        raise ValueError("invalid_ibkr_contract_spec")
    parts = spec.strip().upper().split(":")
    kind = parts[0] if parts else ""
    try:
        if kind == "STK" and len(parts) == 4:
            _, symbol, exchange, currency = parts
            return ib_module.Stock(symbol, exchange, currency), "equity"
        if kind == "CASH" and len(parts) == 3:
            _, pair, exchange = parts
            if len(pair) != 6 or not pair.isalpha():
                raise ValueError
            return ib_module.Forex(pair), "fx"
        if kind == "FUT" and len(parts) == 6:
            _, symbol, expiry, exchange, currency, multiplier = parts
            return ib_module.Future(
                symbol, expiry, exchange, currency=currency, multiplier=multiplier
            ), "future"
        if kind == "OPT" and len(parts) == 8:
            _, symbol, expiry, strike, right, exchange, multiplier, currency = parts
            strike_value = float(strike)
            if strike_value <= 0 or right not in {"C", "P"}:
                raise ValueError
            return ib_module.Option(
                symbol, expiry, strike_value, right, exchange,
                multiplier=multiplier, currency=currency,
            ), "option"
        if kind == "INDX" and len(parts) == 4:
            _, symbol, exchange, currency = parts
            return ib_module.Index(symbol, exchange, currency), "index"
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("invalid_ibkr_contract_spec") from exc
    raise ValueError("unsupported_or_invalid_ibkr_contract_spec")


class IBKRPaperMarketDataAdapter:
    """Fetch historical bars from a verified IBKR paper account, read-only."""

    name = "interactive-brokers-paper"
    mode = "sandbox"

    def __init__(
        self,
        *,
        host: str,
        port: int,
        client_id: int,
        account: str,
        bar_size: str = "1 day",
        timeout_seconds: float = 5.0,
    ) -> None:
        if not host or port not in {7497, 4002}:
            raise IBKRConfigurationError("ibkr_paper_host_and_paper_port_required")
        if client_id < 0 or not account.startswith("DU"):
            raise IBKRConfigurationError("ibkr_paper_client_id_and_DU_account_required")
        if bar_size not in {"1 day", "1 hour", "30 mins", "15 mins", "5 mins", "1 min"}:
            raise IBKRConfigurationError("unsupported_ibkr_bar_size")
        self.host = host
        self.port = port
        self.client_id = client_id
        self.account = account
        self.bar_size = bar_size
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_environment(cls, environ: dict[str, str] | None = None) -> "IBKRPaperMarketDataAdapter":
        env = os.environ if environ is None else environ
        try:
            if env.get("OSIRIS_IBKR_MODE", "").lower() != "paper":
                raise IBKRConfigurationError("ibkr_paper_mode_must_be_explicit")
            return cls(
                host=env.get("OSIRIS_IBKR_HOST", "127.0.0.1"),
                port=int(env["OSIRIS_IBKR_PORT"]),
                client_id=int(env["OSIRIS_IBKR_CLIENT_ID"]),
                account=env["OSIRIS_IBKR_ACCOUNT"],
                bar_size=env.get("OSIRIS_IBKR_BAR_SIZE", "1 day"),
                timeout_seconds=float(env.get("OSIRIS_IBKR_TIMEOUT_SECONDS", "5")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise IBKRConfigurationError("ibkr_paper_configuration_incomplete") from exc

    def fetch_bars(self, symbol: str, start: str, end: str) -> list[dict[str, Any]]:
        try:
            from ib_async import IB
        except ImportError as exc:
            raise RuntimeError("ib_async_dependency_not_installed") from exc

        start_dt = datetime.fromisoformat(start.replace("Z", "+00:00")).astimezone(timezone.utc)
        end_dt = datetime.fromisoformat(end.replace("Z", "+00:00")).astimezone(timezone.utc)
        days = max(1, (end_dt - start_dt).days + 1)
        if days > 365:
            raise ValueError("ibkr_historical_request_exceeds_365_days")

        ib = IB()
        try:
            ib.connect(
                self.host, self.port, clientId=self.client_id,
                timeout=self.timeout_seconds, readonly=True, account=self.account,
            )
            managed_accounts = set(ib.managedAccounts())
            if self.account not in managed_accounts or not self.account.startswith("DU"):
                raise RuntimeError("ibkr_paper_account_not_verified")

            contract, asset_class = parse_ibkr_contract(symbol, __import__("ib_async"))
            bars = ib.reqHistoricalData(
                contract,
                endDateTime=end_dt.strftime("%Y%m%d %H:%M:%S UTC"),
                durationStr=f"{days} D",
                barSizeSetting=self.bar_size,
                whatToShow="MIDPOINT" if asset_class == "fx" else "TRADES",
                useRTH=False,
                formatDate=2,
                keepUpToDate=False,
            )
            records: list[dict[str, Any]] = []
            available_at = datetime.now(timezone.utc).isoformat()
            for bar in bars:
                bar_time = bar.date
                if isinstance(bar_time, (int, float)):
                    event_time = datetime.fromtimestamp(bar_time, timezone.utc)
                elif isinstance(bar_time, datetime):
                    event_time = bar_time.replace(tzinfo=timezone.utc) if bar_time.tzinfo is None else bar_time.astimezone(timezone.utc)
                else:
                    event_time = datetime.fromisoformat(str(bar_time).replace("Z", "+00:00")).astimezone(timezone.utc)
                if not start_dt <= event_time <= end_dt:
                    continue
                records.append({
                    "symbol": symbol,
                    "event_time": event_time.isoformat(),
                    "available_at": available_at,
                    "open": float(bar.open),
                    "high": float(bar.high),
                    "low": float(bar.low),
                    "close": float(bar.close),
                    "volume": float(bar.volume or 0),
                })
            return records
        finally:
            if ib.isConnected():
                ib.disconnect()
