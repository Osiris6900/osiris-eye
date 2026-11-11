"""Authorized P2P market-data ingestion into research and paper pipeline (issue #87).

Maps the signed P2P observation contract (``osiris.observation.v1``) onto the
research observation store with fail-closed gates:

- provenance: provider must be on an explicit operator allowlist
- freshness: ``observed_at`` must be within a bounded age and not future-dated
- schema + payload-hash verification before any storage
- kill switch: ingestion is refused unless ``OSIRIS_P2P_INGEST_ENABLED=1``

Optionally derives a risk-gated *paper* order intent. Live order routing stays
disabled; nothing in this module contacts a broker or exchange.
"""
from __future__ import annotations

import hashlib
import math
import os
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Iterable, Mapping

from .data_store import ResearchStore
from .feed_admission import FeedAdmissionRejection, admit_feed
from .risk import RiskLimits, check_order

if TYPE_CHECKING:
    from .observation_outbox import ObservationOutbox

OBSERVATION_SCHEMA = "osiris.observation.v1"
DATASET = "p2p_observations"
MAX_INSTRUMENT_LENGTH = 64
MAX_PROVIDER_LENGTH = 200
MAX_PAYLOAD_BYTES = 16384
DEFAULT_MAX_AGE_SECONDS = 300.0
DEFAULT_MAX_FUTURE_SKEW_SECONDS = 60.0
ENABLE_ENV = "OSIRIS_P2P_INGEST_ENABLED"
PAPER_INTENT_ENV = "OSIRIS_PAPER_INTENT_ENABLED"

_HEX_64 = re.compile(r"[0-9a-f]{64}")
# Keep validation independent of an injected wall-clock implementation.
_DATETIME_TYPE = datetime


class IngestionRejection(ValueError):
    """Raised when an observation fails a fail-closed ingestion gate."""


def _utc_now(now: datetime | None) -> datetime:
    current = datetime.now(timezone.utc) if now is None else now
    if (not isinstance(current, _DATETIME_TYPE)
            or current.tzinfo is None or current.utcoffset() is None):
        raise IngestionRejection("ingestion_time_missing_timezone")
    return current.astimezone(timezone.utc)


def _validate_freshness_bounds(max_age_seconds: float, max_future_skew_seconds: float) -> None:
    for value in (max_age_seconds, max_future_skew_seconds):
        try:
            valid = (not isinstance(value, bool) and isinstance(value, (int, float))
                     and math.isfinite(value))
        except OverflowError:
            valid = False
        if not valid:
            raise IngestionRejection("invalid_freshness_bounds")
    if max_age_seconds <= 0 or max_future_skew_seconds < 0:
        raise IngestionRejection("invalid_freshness_bounds")


