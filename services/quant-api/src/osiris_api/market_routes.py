"""Authenticated HTTP routes for the provider-neutral market-data gateway."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from .market_gateway import MarketDataGateway, MarketDataUnavailable
from .market_universe import ASSET_CLASSES, audit_instruments

router = APIRouter(tags=["market-data"])
# Deliberately empty by default. Operators must register an adapter and entitlement
# reference in the running application before any provider can serve data.
market_data_gateway = MarketDataGateway()


@router.get("/market-data/asset-classes")
def market_data_asset_classes():
    """Return supported cross-asset taxonomy; this is not a connectivity claim."""
    return {
        "asset_classes": list(ASSET_CLASSES),
        "catalog_persisted": False,
        "exchange_connectivity_verified": False,
        "live_execution": False,
    }


@router.post("/market-data/instruments/audit")
def market_data_instruments_audit(payload: dict[str, Any]):
    """Validate canonical instrument metadata without persisting it."""
    instruments = payload.get("instruments")
    if not isinstance(instruments, list):
        raise HTTPException(status_code=422, detail="instruments_must_be_list")
    try:
        return audit_instruments(instruments)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/market-data/providers")
def market_data_providers():
    """Return configured provider capabilities without exposing credentials."""
    return market_data_gateway.providers()


@router.get("/market-data/bars")
def market_data_bars(
    provider: str = Query(..., min_length=1, max_length=100),
    symbol: str = Query(..., min_length=1, max_length=64),
    start: str = Query(..., min_length=20, max_length=64),
    end: str = Query(..., min_length=20, max_length=64),
    asset_class: str | None = Query(default=None, min_length=1, max_length=32),
):
    """Return normalized historical OHLCV bars from an enabled sandbox adapter."""
    try:
        return market_data_gateway.fetch_bars(provider, symbol, start, end, asset_class=asset_class)
    except MarketDataUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (ValueError, TypeError, OverflowError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
