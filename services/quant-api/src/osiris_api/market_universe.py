"""Cross-asset instrument metadata validation for the global market universe.

This module defines a provider-neutral catalog contract. It does not imply that
any exchange, broker, or data vendor is connected or licensed.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ASSET_CLASSES = (
    {"id": "equity", "label": "Equities", "examples": ["NSE", "BSE", "NYSE", "NASDAQ"]},
    {"id": "crypto", "label": "Crypto", "examples": ["Spot", "Centralized exchanges", "Decentralized venues"]},
    {"id": "forex", "label": "Foreign exchange", "examples": ["Spot FX", "FX forwards"]},
    {"id": "future", "label": "Futures", "examples": ["Equity index", "Energy", "Metals", "Agriculture"]},
    {"id": "option", "label": "Options", "examples": ["Equity", "Index", "FX", "Commodity"]},
    {"id": "commodity", "label": "Cash commodities", "examples": ["Energy", "Metals", "Agriculture"]},
    {"id": "index", "label": "Indices", "examples": ["Equity", "Volatility", "Rates"]},
    {"id": "rates", "label": "Fixed income and rates", "examples": ["Government bonds", "Money markets", "Swaps"]},
)
_ASSET_CLASS_IDS = {item["id"] for item in ASSET_CLASSES}


def _text(value: Any, field: str, *, upper: bool = False, max_length: int = 64) -> str:
    if not isinstance(value, str):
        raise ValueError(f"invalid_{field}")
    result = value.strip()
    if not result or len(result) > max_length:
        raise ValueError(f"invalid_{field}")
    return result.upper() if upper else result


def _positive_decimal(value: Any, field: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError(f"invalid_{field}")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"invalid_{field}") from None
    if not number.is_finite() or number <= 0:
        raise ValueError(f"invalid_{field}")
    return format(number.normalize(), "f")


def normalize_instrument(record: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise ValueError("invalid_instrument")
    asset_class = _text(record.get("asset_class"), "asset_class", max_length=24).lower()
    if asset_class not in _ASSET_CLASS_IDS:
        raise ValueError("unsupported_asset_class")
    venue = _text(record.get("venue"), "venue", upper=True)
    symbol = _text(record.get("symbol"), "symbol", upper=True)
    quote_currency = _text(record.get("quote_currency"), "quote_currency", upper=True, max_length=12)
    timezone_name = _text(record.get("timezone"), "timezone", max_length=64)
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        raise ValueError("invalid_timezone") from None
    session_type = _text(record.get("session_type", "exchange"), "session_type", max_length=16).lower()
    if session_type not in {"exchange", "continuous", " otc"} and session_type != "otc":
        raise ValueError("invalid_session_type")
    result = {
        "instrument_id": f"{asset_class}:{venue}:{symbol}",
        "asset_class": asset_class,
        "venue": venue,
        "symbol": symbol,
        "quote_currency": quote_currency,
        "timezone": timezone_name,
        "session_type": session_type,
        "tick_size": _positive_decimal(record.get("tick_size"), "tick_size"),
        "lot_size": _positive_decimal(record.get("lot_size"), "lot_size"),
    }
    if record.get("base_currency") is not None:
        result["base_currency"] = _text(record["base_currency"], "base_currency", upper=True, max_length=12)
    if record.get("contract_multiplier") is not None:
        result["contract_multiplier"] = _positive_decimal(record["contract_multiplier"], "contract_multiplier")
    return result


def audit_instruments(records: list[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(records, list) or len(records) > 10000:
        raise ValueError("invalid_instrument_batch")
    normalized = []
    rejected = []
    seen: set[str] = set()
    for index, record in enumerate(records):
        try:
            item = normalize_instrument(record)
            if item["instrument_id"] in seen:
                raise ValueError("duplicate_instrument")
            seen.add(item["instrument_id"])
            normalized.append(item)
        except (ValueError, TypeError) as exc:
            rejected.append({"index": index, "reason": str(exc)})
    return {
        "accepted": len(normalized),
        "rejected": len(rejected),
        "instruments": normalized,
        "errors": rejected,
        "catalog_persisted": False,
        "exchange_connectivity_verified": False,
        "live_execution": False,
    }
