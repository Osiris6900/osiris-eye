"""Authenticated-by-parent-middleware research-only intelligence endpoints."""
from fastapi import APIRouter
from pydantic import BaseModel, Field

from .satellite_arb import compare_routes
from .neural_field import SentimentResearch

router = APIRouter(prefix="/api/v1/intelligence", tags=["Intelligence research"])
_sentiment = SentimentResearch()


class RouteComparison(BaseModel):
    lat_a: float = Field(ge=-90, le=90)
    lon_a: float = Field(ge=-180, le=180)
    lat_b: float = Field(ge=-90, le=90)
    lon_b: float = Field(ge=-180, le=180)
    fiber_path_factor: float = Field(default=1.3, ge=1, le=10)
    satellite_path_factor: float = Field(default=1.5, ge=1, le=10)


class SentimentInput(BaseModel):
    source: str = Field(min_length=1, max_length=100)
    asset: str = Field(min_length=1, max_length=40)
    sentiment: float = Field(ge=-1, le=1, allow_inf_nan=False)
    intensity: float = Field(ge=0, le=1, allow_inf_nan=False)
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)


@router.post("/satellite/compare-routes")
def satellite_compare(request: RouteComparison):
    return compare_routes(**request.model_dump())


@router.post("/sentiment/ingest")
def ingest_sentiment(request: SentimentInput):
    return _sentiment.ingest(**request.model_dump())


@router.get("/dashboard/intelligence-overview")
def intelligence_overview():
    return {
        "mode": "research_only",
        "satellite": {"telemetry_connected": False, "arbitrage_verified": False},
        "sentiment": _sentiment.summary(),
        "execution_enabled": False,
    }
