"""Discoverable, caller-data market mechanics research diagnostics."""

from fastapi import APIRouter

from .market_mechanics_flows import CATALOG as FLOW_CATALOG
from .market_mechanics_flows import router as flow_router
from .market_mechanics_microstructure import CATALOG as MICROSTRUCTURE_CATALOG
from .market_mechanics_microstructure import router as microstructure_router

CATALOG = [*MICROSTRUCTURE_CATALOG, *FLOW_CATALOG]

router = APIRouter()
router.include_router(microstructure_router)
router.include_router(flow_router)


@router.get("/research/market-mechanics/catalog", tags=["Market mechanics"])
def market_mechanics_catalog():
    return {
        "count": len(CATALOG),
        "modules": CATALOG,
        "research_only": True,
        "execution_enabled": False,
    }
