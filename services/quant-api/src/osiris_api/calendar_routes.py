"""HTTP API for operator-supplied, versioned exchange calendars."""
from typing import Any
from fastapi import APIRouter, HTTPException, Query
from .exchange_calendar import ExchangeCalendarStore

router = APIRouter(tags=["exchange-calendar"])
calendar_store = ExchangeCalendarStore()

@router.post("/market-data/calendars/sessions")
def import_calendar_record(payload: dict[str, Any]):
    try:
        return calendar_store.put(payload.get("record", {}),
                                  effective_at=payload.get("effective_at"))
    except (ValueError, TypeError, OverflowError) as exc:
        raise HTTPException(422, str(exc)) from exc

@router.get("/market-data/calendars/sessions")
def get_calendar_record(venue: str = Query(..., min_length=1, max_length=64),
                        calendar_date: str = Query(...), as_of: str | None = None):
    try:
        result = calendar_store.get(venue, calendar_date, as_of)
        if result is None:
            raise HTTPException(404, "calendar_record_not_found")
        return result
    except (ValueError, TypeError, OverflowError) as exc:
        raise HTTPException(422, str(exc)) from exc

@router.get("/market-data/calendars/status")
def get_market_status(venue: str = Query(..., min_length=1, max_length=64),
                      at: str = Query(...), as_of: str | None = None):
    try:
        return calendar_store.status(venue, at, as_of)
    except (ValueError, TypeError, OverflowError) as exc:
        raise HTTPException(422, str(exc)) from exc
