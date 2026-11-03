"""Authenticated HTTP routes for the provider-neutral market-data gateway."""
from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from .ibkr_adapter import IBKRPaperMarketDataAdapter, IBKRConfigurationError
from .market_gateway import MarketDataGateway, MarketDataUnavailable
from .market_universe import ASSET_CLASSES, audit_instruments
from .provider_bootstrap import provider_readiness
from .provider_router import MultiProviderMarketDataRouter
from .universal_aggregator import UniversalMarketDataAggregator
from .public_market_data import (
    BinancePublicMarketDataAdapter, CoinbasePublicMarketDataAdapter,
    StooqDailyMarketDataAdapter,
)

router = APIRouter(tags=["market-data"])
market_data_gateway = MarketDataGateway()

# Public feeds are read-only, require no trading credentials, and can be disabled.
if os.getenv("OSIRIS_PUBLIC_COINBASE_ENABLED", "true").strip().lower() in {"1", "true", "yes"}:
    granularity = int(os.getenv("OSIRIS_PUBLIC_COINBASE_GRANULARITY", "60"))
    market_data_gateway.register_adapter(
        "coinbase-public", CoinbasePublicMarketDataAdapter(granularity=granularity),
        supported_asset_classes=["crypto"],
        entitlement_reference="public-data:coinbase-exchange",
    )

if os.getenv("OSIRIS_PUBLIC_BINANCE_ENABLED", "true").strip().lower() in {"1", "true", "yes"}:
    market_data_gateway.register_adapter(
        "binance-public",
        BinancePublicMarketDataAdapter(
            interval=os.getenv("OSIRIS_PUBLIC_BINANCE_INTERVAL", "1m")
        ),
        supported_asset_classes=["crypto"],
        entitlement_reference="public-data:binance-spot",
    )

if os.getenv("OSIRIS_PUBLIC_STOOQ_ENABLED", "true").strip().lower() in {"1", "true", "yes"}:
    market_data_gateway.register_adapter(
        "stooq-daily", StooqDailyMarketDataAdapter(),
        supported_asset_classes=["equity"],
        entitlement_reference="public-data:stooq-daily",
    )


def _register_ibkr_paper_adapter() -> None:
    """Register only explicitly configured IBKR paper access; never live accounts."""
    env = os.environ
    required = (
        "OSIRIS_IBKR_MODE", "OSIRIS_IBKR_PORT", "OSIRIS_IBKR_CLIENT_ID",
        "OSIRIS_IBKR_ACCOUNT", "OSIRIS_IBKR_ENTITLEMENT_REFERENCE",
    )
    if env.get("OSIRIS_IBKR_MODE", "").lower() != "paper" or any(not env.get(k, "").strip() for k in required):
        return
    try:
        adapter = IBKRPaperMarketDataAdapter.from_environment()
        market_data_gateway.register_adapter(
            "interactive_brokers", adapter,
            supported_asset_classes=["equity", "fx", "future", "option", "index"],
            entitlement_reference=env["OSIRIS_IBKR_ENTITLEMENT_REFERENCE"],
            credential_reference="env:OSIRIS_IBKR_ACCOUNT",
        )
    except (IBKRConfigurationError, ValueError):
        return


_register_ibkr_paper_adapter()


def _configured_provider_order() -> list[str]:
    return [item.strip() for item in os.getenv("OSIRIS_MARKET_PROVIDER_ORDER", "").split(",") if item.strip()]


def _default_provider_order(symbol: str) -> list[str]:
    """Choose only a feed whose native symbol convention matches the request."""
    normalized = symbol.strip().upper()
    if normalized.endswith(".US") or normalized.endswith(".UK"):
        return ["stooq-daily"]
    if "-" in normalized:
        return ["coinbase-public"]
    return ["binance-public"]


def _router_for_symbol(symbol: str) -> MultiProviderMarketDataRouter:
    order = _configured_provider_order() or _default_provider_order(symbol)
    return MultiProviderMarketDataRouter(market_data_gateway, order)


@router.get("/market-data/asset-classes")
def market_data_asset_classes():
    return {
        "asset_classes": list(ASSET_CLASSES), "catalog_persisted": False,
        "exchange_connectivity_verified": False, "live_execution": False,
    }


