"""Opt-in local receipt -> research -> risk -> canonical paper execution.

A delivery receipt is a prerequisite for this path, never an order approval.
There is no HTTP endpoint, broker transport or automatic background execution.
The authority's SQLite files and the invoking research process are trusted.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from . import paper_execution as execution
from .control_plane.database import canonical, digest, identifier
from .data_store import ResearchStore
from .decimal_safety import bounded_decimal
from .feed_admission import admit_feed
from .p2p_ingestion import (
    DATASET,
    IngestionRejection,
    ingest_p2p_observation,
    validate_p2p_observation,
)
from .paper_observation_bridge import _reference_price
from .paper_pipeline import _account_result, _decimal, _decision
from .paper_workflow import evaluate_paper_workflow
from .receipt_gated_retry import AdmissionDecision, ReceiptRetryScheduler
from .risk import RiskLimits
from .verified_receipt_consumer import VerifiedReceiptConsumer

ENABLE_ENV = "OSIRIS_RECEIPT_PAPER_ENABLED"


def _amount(value, *, positive):
    bounded_decimal(value)
    return _decimal(value, positive=positive)


def _ingest_once(store, observation, normalized, allowed_providers, feed_policy, current):
    admission = admit_feed(
        provider=normalized["provider"],
        instrument=normalized["instrument"],
        policy=feed_policy,
        now=current,
    )
    source = "p2p:" + normalized["provider"]
    key = normalized["instrument"] + ":" + normalized["payload_sha256"][:32]
    stored = store.get(DATASET, source, key)
    if stored is None:
        try:
            ingest_p2p_observation(
                store,
                observation,
                allowed_providers=allowed_providers,
                feed_policy=feed_policy,
                require_feed_policy=True,
                now=current,
            )
        except IngestionRejection:
            # Another process may have committed the same immutable record.
            if store.get(DATASET, source, key) is None:
                raise
        stored = store.get(DATASET, source, key)
    expected = {
        "schema": normalized["schema"],
        "provider": normalized["provider"],
        "payload": normalized["payload"],
        "payload_sha256": normalized["payload_sha256"],
    }
    if (
        stored is None
        or any(stored["payload"].get(k) != v for k, v in expected.items())
        or stored["event_time"] != normalized["observed_at"].isoformat()
        or datetime.fromisoformat(stored["available_at"]) > current
    ):
        raise ValueError("receipt_stored_observation_conflict")
    original_admission = stored["payload"].get("feed_admission", {})
    if any(
        original_admission.get(k) != admission[k]
        for k in ("authorized_source", "entitlement_reference", "redistribution_rights")
    ):
        raise ValueError("receipt_feed_policy_changed")
    return {
        "dataset": DATASET,
        "record_key": key,
        "source": source,
        "provider": normalized["provider"],
        "event_time": stored["event_time"],
        "available_at": stored["available_at"],
        "payload_sha256": normalized["payload_sha256"],
    }


def execute_receipted_observation(
    scheduler: ReceiptRetryScheduler,
    consumer: VerifiedReceiptConsumer,
    store: ResearchStore,
    *,
    observation_key: str,
    recipient: str,
    allowed_providers: set[str],
    policy: AdmissionDecision,
    feed_policy: dict,
    strategy_id: str,
    signals: list[float],
    asset_returns: list[float],
    quantity: str,
    fee: str = "0",
    source_mode: str = "operator-declared",
    risk_service_available: bool = False,
    now: datetime | None = None,
) -> dict:
    """Execute an explicitly invoked synthetic/paper proposal once per observation.

    Current admission, feed rights declarations, freshness and canonical risk
    are checked independently of receipt verification. The stable order key
    prevents another recipient/message or a crash from creating a second fill.
    """
    if os.getenv(ENABLE_ENV) != "1":
        raise ValueError("receipt_paper_execution_disabled")
    if os.getenv("OSIRIS_ENABLE_LIVE_EXECUTION"):
        raise ValueError("live_execution_disabled")
    if Path(scheduler.ledger.path).resolve() != Path(consumer.ledger.path).resolve():
        raise ValueError("receipt_ledger_mismatch")
    if not isinstance(policy, AdmissionDecision) or policy.outcome(recipient) != "allowed":
        raise ValueError("receipt_recipient_not_authorized")
    if type(risk_service_available) is not bool:
        raise ValueError("invalid_risk_service_state")
    if source_mode not in {"synthetic", "operator-declared"}:
        raise ValueError("invalid_receipt_source_mode")
    strategy_id = identifier(strategy_id)
    if any(
        not isinstance(values, list) or not 2 <= len(values) <= 10000
        for values in (signals, asset_returns)
    ):
        raise ValueError("invalid_receipt_research_size")
    # Detach mutable research inputs before any storage or execution call.
    research = json.loads(canonical({"signals": signals, "asset_returns": asset_returns}))
    qty, cost = _amount(quantity, positive=True), _amount(fee, positive=False)
    current = now if now is not None else datetime.now(UTC)
    if not isinstance(current, datetime) or current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("receipt_paper_clock_requires_timezone")
    current = current.astimezone(UTC)
    attempt = scheduler.snapshot(observation_key, recipient)
    if not attempt or attempt["state"] != "acknowledged":
        raise ValueError("verified_receipt_required")
    event = consumer.verified_event(attempt["message_id"], recipient, attempt["envelope_sha256"])
    if event is None:
        raise ValueError("verified_receipt_provenance_required")
    observation = json.loads(attempt["observation_json"])
    normalized = validate_p2p_observation(
        observation,
        allowed_providers=allowed_providers,
        max_future_skew_seconds=0,
        now=current,
    )
    expected_key = ":".join(
        (normalized["provider"], normalized["instrument"], normalized["payload_sha256"])
    )
    if expected_key != observation_key:
        raise ValueError("receipt_observation_binding_mismatch")
    payload = json.loads(normalized["payload"])
    if not isinstance(payload, dict):
        raise TypeError("receipt_market_payload_required")
    reference = _reference_price(payload)
    # Match the workflow's exact float contract without silently changing price.
    values = [payload[key] for key in ("last", "price", "rate_per_usd") if key in payload]
    price = _amount(values[0], positive=True)
    if any(_amount(value, positive=True) != price for value in values[1:]):
        raise ValueError("receipt_market_price_ambiguous")
    side = (
        "BUY" if research["signals"][-1] > 0 else "SELL" if research["signals"][-1] < 0 else "HOLD"
    )
    context = execution.execution_context(
        symbol=normalized["instrument"],
        price=str(price),
        side="SELL" if side == "SELL" else "BUY",
    )
    validate_p2p_observation(
        observation,
        allowed_providers=allowed_providers,
        max_age_seconds=float(context["max_market_age_seconds"]),
        max_future_skew_seconds=0,
        now=current,
    )
    if type(event["accepted_at"]) is not int or not 0 <= current.timestamp() - event[
        "accepted_at"
    ] <= float(context["max_market_age_seconds"]):
        raise ValueError("receipt_not_current")
    # Real ingestion rechecks the declared feed policy and stores immutable
    # point-in-time provenance; it does not infer an independently verified licence.
    ingested = _ingest_once(store, observation, normalized, allowed_providers, feed_policy, current)
    workflow = evaluate_paper_workflow(
        symbol=normalized["instrument"],
        **research,
        quantity=float(qty),
        price=reference,
        current_position_notional=float(context["current_position_notional"]),
        current_gross_exposure=float(context["current_gross_exposure"]),
        daily_pnl=float(context["daily_pnl"]),
        limits=RiskLimits(**context["limits"]),
        kill_switch_active=context["kill_switch_active"],
        market_data_fresh=True,
        risk_service_available=risk_service_available,
    )
    trace_id = "receipt-paper-" + digest(
        {"strategy": strategy_id, "observation_key": observation_key}
    )
    fingerprint = digest(
        {
            "receipt": event,
            "observation": observation,
            "research": research,
            "strategy": strategy_id,
        "quantity": execution._canonical(qty),
        "price": execution._canonical(price),
        "fee": execution._canonical(cost),
            "source_mode": source_mode,
        }
    )
    prior = _decision(trace_id, fingerprint)
    if prior is not None:
        actual = execution.get_paper_order(trace_id)
        if actual is not None and actual.get("origin_reference") != fingerprint:
            raise ValueError("receipt_paper_origin_unbound")
        return {
            **_account_result({**prior, "replayed": True}),
            "current_risk": workflow["risk"],
            "new_execution_authorized": False,
        }
    existing = execution.get_paper_order(trace_id)
    if existing is not None:
        if existing.get("origin_reference") != fingerprint:
            raise ValueError("receipt_paper_origin_unbound")
        expected = {
            "symbol": normalized["instrument"].upper(),
            "side": side,
            "quantity": str(qty),
            "price": str(price),
            "fee": str(cost),
        }
        for field, value in expected.items():
            actual = existing[field]
            same = (
                actual == value
                if field in {"symbol", "side"}
                else _decimal(actual, positive=False) == _decimal(value, positive=False)
            )
            if not same:
                raise ValueError("receipt_paper_order_conflict")
        order = existing
    else:
        order = execution.submit_approved_workflow(
            workflow,
            client_order_id=trace_id,
            quantity=str(qty),
            price=str(price),
            fee=str(cost),
            observed_at=normalized["observed_at"].isoformat(),
            now=current.isoformat(),
            risk_service_available=risk_service_available,
            origin_reference=fingerprint,
        )
    execution.mark_to_market(
        {normalized["instrument"]: str(price)},
        observed_at=normalized["observed_at"].isoformat(),
        now=current.isoformat(),
    )
    result = _account_result(
        {
            "schema": "osiris.receipt-paper-execution.v1",
            "pipeline_run_id": trace_id,
            "trace_id": trace_id,
            "strategy_id": strategy_id,
            "receipt": event,
            "observation_provenance": ingested,
            "workflow": workflow,
            "order": order,
            "replayed": existing is not None,
            "decision_time": current.isoformat(),
            "source_mode": source_mode,
            "receipt_is_order_approval": False,
            "new_execution_authorized": False,
            "independent_model_approval": False,
            "independent_vendor_rights_verified": False,
            "live_execution": False,
            "broker_order_submitted": False,
            "mode": "paper_only",
        }
    )
    if (
        execution.get_paper_order(trace_id) is not None
        and result["order"].get("origin_reference") != fingerprint
    ):
        raise ValueError("receipt_paper_origin_unbound")
    _decision(trace_id, fingerprint, result)
    return {**result, "current_risk": workflow["risk"]}
