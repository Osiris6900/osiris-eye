"""Provider-agnostic multi-source market data aggregation.

The aggregator preserves provider provenance and never silently averages prices
from distinct venues. It selects a primary series and reports cross-source
divergence for aligned candle timestamps.
"""
from __future__ import annotations

from typing import Any, Sequence

from .market_gateway import MarketDataGateway, MarketDataUnavailable


class UniversalMarketDataAggregator:
    """Fetch, normalize, compare and quality-audit bars from multiple providers."""

    def __init__(self, gateway: MarketDataGateway, *, divergence_threshold_bps: float = 50.0):
        if isinstance(divergence_threshold_bps, bool) or not isinstance(
            divergence_threshold_bps, (int, float)
        ) or not 0 <= float(divergence_threshold_bps) <= 10000:
            raise ValueError("invalid_divergence_threshold_bps")
        self.gateway = gateway
        self.divergence_threshold_bps = float(divergence_threshold_bps)

    def aggregate_bars(
        self, *, providers: Sequence[str], symbol: str, start: str, end: str,
        asset_class: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(providers, (list, tuple)) or not providers:
            raise ValueError("providers_must_not_be_empty")
        ordered = [p.strip() for p in providers if isinstance(p, str)]
        if len(ordered) != len(providers) or any(not p for p in ordered):
            raise ValueError("invalid_provider_name")
        if len(set(ordered)) != len(ordered):
            raise ValueError("duplicate_provider_in_list")

        datasets: dict[str, dict[str, Any]] = {}
        failures: list[dict[str, str]] = []
        for provider in ordered:
            try:
                result = self.gateway.fetch_bars(
                    provider, symbol, start, end, asset_class=asset_class
                )
                datasets[provider] = result
            except MarketDataUnavailable as exc:
                failures.append({"provider": provider, "reason": str(exc)})

        if not datasets:
            raise MarketDataUnavailable("all_aggregation_providers_unavailable")

        primary_provider = next(p for p in ordered if p in datasets)
        primary_bars = datasets[primary_provider]["bars"]
        indexed: dict[str, dict[str, dict[str, Any]]] = {
            provider: {bar["event_time"]: bar for bar in result["bars"]}
            for provider, result in datasets.items()
        }
        aligned_times = sorted(
            set.intersection(*(set(rows) for rows in indexed.values()))
        ) if len(indexed) > 1 else []
        divergence: list[dict[str, Any]] = []
        for timestamp in aligned_times:
            closes = {provider: indexed[provider][timestamp]["close"] for provider in indexed}
            reference = closes[primary_provider]
            if reference <= 0:
                continue
            for provider, close in closes.items():
                if provider == primary_provider:
                    continue
                deviation_bps = abs(close - reference) / reference * 10000
                if deviation_bps > self.divergence_threshold_bps:
                    divergence.append({
                        "event_time": timestamp,
                        "reference_provider": primary_provider,
                        "comparison_provider": provider,
                        "reference_close": reference,
                        "comparison_close": close,
                        "deviation_bps": round(deviation_bps, 4),
                    })

        provider_summary = []
        for provider in ordered:
            if provider in datasets:
                result = datasets[provider]
                provider_summary.append({
                    "provider": provider, "status": "ok",
                    "mode": result["mode"], "bars_returned": result["bars_returned"],
                })
            else:
                reason = next(item["reason"] for item in failures if item["provider"] == provider)
                provider_summary.append({
                    "provider": provider, "status": "unavailable", "reason": reason,
                })

        return {
            "aggregator": "universal_market_data_v1",
            "symbol": symbol.strip().upper(),
            "asset_class": asset_class,
            "primary_provider": primary_provider,
            "bars": primary_bars,
            "bars_returned": len(primary_bars),
            "provider_datasets": datasets,
            "providers": provider_summary,
            "quality": {
                "provider_count_requested": len(ordered),
                "provider_count_available": len(datasets),
                "aligned_candle_count": len(aligned_times),
                "divergence_threshold_bps": self.divergence_threshold_bps,
                "divergence_count": len(divergence),
                "divergences": divergence[:500],
                "primary_series_policy": "first_successful_provider_in_requested_order",
                "price_blending": False,
            },
            "failures": failures,
            "live_execution": False,
            "data_note": "Provider coverage and licensing vary; this endpoint does not imply global real-time coverage.",
        }
