"""Persistent instrument master HTTP API. Reads are seeded from the checked-in fixture."""
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from .instrument_master import InstrumentMasterStore, instrument_db_path

router = APIRouter(tags=["instrument-master"])


def _store() -> InstrumentMasterStore:
    store = InstrumentMasterStore(instrument_db_path())
    store.ensure_seeded()
    return store


@router.get("/market-data/instruments/catalog")
def instrument_catalog():
    """Read-only security master. This route does not accept writes or live orders."""
    store = _store()
    items = store.list_master()
    actions = store.list_corporate_actions()
    return {
        "catalog_version": store.catalog_version(),
        "count": len(items),
        "instruments": items,
        "corporate_actions": actions,
        "corporate_actions_applied_to_positions": False,
        "trading_hours_enforced": True,
        "exchange_holidays_enforced": False,
        "catalog_persisted": True,
        "exchange_connectivity_verified": False,
        "live_execution": False,
        "live_trading_authorized": False,
    }


@router.post("/market-data/instruments")
def put_instrument(payload: dict[str, Any]):
    try:
        return _store().put(
            payload.get("instrument", {}),
            source=payload.get("source", "operator"),
            effective_at=payload.get("effective_at"),
        )
    except (ValueError, TypeError, OverflowError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/market-data/instruments")
def search_instruments(
    asset_class: str | None = None,
    venue: str | None = None,
    symbol: str | None = None,
    as_of: str | None = None,
    limit: int = Query(100, ge=1, le=1000),
):
    try:
        store = _store()
        items = store.search(
            asset_class=asset_class, venue=venue, symbol=symbol, as_of=as_of, limit=limit,
        )
        return {
            "count": len(items),
            "instruments": items,
            "catalog_version": store.catalog_version(),
            "catalog_persisted": True,
            "exchange_connectivity_verified": False,
            "live_execution": False,
            "live_trading_authorized": False,
        }
    except (ValueError, TypeError, OverflowError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/market-data/instruments/provider-mappings/resolve")
def resolve_mapping(provider: str, provider_symbol: str, as_of: str | None = None):
    try:
        result = _store().resolve(provider, provider_symbol, as_of=as_of)
        if result is None:
            raise HTTPException(404, "provider_mapping_not_found")
        return {"provider": provider, "provider_symbol": provider_symbol, "instrument_id": result}
    except (ValueError, TypeError, OverflowError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/market-data/instruments/provider-mappings")
def put_mapping(payload: dict[str, Any]):
    try:
        return _store().put_mapping(
            provider=payload.get("provider"),
            provider_symbol=payload.get("provider_symbol"),
            instrument_id=payload.get("instrument_id"),
            source=payload.get("source", "operator"),
            effective_at=payload.get("effective_at"),
        )
    except (ValueError, TypeError, OverflowError) as exc:
        raise HTTPException(422, str(exc)) from exc
