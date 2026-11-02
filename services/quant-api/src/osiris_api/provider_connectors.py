"""Multi-provider connector registry and capability contracts.

Credentials are referenced by secret-manager/environment variable identifiers only;
secret material must never be passed into or returned from this registry.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Protocol

SUPPORTED_ASSET_CLASSES = frozenset({
    "equity", "crypto", "fx", "future", "option",
    "commodity", "index", "fixed_income",
})
SUPPORTED_MODES = frozenset({"synthetic", "sandbox", "live"})
_PROVIDER_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


class BarProviderAdapter(Protocol):
    """Minimal provider adapter contract; implementations normalize upstream data."""

    name: str
    mode: str

    def fetch_bars(self, symbol: str, start: str, end: str) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class ProviderRegistration:
    provider_id: str
    adapter: BarProviderAdapter
    supported_asset_classes: tuple[str, ...]
    entitlement_reference: str | None
    credential_reference: str | None


class ProviderRegistry:
    """In-memory, explicit allow-list of provider adapters and their capabilities."""

    def __init__(self) -> None:
        self._providers: dict[str, ProviderRegistration] = {}

    def register(
        self,
        provider_id: str,
        adapter: BarProviderAdapter,
        *,
        supported_asset_classes: tuple[str, ...] | list[str],
        entitlement_reference: str | None,
        credential_reference: str | None = None,
    ) -> ProviderRegistration:
        if not isinstance(provider_id, str) or not _PROVIDER_ID.fullmatch(provider_id):
            raise ValueError("invalid_provider_id")
        if provider_id in self._providers:
            raise ValueError("provider_already_registered")
        mode = getattr(adapter, "mode", None)
        if mode not in SUPPORTED_MODES:
            raise ValueError("unsupported_provider_mode")
        if not callable(getattr(adapter, "fetch_bars", None)):
            raise ValueError("adapter_fetch_bars_required")
        classes = tuple(sorted(set(supported_asset_classes)))
        if not classes or any(item not in SUPPORTED_ASSET_CLASSES for item in classes):
            raise ValueError("unsupported_asset_class")
        for reference in (entitlement_reference, credential_reference):
            if reference is not None and (
                not isinstance(reference, str) or not reference.strip() or len(reference) > 256
            ):
                raise ValueError("invalid_secret_reference")
        registration = ProviderRegistration(
            provider_id=provider_id,
            adapter=adapter,
            supported_asset_classes=classes,
            entitlement_reference=entitlement_reference.strip() if entitlement_reference else None,
            credential_reference=credential_reference.strip() if credential_reference else None,
        )
        self._providers[provider_id] = registration
        return registration

    def get(self, provider_id: str) -> ProviderRegistration | None:
        return self._providers.get(provider_id)

    def describe(self) -> dict[str, Any]:
        result = []
        for provider_id, registration in sorted(self._providers.items()):
            mode = registration.adapter.mode
            entitled = bool(registration.entitlement_reference)
            result.append({
                "provider": provider_id,
                "mode": mode,
                "configured": True,
                "supported_asset_classes": list(registration.supported_asset_classes),
                "credential_reference_present": bool(registration.credential_reference),
                "operator_entitlement_reference_present": entitled,
                "available": entitled and mode in {"synthetic", "sandbox"},
            })
        return {
            "providers": result,
            "supported_asset_classes": sorted(SUPPORTED_ASSET_CLASSES),
            "live_market_data_enabled": False,
            "note": "References are operator-supplied identifiers; credentials and entitlements are not independently verified.",
        }
