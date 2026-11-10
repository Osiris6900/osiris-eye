"""Durable, fail-closed observation-to-paper-workflow integration.

Only observations that previously passed the P2P ingestion gates and were stored
in ``p2p_observations`` can be evaluated. Current provider authorization and
freshness are checked again. The result is an idempotent paper intent, never a
broker order, fill, position update, or live-trading instruction.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

from .data_store import ResearchStore
from .p2p_ingestion import DATASET as OBSERVATION_DATASET
from .p2p_ingestion import PAPER_INTENT_ENV
from .paper_workflow import evaluate_paper_workflow
from .risk import RiskLimits

PAPER_DATASET = "paper_order_intents"
PAPER_SCHEMA = "osiris.paper_intent.v1"
PAPER_SOURCE = "paper:observation-bridge"
DEFAULT_MAX_AGE_SECONDS = 300.0
DEFAULT_MAX_FUTURE_SKEW_SECONDS = 60.0


class PaperBridgeRefused(ValueError):
    """Raised when a stored observation cannot authorize paper evaluation."""


def _utc_now(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise PaperBridgeRefused("paper_evaluation_time_missing_timezone")
    return current.astimezone(timezone.utc)


def _event_time(value: Any) -> datetime:
    if not isinstance(value, str):
        raise PaperBridgeRefused("stored_observation_event_time_invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PaperBridgeRefused("stored_observation_event_time_invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PaperBridgeRefused("stored_observation_event_time_invalid")
    return parsed.astimezone(timezone.utc)


def _load_market_payload(stored: dict[str, Any]) -> tuple[dict[str, Any], str]:
    wrapper = stored.get("payload")
    if not isinstance(wrapper, dict) or wrapper.get("schema") != "osiris.observation.v1":
        raise PaperBridgeRefused("stored_observation_contract_invalid")
    encoded = wrapper.get("payload")
    declared = wrapper.get("payload_sha256")
    if not isinstance(encoded, str) or not isinstance(declared, str):
        raise PaperBridgeRefused("stored_observation_contract_invalid")
    if hashlib.sha256(encoded.encode("utf-8")).hexdigest() != declared:
        raise PaperBridgeRefused("stored_observation_payload_hash_mismatch")
    try:
        payload = json.loads(encoded)
    except json.JSONDecodeError as exc:
        raise PaperBridgeRefused("stored_observation_payload_not_json") from exc
    if not isinstance(payload, dict):
        raise PaperBridgeRefused("stored_observation_payload_not_object")
    provider = wrapper.get("provider")
    if not isinstance(provider, str) or stored.get("source") != f"p2p:{provider}":
        raise PaperBridgeRefused("stored_observation_provenance_mismatch")
    expected_key_suffix = ":" + declared[:32]
    if not str(stored.get("record_key", "")).endswith(expected_key_suffix):
        raise PaperBridgeRefused("stored_observation_record_key_mismatch")
    return payload, provider


def _reference_price(payload: dict[str, Any]) -> float:
    values = []
    for field in ("last", "price", "rate_per_usd"):
        if field not in payload:
            continue
        value = payload[field]
        if isinstance(value, bool):
            raise PaperBridgeRefused("stored_observation_price_invalid")
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise PaperBridgeRefused("stored_observation_price_invalid") from exc
        if not math.isfinite(number) or number <= 0:
            raise PaperBridgeRefused("stored_observation_price_invalid")
        values.append(number)
    if not values:
        raise PaperBridgeRefused("stored_observation_price_missing")
    if any(value != values[0] for value in values[1:]):
        raise PaperBridgeRefused("stored_observation_price_ambiguous")
    return values[0]


def _intent_digest(
    stored: dict[str, Any], *, signals: Sequence[float], asset_returns: Sequence[float],
    quantity: float, reference_price: float, current_position_notional: float,
    daily_pnl: float, cost_bps_per_unit_turnover: float, limits: RiskLimits,
) -> str:
    material = {
        "observation": {
            "dataset": stored["dataset"], "source": stored["source"],
            "record_key": stored["record_key"], "event_time": stored["event_time"],
        },
        "signals": list(signals), "asset_returns": list(asset_returns),
        "quantity": quantity, "reference_price": reference_price,
        "current_position_notional": current_position_notional, "daily_pnl": daily_pnl,
        "cost_bps_per_unit_turnover": cost_bps_per_unit_turnover, "limits": asdict(limits),
    }
    try:
        encoded = json.dumps(material, allow_nan=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise PaperBridgeRefused("invalid_paper_workflow_input") from exc
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def evaluate_stored_observation(
    store: ResearchStore,
    *,
    record_key: str,
    source: str,
    allowed_providers: Iterable[str],
    signals: Sequence[float],
    asset_returns: Sequence[float],
    quantity: float,
    current_position_notional: float = 0.0,
    daily_pnl: float = 0.0,
    cost_bps_per_unit_turnover: float = 2.0,
    limits: RiskLimits = RiskLimits(),
    max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS,
    max_future_skew_seconds: float = DEFAULT_MAX_FUTURE_SKEW_SECONDS,
    require_feed_admission: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Evaluate one stored observation and durably record an idempotent paper intent."""
    if os.getenv(PAPER_INTENT_ENV) != "1":
        raise PaperBridgeRefused("paper_intent_disabled")
    if max_age_seconds <= 0 or max_future_skew_seconds < 0:
        raise PaperBridgeRefused("invalid_freshness_bounds")
    providers = {str(item).strip() for item in allowed_providers if str(item).strip()}
    if not providers:
        raise PaperBridgeRefused("no_authorized_providers")

    stored = store.get(OBSERVATION_DATASET, source, record_key)
    if stored is None:
        raise PaperBridgeRefused("stored_observation_not_found")
    payload, provider = _load_market_payload(stored)
    symbol = str(stored["record_key"]).rsplit(":", 1)[0]
    admission = stored["payload"].get("feed_admission")
    if require_feed_admission and (
        not isinstance(admission, dict) or admission.get("status") != "admitted"
        or admission.get("provider") != provider or admission.get("instrument") != symbol
    ):
        raise PaperBridgeRefused("feed_admission_required")
    if provider not in providers:
        raise PaperBridgeRefused("provider_not_authorized")

    current = _utc_now(now)
    observed_at = _event_time(stored.get("event_time"))
    age = (current - observed_at).total_seconds()
    if age > max_age_seconds:
        raise PaperBridgeRefused("observation_stale")
    if age < -max_future_skew_seconds:
        raise PaperBridgeRefused("observation_future_dated")

    price = _reference_price(payload)
    try:
        result = evaluate_paper_workflow(
            symbol=symbol, signals=signals, asset_returns=asset_returns,
            quantity=quantity, price=price,
            current_position_notional=current_position_notional, daily_pnl=daily_pnl,
            cost_bps_per_unit_turnover=cost_bps_per_unit_turnover, limits=limits,
        )
    except (TypeError, ValueError, OverflowError) as exc:
        raise PaperBridgeRefused("invalid_paper_workflow_input:" + str(exc)) from exc

    if result["paper_order_intent"]["side"] == "HOLD":
        result["paper_order_intent"]["status"] = "PAPER_HOLD"
        result["risk"] = {"approved_for_paper": False, "reason": "hold_signal_no_order"}

    intent_id = _intent_digest(
        stored, signals=signals, asset_returns=asset_returns, quantity=quantity,
        reference_price=price, current_position_notional=current_position_notional,
        daily_pnl=daily_pnl, cost_bps_per_unit_turnover=cost_bps_per_unit_turnover,
        limits=limits,
    )
    provenance = {
        "dataset": stored["dataset"], "record_key": stored["record_key"],
        "source": stored["source"], "provider": provider,
        "event_time": stored["event_time"],
        "payload_sha256": stored["payload"]["payload_sha256"],
    }
    result.update({
        "schema": PAPER_SCHEMA,
        "paper_intent_id": intent_id,
        "observation_provenance": provenance,
        "execution": {
            "mode": "paper_only", "broker_order_submitted": False,
            "live_execution": False, "fill_simulated": False,
        },
    })
    record_key_out = "intent:" + intent_id
    existing = store.get(PAPER_DATASET, PAPER_SOURCE, record_key_out)
    if existing is not None:
        # Never re-authorize a historical approved intent on idempotent replay:
        # current freshness, provider and pretrade gates may now be closed.
        # The original durable record stays immutable as historical evidence.
        return {**result, "persistence": {
            "status": "idempotent_replay", "dataset": PAPER_DATASET,
            "record_key": record_key_out, "duplicate_created": False,
        }}

    persisted_payload = {"schema": PAPER_SCHEMA, "result": result}
    try:
        store.put(
            dataset=PAPER_DATASET, record_key=record_key_out, source=PAPER_SOURCE,
            event_time=stored["event_time"],
            available_at=max(current, observed_at).isoformat(), payload=persisted_payload,
        )
    except ValueError as exc:
        # A concurrent identical request may win the insert race. Return its exact
        # durable result; any other storage failure remains fail-closed.
        existing = store.get(PAPER_DATASET, PAPER_SOURCE, record_key_out)
        if existing is None:
            raise PaperBridgeRefused("paper_intent_persistence_failed") from exc
        # Lost the insert race: re-evaluate the current risk result rather than
        # trusting a previously persisted approval from another request.
        return {**result, "persistence": {
            "status": "idempotent_replay", "dataset": PAPER_DATASET,
            "record_key": record_key_out, "duplicate_created": False,
        }}
    return {**result, "persistence": {
        "status": "stored", "dataset": PAPER_DATASET,
        "record_key": record_key_out, "duplicate_created": False,
    }}
