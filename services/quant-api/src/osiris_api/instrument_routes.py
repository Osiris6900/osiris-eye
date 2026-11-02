"""Persistent instrument master HTTP API."""
from typing import Any
from fastapi import APIRouter, HTTPException, Query
from .instrument_master import InstrumentMasterStore

router = APIRouter(tags=["instrument-master"])
instrument_master = InstrumentMasterStore()

@router.post("/market-data/instruments")
def put_instrument(payload: dict[str, Any]):
    try:
        return instrument_master.put(payload.get("instrument", {}),
            source=payload.get("source","operator"), effective_at=payload.get("effective_at"))
    except (ValueError, TypeError, OverflowError) as exc:
        raise HTTPException(422, str(exc)) from exc

@router.get("/market-data/instruments")
def search_instruments(asset_class: str | None = None, venue: str | None = None,
        symbol: str | None = None, as_of: str | None = None,
        limit: int = Query(100, ge=1, le=1000)):
    try:
        items=instrument_master.search(asset_class=asset_class,venue=venue,symbol=symbol,
            as_of=as_of,limit=limit)
        return {"count":len(items),"instruments":items,"catalog_persisted":True,
            "exchange_connectivity_verified":False,"live_execution":False}
    except (ValueError, TypeError, OverflowError) as exc:
        raise HTTPException(422,str(exc)) from exc

@router.get("/market-data/instruments/provider-mappings/resolve")
def resolve_mapping(provider: str, provider_symbol: str, as_of: str | None = None):
    try:
        result=instrument_master.resolve(provider,provider_symbol,as_of=as_of)
        if result is None: raise HTTPException(404,"provider_mapping_not_found")
        return {"provider":provider,"provider_symbol":provider_symbol,"instrument_id":result}
    except (ValueError, TypeError, OverflowError) as exc:
        raise HTTPException(422,str(exc)) from exc

@router.post("/market-data/instruments/provider-mappings")
def put_mapping(payload: dict[str, Any]):
    try:
        return instrument_master.put_mapping(provider=payload.get("provider"),
            provider_symbol=payload.get("provider_symbol"),instrument_id=payload.get("instrument_id"),
            source=payload.get("source","operator"),effective_at=payload.get("effective_at"))
    except (ValueError, TypeError, OverflowError) as exc:
        raise HTTPException(422,str(exc)) from exc
