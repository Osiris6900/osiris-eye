"""Provider-neutral, fail-closed market data gateway."""
from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any

from .provider_connectors import ProviderRegistry, SUPPORTED_ASSET_CLASSES


class MarketDataUnavailable(RuntimeError):
    """No configured, entitled, supported adapter can serve the request."""


def parse_market_time(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp_must_be_iso8601_with_timezone")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("timestamp_must_be_iso8601_with_timezone") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp_must_be_iso8601_with_timezone")
    return parsed.astimezone(timezone.utc)


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"invalid_{field}")
    try:
        number = float(value)
    except OverflowError as exc:
        raise ValueError(f"invalid_{field}") from exc
    if not math.isfinite(number):
        raise ValueError(f"invalid_{field}")
    return number


def normalize_bar(record: dict[str, Any], requested_symbol: str) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise ValueError("invalid_bar")
    symbol = str(record.get("symbol", "")).strip().upper()
    expected = requested_symbol.strip().upper()
    if not symbol or symbol != expected or len(symbol) > 64:
        raise ValueError("symbol_mismatch")
    event_time = parse_market_time(record.get("event_time")).isoformat()
    available_at = parse_market_time(record.get("available_at")).isoformat()
    if parse_market_time(available_at) < parse_market_time(event_time):
        raise ValueError("availability_before_event")
    values = {key: _finite_number(record.get(key), key)
              for key in ("open", "high", "low", "close", "volume")}
    if min(values[key] for key in ("open", "high", "low", "close")) <= 0:
        raise ValueError("prices_must_be_positive")
    if values["volume"] < 0:
        raise ValueError("volume_must_be_non_negative")
    if values["high"] < max(values["open"], values["close"], values["low"]):
        raise ValueError("invalid_ohlc_high")
    if values["low"] > min(values["open"], values["close"], values["high"]):
        raise ValueError("invalid_ohlc_low")
    return {"symbol": symbol, "event_time": event_time, "available_at": available_at, **values}


class MarketDataGateway:
    """Routes requests through a registered, capability-checked provider allow-list."""

    def __init__(self, adapters: dict[str, Any] | None = None,
                 entitlement_references: dict[str, str] | None = None,
                 supported_asset_classes: dict[str, list[str] | tuple[str, ...]] | None = None,
                 credential_references: dict[str, str] | None = None) -> None:
        self.registry = ProviderRegistry()
        entitlements = entitlement_references or {}
        credentials = credential_references or {}
        classes = supported_asset_classes or {}
        for name, adapter in (adapters or {}).items():
            # Compatibility defaults keep existing test/sandbox adapters working;
            # production registrations should declare their exact asset coverage.
            self.registry.register(
                name, adapter,
                supported_asset_classes=classes.get(name, tuple(SUPPORTED_ASSET_CLASSES)),
                entitlement_reference=entitlements.get(name),
                credential_reference=credentials.get(name),
            )

    def register_adapter(self, provider_id: str, adapter: Any, *,
                         supported_asset_classes: list[str] | tuple[str, ...],
                         entitlement_reference: str | None,
                         credential_reference: str | None = None) -> None:
        """Register an adapter using secret references, never raw credential values."""
        self.registry.register(
            provider_id, adapter,
            supported_asset_classes=supported_asset_classes,
            entitlement_reference=entitlement_reference,
            credential_reference=credential_reference,
        )

    def providers(self) -> dict[str, Any]:
        return self.registry.describe()

    def fetch_bars(self, provider: str, symbol: str, start: str, end: str,
                   asset_class: str | None = None) -> dict[str, Any]:
        registration = self.registry.get(provider)
        if registration is None:
            raise MarketDataUnavailable("provider_adapter_not_configured")
        adapter = registration.adapter
        if not registration.entitlement_reference:
            raise MarketDataUnavailable("provider_entitlement_reference_missing")
        if adapter.mode not in {"synthetic", "sandbox"}:
            raise MarketDataUnavailable("live_provider_mode_disabled")
        if asset_class is not None and asset_class not in registration.supported_asset_classes:
            raise MarketDataUnavailable("provider_asset_class_not_supported")
        normalized_symbol = symbol.strip().upper()
        if not normalized_symbol or len(normalized_symbol) > 64:
            raise ValueError("invalid_symbol")
        start_dt, end_dt = parse_market_time(start), parse_market_time(end)
        if end_dt <= start_dt:
            raise ValueError("end_must_be_after_start")
        try:
            raw_bars = adapter.fetch_bars(normalized_symbol, start_dt.isoformat(), end_dt.isoformat())
        except Exception as exc:
            raise MarketDataUnavailable("provider_fetch_failed") from exc
        if not isinstance(raw_bars, list) or len(raw_bars) > 50000:
            raise ValueError("invalid_provider_response")
        bars = [normalize_bar(item, normalized_symbol) for item in raw_bars]
        seen: set[str] = set()
        for bar in bars:
            event_time = bar["event_time"]
            if event_time in seen:
                raise ValueError("duplicate_bar_timestamp")
            seen.add(event_time)
            instant = parse_market_time(event_time)
            if instant < start_dt or instant > end_dt:
                raise ValueError("bar_outside_requested_range")
        bars.sort(key=lambda item: item["event_time"])
        return {
            "provider": provider,
            "mode": adapter.mode,
            "asset_class": asset_class,
            "symbol": normalized_symbol,
            "bars_returned": len(bars),
            "bars": bars,
            "entitlement": "operator_reference_present_not_independently_verified",
            "live_execution": False,
        }
