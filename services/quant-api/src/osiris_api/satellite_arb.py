"""Offline route-latency comparison. No satellite telemetry or trading."""
from math import asin, cos, radians, sin, sqrt
from datetime import datetime, timezone

FIBER_KM_S = 200_000.0
VACUUM_KM_S = 299_792.458


def compare_routes(lat_a: float, lon_a: float, lat_b: float, lon_b: float,
                   fiber_path_factor: float = 1.3, satellite_path_factor: float = 1.5) -> dict:
    """Illustrative one-way propagation lower bounds, not clock skew or alpha."""
    if not (-90 <= lat_a <= 90 and -90 <= lat_b <= 90
            and -180 <= lon_a <= 180 and -180 <= lon_b <= 180):
        raise ValueError("invalid coordinates")
    if not (1 <= fiber_path_factor <= 10 and 1 <= satellite_path_factor <= 10):
        raise ValueError("invalid path factor")
    a = sin(radians(lat_b - lat_a) / 2) ** 2 + cos(radians(lat_a)) * cos(radians(lat_b)) * sin(radians(lon_b - lon_a) / 2) ** 2
    distance_km = 6371.0 * 2 * asin(min(1.0, sqrt(max(0.0, a))))
    fiber_us = distance_km * fiber_path_factor / FIBER_KM_S * 1e6
    satellite_us = distance_km * satellite_path_factor / VACUUM_KM_S * 1e6
    return {
        "mode": "simulation_only", "source": "theoretical_propagation",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "distance_km": round(distance_km, 2),
        "fiber_delay_us": round(fiber_us, 2),
        "satellite_delay_us": round(satellite_us, 2),
        "modeled_difference_us": round(fiber_us - satellite_us, 2),
        "clock_skew_measured": False, "arbitrage_verified": False,
        "execution_enabled": False,
    }
