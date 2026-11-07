"""Fail-closed operator policy for feed provenance and quality admission.

The policy is an operator attestation, not proof that OSIRIS independently
verified a vendor contract, redistribution license, or reviewer identity.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

DEFAULT_MAX_REVIEW_AGE_SECONDS = 30 * 24 * 60 * 60.0
MAX_REVIEW_FUTURE_SKEW_SECONDS = 60.0


class FeedAdmissionRejection(ValueError):
    """Raised when feed provenance, rights, coverage, or quality is insufficient."""


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 200:
        raise FeedAdmissionRejection("feed_" + field + "_required")
    return value.strip()


def _reviewed_at(value: Any) -> datetime:
    if not isinstance(value, str):
        raise FeedAdmissionRejection("feed_quality_reviewed_at_invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise FeedAdmissionRejection("feed_quality_reviewed_at_invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise FeedAdmissionRejection("feed_quality_reviewed_at_invalid")
    return parsed.astimezone(timezone.utc)


def admit_feed(
    *,
    provider: str,
    instrument: str,
    policy: Mapping[str, Any],
    now: datetime | None = None,
    max_review_age_seconds: float = DEFAULT_MAX_REVIEW_AGE_SECONDS,
) -> dict[str, Any]:
    """Return an auditable admission report or reject without admitting data."""
    if not isinstance(policy, Mapping):
        raise FeedAdmissionRejection("feed_policy_required")
    if isinstance(max_review_age_seconds, bool) or not isinstance(
        max_review_age_seconds, (int, float)
    ) or not 0 < max_review_age_seconds <= 366 * 24 * 60 * 60:
        raise FeedAdmissionRejection("feed_quality_review_age_bound_invalid")
    record = policy.get(provider)
    if not isinstance(record, Mapping):
        raise FeedAdmissionRejection("feed_provider_policy_missing")

    authorized_source = _text(record.get("authorized_source"), "authorized_source")
    entitlement_reference = _text(
        record.get("entitlement_reference"), "entitlement_reference"
    )
    if record.get("redistribution_rights") != "permitted":
        raise FeedAdmissionRejection("feed_redistribution_not_permitted")
    if record.get("availability_timestamps_required") is not True:
        raise FeedAdmissionRejection("feed_availability_timestamps_required")

    instruments = record.get("instruments")
    if (not isinstance(instruments, (list, tuple, set, frozenset))
            or not instruments or instrument not in instruments):
        raise FeedAdmissionRejection("feed_instrument_not_authorized")

    quality = record.get("quality_review")
    if not isinstance(quality, Mapping) or quality.get("status") != "passed":
        raise FeedAdmissionRejection("feed_quality_review_not_passed")
    reviewer = _text(quality.get("reviewer"), "quality_reviewer")
    reviewed_at = _reviewed_at(quality.get("reviewed_at"))
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise FeedAdmissionRejection("feed_admission_time_missing_timezone")
    current = current.astimezone(timezone.utc)
    review_age = (current - reviewed_at).total_seconds()
    if review_age > max_review_age_seconds:
        raise FeedAdmissionRejection("feed_quality_review_stale")
    if review_age < -MAX_REVIEW_FUTURE_SKEW_SECONDS:
        raise FeedAdmissionRejection("feed_quality_review_future_dated")

    return {
        "status": "admitted",
        "provider": provider,
        "instrument": instrument,
        "authorized_source": authorized_source,
        "entitlement_reference": entitlement_reference,
        "redistribution_rights": "permitted",
        "availability_timestamps": "event_time_and_available_at_required",
        "quality_review": {
            "status": "passed", "reviewer": reviewer,
            "reviewed_at": reviewed_at.isoformat(),
        },
        "verification_boundary": "operator_attestation_not_independent_verification",
    }
