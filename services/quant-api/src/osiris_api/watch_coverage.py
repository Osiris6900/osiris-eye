"""Coverage evidence from a market watchlist and the quotes actually returned.

This module has no collector dependencies, so collection, daemon and P2P bridge
paths can all use the same report without creating import cycles. Coverage is
about fetched observations, not ingestion authorization or remote delivery.
"""
from __future__ import annotations

from typing import Any

_USD_FX_PROVIDERS = frozenset({"frankfurter-ecb", "erapi-public"})


def _canonical_instrument(provider: str, symbol: str) -> str:
    # Match collector instrument names: FX inputs are currencies, while Stooq
    # tickers and crypto symbols are stripped and uppercased by their adapters.
    instrument = symbol.strip().upper()
    return f"USD-{instrument}" if provider in _USD_FX_PROVIDERS else instrument


def coverage_report(
    watchlist: dict[str, tuple[str, ...]],
    collected: dict[str, Any],
) -> dict[str, Any]:
    """Report fetched coverage, including instruments that disappeared entirely.

    Only observations from a watched provider for an instrument requested from
    that provider count. An unrelated quote cannot conceal a blind spot. The
    same instrument returned by multiple providers counts once in instrument
    totals, while retaining each provider in ``instrument_sources``.
    """
    expected_by_source = {
        provider: {_canonical_instrument(provider, symbol) for symbol in symbols}
        for provider, symbols in watchlist.items()
    }
    watched = set().union(*expected_by_source.values()) if expected_by_source else set()
    errors = collected.get("errors", [])
    error_by_source = {
        str(item.get("source", "")): str(item.get("error", ""))
        for item in errors if isinstance(item, dict)
    }

    per_source: dict[str, dict[str, Any]] = {
        provider: {"quotes": 0, "status": "ok", "error": error_by_source.get(provider)}
        for provider in sorted(watchlist)
    }
    instruments: dict[str, set[str]] = {}
    for observation in collected.get("observations", []):
        if not isinstance(observation, dict):
            continue
        provider = observation.get("provider")
        instrument = observation.get("instrument")
        if not isinstance(provider, str) or not isinstance(instrument, str):
            continue
        if provider not in expected_by_source or instrument not in expected_by_source[provider]:
            continue
        per_source[provider]["quotes"] += 1
        instruments.setdefault(instrument, set()).add(provider)

    failed, silent = [], []
    for provider, info in per_source.items():
        if info["error"] is not None:
            info["status"] = "error"
            failed.append(provider)
        elif info["quotes"] == 0:
            info["status"] = "silent"
            silent.append(provider)

    missing = sorted(watched - instruments.keys())
    return {
        "sources_watched": len(per_source),
        "sources_ok": sum(info["status"] == "ok" for info in per_source.values()),
        "failed_sources": failed,
        "silent_sources": silent,
        "per_source": per_source,
        "instruments_watched": len(watched),
        "instruments_covered": len(instruments),
        "instruments_missing": len(missing),
        "missing_instruments": missing,
        "instrument_sources": {
            instrument: sorted(instruments[instrument]) for instrument in sorted(instruments)
        },
        "single_source_instruments": sorted(
            instrument for instrument, providers in instruments.items() if len(providers) == 1
        ),
        "live_execution": False,
    }
