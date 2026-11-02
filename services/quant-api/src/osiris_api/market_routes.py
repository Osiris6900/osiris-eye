"""Authenticated HTTP routes for the provider-neutral market-data gateway."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from .market_gateway import MarketDataGateway, MarketDataUnavailable

router = APIRouter(tags=["market-data"])
# Deliberately empty by default. Operators must register an adapter and entitlement
# reference in the running application before any provider can serve data.
market_data_gateway = MarketDataGateway()


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
):
    """Return normalized historical OHLCV bars from an enabled sandbox adapter."""
    try:
        return market_data_gateway.fetch_bars(provider, symbol, start, end)
    except MarketDataUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (ValueError, TypeError, OverflowError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
