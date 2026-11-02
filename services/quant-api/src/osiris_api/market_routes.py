"""Authenticated HTTP routes for the provider-neutral market-data gateway."""
from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from .market_gateway import MarketDataGateway, MarketDataUnavailable
from .market_universe import ASSET_CLASSES, audit_instruments
from .provider_bootstrap import provider_readiness
from .provider_router import MultiProviderMarketDataRouter
from .public_market_data import CoinbasePublicMarketDataAdapter

router = APIRouter(tags=["market-data"])
market_data_gateway = MarketDataGateway()

# Public Coinbase candles need no API key. Operators can disable outbound access.
if os.getenv("OSIRIS_PUBLIC_COINBASE_ENABLED", "true").strip().lower() in {"1", "true", "yes"}:
    granularity = int(os.getenv("OSIRIS_PUBLIC_COINBASE_GRANULARITY", "60"))
    market_data_gateway.register_adapter(
        "coinbase-public", CoinbasePublicMarketDataAdapter(granularity=granularity),
        supported_asset_classes=["crypto"],
        entitlement_reference="public-data:coinbase-exchange",
    )


@router.get("/market-data/asset-classes")
def market_data_asset_classes():
    """Return supported cross-asset taxonomy; this is not a connectivity claim."""
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


_provider_order = [
    item.strip() for item in os.getenv("OSIRIS_MARKET_PROVIDER_ORDER", "").split(",")
    if item.strip()
]
try:
    multi_provider_router = MultiProviderMarketDataRouter(market_data_gateway, _provider_order) if _provider_order else None
except ValueError:
    multi_provider_router = None


@router.get("/market-data/routing/providers")
def market_data_routing_providers():
    if multi_provider_router is None:
        return {"providers": [], "routing_policy": "not_configured", "live_market_data_enabled": False}
    return multi_provider_router.providers()


@router.get("/market-data/routing/bars")
def market_data_routed_bars(
    symbol: str = Query(..., min_length=1, max_length=64),
    start: str = Query(..., min_length=20, max_length=64),
    end: str = Query(..., min_length=20, max_length=64),
):
    if multi_provider_router is None:
        raise HTTPException(status_code=503, detail="multi_provider_routing_not_configured")
    try:
        return multi_provider_router.fetch_bars(symbol, start, end)
    except MarketDataUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (ValueError, TypeError, OverflowError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/market-data/provider-readiness")
def market_data_provider_readiness():
    return provider_readiness()