@router.post("/market-data/instruments/audit")
def market_data_instruments_audit(payload: dict[str, Any]):
    instruments = payload.get("instruments")
    if not isinstance(instruments, list):
        raise HTTPException(status_code=422, detail="instruments_must_be_list")
    try:
        return audit_instruments(instruments)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/market-data/global-coverage")
def market_data_global_coverage():
    providers = market_data_gateway.providers()["providers"]
    return {
        "asset_classes": list(ASSET_CLASSES),
        "implemented_public_connectors": [
            {"provider": "coinbase-public", "asset_classes": ["crypto"], "granularity": "configurable candles", "coverage": "Coinbase Exchange listed products"},
            {"provider": "binance-public", "asset_classes": ["crypto"], "granularity": os.getenv("OSIRIS_PUBLIC_BINANCE_INTERVAL", "1m"), "coverage": "Binance Spot listed pairs"},
            {"provider": "stooq-daily", "asset_classes": ["equity"], "granularity": "daily", "coverage": "Stooq-supported symbols with market suffix; verify symbol coverage per listing"},
            {"provider": "interactive_brokers", "asset_classes": ["equity", "fx", "future", "option", "index"], "granularity": "broker entitlement dependent", "coverage": "only when paper adapter is explicitly configured"},
        ],
        "registered_provider_status": providers,
        "global_coverage_complete": False,
        "real_time_all_markets_verified": False,
        "live_execution": False,
        "note": "Connector support is not equivalent to full exchange coverage. Verify symbol mapping, data delay, exchange entitlements, rate limits and redistribution rights before production use.",
    }


@router.get("/market-data/providers")
def market_data_providers():
    return market_data_gateway.providers()


@router.get("/market-data/bars")
def market_data_bars(
    provider: str = Query(..., min_length=1, max_length=100),
    symbol: str = Query(..., min_length=1, max_length=64),
    start: str = Query(..., min_length=20, max_length=64),
    end: str = Query(..., min_length=20, max_length=64),
    asset_class: str | None = Query(default=None, min_length=1, max_length=32),
):
    try:
        return market_data_gateway.fetch_bars(provider, symbol, start, end, asset_class=asset_class)
    except MarketDataUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (ValueError, TypeError, OverflowError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/market-data/aggregate/bars")
def market_data_aggregate_bars(
    providers: str = Query(..., min_length=1, max_length=500),
    symbol: str = Query(..., min_length=1, max_length=64),
    start: str = Query(..., min_length=20, max_length=64),
    end: str = Query(..., min_length=20, max_length=64),
    asset_class: str | None = Query(default=None, min_length=1, max_length=32),
    divergence_threshold_bps: float = Query(default=50.0, ge=0, le=10000),
):
    try:
        aggregator = UniversalMarketDataAggregator(
            market_data_gateway, divergence_threshold_bps=divergence_threshold_bps
        )
        return aggregator.aggregate_bars(
            providers=[item.strip() for item in providers.split(",")],
            symbol=symbol, start=start, end=end, asset_class=asset_class,
        )
    except MarketDataUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (ValueError, TypeError, OverflowError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/market-data/routing/providers")
def market_data_routing_providers():
    configured = _configured_provider_order()
    providers = market_data_gateway.providers()["providers"]
    return {
        "providers": providers,
        "routing_policy": "operator_priority_order" if configured else "symbol_convention_default",
        "default_symbol_routes": {
            "BASE-QUOTE": ["coinbase-public"],
            "BASEQUOTE": ["binance-public"],
            "TICKER.US_or_TICKER.UK": ["stooq-daily"],
        },
        "live_market_data_enabled": False,
        "connectivity_verified": False,
        "note": "Symbol routing selects an adapter only; it does not prove upstream connectivity, exchange coverage, data licensing, or real-time entitlement.",
    }


@router.get("/market-data/routing/bars")
def market_data_routed_bars(
    symbol: str = Query(..., min_length=1, max_length=64),
    start: str = Query(..., min_length=20, max_length=64),
    end: str = Query(..., min_length=20, max_length=64),
):
    try:
        return _router_for_symbol(symbol).fetch_bars(symbol, start, end)
    except MarketDataUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (ValueError, TypeError, OverflowError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/market-data/provider-readiness")
def market_data_provider_readiness():
    return provider_readiness()