def _parse_timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise IngestionRejection(f"invalid_{field}")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise IngestionRejection(f"invalid_{field}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise IngestionRejection(f"{field}_missing_timezone")
    return parsed.astimezone(timezone.utc)


def validate_observation_contract(observation: Any) -> dict[str, Any]:
    """Validate and normalize one ``osiris.observation.v1`` observation.

    Rejects unknown schemas, malformed fields, oversized payloads and any
    payload whose bytes do not match the declared ``payload_sha256``.
    """
    if not isinstance(observation, dict):
        raise IngestionRejection("observation_must_be_object")
    if observation.get("schema") != OBSERVATION_SCHEMA:
        raise IngestionRejection("unsupported_schema")

    instrument = observation.get("instrument")
    if (not isinstance(instrument, str) or not instrument.strip()
            or len(instrument.strip()) > MAX_INSTRUMENT_LENGTH):
        raise IngestionRejection("invalid_instrument")

    provider = observation.get("provider")
    if (not isinstance(provider, str) or not provider.strip()
            or len(provider.strip()) > MAX_PROVIDER_LENGTH):
        raise IngestionRejection("invalid_provider")

    observed_at = _parse_timestamp(observation.get("observed_at"), "observed_at")

    payload = observation.get("payload")
    if not isinstance(payload, str) or not payload:
        raise IngestionRejection("invalid_payload")
    try:
        payload_bytes = payload.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise IngestionRejection("invalid_payload_encoding") from exc
    if len(payload_bytes) > MAX_PAYLOAD_BYTES:
        raise IngestionRejection("payload_too_large")

    declared = observation.get("payload_sha256")
    if not isinstance(declared, str) or _HEX_64.fullmatch(declared) is None:
        raise IngestionRejection("invalid_payload_sha256")
    actual = hashlib.sha256(payload_bytes).hexdigest()
    if actual != declared:
        raise IngestionRejection("payload_hash_mismatch")

    return {
        "schema": OBSERVATION_SCHEMA,
        "instrument": instrument.strip(),
        "provider": provider.strip(),
        "observed_at": observed_at,
        "payload": payload,
        "payload_sha256": declared,
    }


def validate_p2p_observation(
    observation: Any,
    *,
    allowed_providers: Iterable[str],
    max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS,
    max_future_skew_seconds: float = DEFAULT_MAX_FUTURE_SKEW_SECONDS,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Validate current admission policy without writing to the research store.

    Initial ingestion and durable retries share the same kill switch,
    provenance, freshness and contract checks. A previous successful ingestion
    does not authorize later delivery under a changed policy.
    """
    if os.getenv(ENABLE_ENV) != "1":
        raise IngestionRejection("ingestion_disabled_kill_switch")

    providers = {str(item).strip() for item in allowed_providers if str(item).strip()}
    if not providers:
        raise IngestionRejection("no_authorized_providers")
    _validate_freshness_bounds(max_age_seconds, max_future_skew_seconds)

    current = _utc_now(now)
    normalized = validate_observation_contract(observation)

    if normalized["provider"] not in providers:
        raise IngestionRejection("provider_not_authorized")

    age = (current - normalized["observed_at"]).total_seconds()
    if age > max_age_seconds:
        raise IngestionRejection("observation_stale")
    if age < -max_future_skew_seconds:
        raise IngestionRejection("observation_future_dated")

    return normalized


def ingest_p2p_observation(
    store: ResearchStore,
    observation: Any,
    *,
    allowed_providers: Iterable[str],
    max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS,
    max_future_skew_seconds: float = DEFAULT_MAX_FUTURE_SKEW_SECONDS,
    now: datetime | None = None,
    outbox: ObservationOutbox | None = None,
    feed_policy: Mapping[str, Any] | None = None,
    require_feed_policy: bool = False,
) -> dict[str, Any]:
    """Validate one P2P observation and append it to the research store.

    Every gate fails closed: the kill switch, provenance allowlist, freshness
    bound and contract validation must all pass before anything is stored.
    With an outbox, both rows commit together in the same file-backed database.
    """
    current = _utc_now(now)
    normalized = validate_p2p_observation(
        observation, allowed_providers=allowed_providers,
        max_age_seconds=max_age_seconds,
        max_future_skew_seconds=max_future_skew_seconds, now=current,
    )
    if require_feed_policy and feed_policy is None:
        raise IngestionRejection("feed_policy_required")
    admission = None
    if feed_policy is not None:
        try:
            admission = admit_feed(
                provider=normalized["provider"], instrument=normalized["instrument"],
                policy=feed_policy, now=current,
            )
        except FeedAdmissionRejection as exc:
            raise IngestionRejection("feed_admission_rejected:" + str(exc)) from exc
    if outbox is not None:
        try:
            outbox.require_shared_database(store.database_path)
        except ValueError as exc:
            raise IngestionRejection(str(exc)) from exc

    # available_at may not precede event_time in the store; clamp minor clock skew.
    available_at = max(current, normalized["observed_at"])
    fields = {
        "dataset": DATASET,
        "record_key": f"{normalized['instrument']}:{normalized['payload_sha256'][:32]}",
        "source": f"p2p:{normalized['provider']}",
        "event_time": normalized["observed_at"].isoformat(),
        "available_at": available_at.isoformat(),
        "payload": {
            "schema": normalized["schema"],
            "provider": normalized["provider"],
            "payload": normalized["payload"],
            "payload_sha256": normalized["payload_sha256"],
        },
    }
    if admission is not None:
        fields["payload"]["feed_admission"] = admission
    try:
        if outbox is None:
            record = store.put(**fields)
        else:
            with store.transaction() as connection:
                record = store._put_in_transaction(connection, **fields)
                if not outbox._enqueue_in_transaction(connection, observation):
                    # A legacy key collision must not commit an unqueued observation.
                    raise IngestionRejection("outbox_observation_conflict")
    except IngestionRejection:
        raise
    except ValueError as exc:
        raise IngestionRejection(f"store_rejected:{exc}") from exc

    return {
        "ingested": True,
        "dataset": record["dataset"],
        "record_key": record["record_key"],
        "source": record["source"],
        "event_time": record["event_time"],
        "available_at": record["available_at"],
        "feed_admission": admission,
        "live_execution": False,
    }


def paper_intent_from_observation(
    ingestion_record: dict[str, Any],
    *,
    quantity: float,
    reference_price: float,
    current_position_notional: float = 0.0,
    daily_pnl: float = 0.0,
    limits: RiskLimits = RiskLimits(),
) -> dict[str, Any]:
    """Derive a risk-gated paper order intent from an ingested observation.

    Paper intents require an explicit second opt-in
    (``OSIRIS_PAPER_INTENT_ENABLED=1``) and must pass the independent risk
    gate. No broker or exchange call is ever made.
    """
    if not isinstance(ingestion_record, dict) or not ingestion_record.get("ingested"):
        raise ValueError("ingestion_record_required")
    symbol = str(ingestion_record.get("record_key", "")).split(":", 1)[0]

    base = {
        "workflow": "p2p_observation_to_paper_intent",
        "symbol": symbol,
        "execution": {
            "mode": "paper_only",
            "broker_order_submitted": False,
            "live_execution": False,
        },
    }
    if os.getenv(PAPER_INTENT_ENV) != "1":
        return {
            **base,
            "paper_order_intent": {
                "symbol": symbol,
                "status": "BLOCKED_BY_KILL_SWITCH",
                "reason": "paper_intent_disabled",
            },
        }

    approved, reason = check_order(
        quantity=quantity,
        price=reference_price,
        current_position_notional=current_position_notional,
        daily_pnl=daily_pnl,
        limits=limits,
    )
    return {
        **base,
        "risk": {"approved_for_paper": approved, "reason": reason},
        "paper_order_intent": {
            "symbol": symbol,
            "quantity": float(quantity),
            "reference_price": float(reference_price),
            "status": "PAPER_INTENT_APPROVED" if approved else "BLOCKED_BY_RISK",
        },
    }
