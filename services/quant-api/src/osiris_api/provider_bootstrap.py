"""Operator-safe provider onboarding catalog.

This module reports configuration readiness only. It never loads or returns secret
values, instantiates vendor SDKs, or claims that a provider is reachable.
"""
from __future__ import annotations

import os
from typing import Mapping

# These are onboarding profiles, not claims of a working vendor adapter.
# Asset coverage is deliberately conservative and must be checked against each
# account's product and exchange entitlements before use.
PROVIDER_PROFILES: tuple[dict[str, object], ...] = (
    {
        "provider": "interactive_brokers",
        "label": "Interactive Brokers",
        "asset_classes": ["equity", "fx", "future", "option", "commodity", "index", "fixed_income"],
        "required_env": ["OSIRIS_IBKR_HOST", "OSIRIS_IBKR_PORT", "OSIRIS_IBKR_CLIENT_ID"],
        "integration_status": "adapter_not_implemented",
    },
    {
        "provider": "alpaca",
        "label": "Alpaca",
        "asset_classes": ["equity", "crypto", "option"],
        "required_env": ["OSIRIS_ALPACA_API_KEY", "OSIRIS_ALPACA_API_SECRET"],
        "integration_status": "adapter_not_implemented",
    },
    {
        "provider": "binance",
        "label": "Binance",
        "asset_classes": ["crypto"],
        "required_env": ["OSIRIS_BINANCE_API_KEY", "OSIRIS_BINANCE_API_SECRET"],
        "integration_status": "adapter_not_implemented",
    },
    {
        "provider": "coinbase",
        "label": "Coinbase",
        "asset_classes": ["crypto"],
        "required_env": ["OSIRIS_COINBASE_API_KEY", "OSIRIS_COINBASE_API_SECRET"],
        "integration_status": "adapter_not_implemented",
    },
    {
        "provider": "oanda",
        "label": "OANDA",
        "asset_classes": ["fx"],
        "required_env": ["OSIRIS_OANDA_API_TOKEN", "OSIRIS_OANDA_ACCOUNT_ID"],
        "integration_status": "adapter_not_implemented",
    },
    {
        "provider": "polygon",
        "label": "Polygon",
        "asset_classes": ["equity", "option", "index", "fx", "crypto"],
        "required_env": ["OSIRIS_POLYGON_API_KEY"],
        "integration_status": "adapter_not_implemented",
    },
    {
        "provider": "twelvedata",
        "label": "Twelve Data",
        "asset_classes": ["equity", "fx", "crypto", "index"],
        "required_env": ["OSIRIS_TWELVEDATA_API_KEY"],
        "integration_status": "adapter_not_implemented",
    },
)


def provider_readiness(environ: Mapping[str, str] | None = None) -> dict[str, object]:
    """Return provider onboarding status without exposing credential material."""
    env = os.environ if environ is None else environ
    providers = []
    for profile in PROVIDER_PROFILES:
        required = list(profile["required_env"])
        present = [name for name in required if bool(env.get(name, "").strip())]
        missing = [name for name in required if name not in present]
        providers.append({
            "provider": profile["provider"],
            "label": profile["label"],
            "supported_asset_classes": list(profile["asset_classes"]),
            "required_environment_variables": required,
            "configured": not missing,
            "configured_variable_count": len(present),
            "required_variable_count": len(required),
            "missing_environment_variables": missing,
            "integration_status": profile["integration_status"],
            "connectivity_verified": False,
            "market_data_enabled": False,
        })
    return {
        "providers": providers,
        "provider_count": len(providers),
        "configured_provider_count": sum(bool(item["configured"]) for item in providers),
        "connectivity_verified": False,
        "live_market_data_enabled": False,
        "live_execution": False,
        "note": (
            "Configuration presence is not a connectivity, licensing, entitlement, "
            "or production-readiness check. No vendor adapter is activated by this catalog."
        ),
    }
