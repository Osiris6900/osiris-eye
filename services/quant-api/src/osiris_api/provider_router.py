"""Deterministic, fail-closed multi-provider routing for sandbox market data."""
from __future__ import annotations

from typing import Any

from .market_gateway import MarketDataGateway, MarketDataUnavailable


class MultiProviderMarketDataRouter:
    """Try explicitly ordered providers; never downgrade data-validation failures."""

    def __init__(self, gateway: MarketDataGateway, provider_order: list[str]) -> None:
        if not isinstance(provider_order, list) or not provider_order:
            raise ValueError("provider_order_must_not_be_empty")
        normalized = [item.strip() for item in provider_order if isinstance(item, str)]
        if len(normalized) != len(provider_order) or any(not item for item in normalized):
            raise ValueError("invalid_provider_name")
        if len(set(normalized)) != len(normalized):
            raise ValueError("duplicate_provider_in_order")
        self._gateway = gateway
        self._provider_order = tuple(normalized)

    def providers(self) -> dict[str, Any]:
        status = self._gateway.providers()
        by_name = {item["provider"]: item for item in status["providers"]}
        ordered = []
        for priority, name in enumerate(self._provider_order, start=1):
            item = dict(by_name.get(name, {
                "provider": name, "mode": None, "configured": False,
                "operator_entitlement_reference_present": False, "available": False,
            }))
            item["priority"] = priority
            ordered.append(item)
        return {
            "providers": ordered,
            "routing_policy": "priority_order_failover",
            "live_market_data_enabled": False,
            "note": "Availability means sandbox/synthetic adapter plus operator reference; licensing is not independently verified.",
        }

    def fetch_bars(self, symbol: str, start: str, end: str) -> dict[str, Any]:
        failures: list[dict[str, str]] = []
        for provider in self._provider_order:
            try:
                result = self._gateway.fetch_bars(provider, symbol, start, end)
                result["routing"] = {
                    "policy": "priority_order_failover",
                    "selected_provider": provider,
                    "prior_provider_failures": failures,
                    "live_execution": False,
                }
                return result
            except MarketDataUnavailable as exc:
                failures.append({"provider": provider, "reason": str(exc)})
        raise MarketDataUnavailable("all_configured_providers_unavailable")
