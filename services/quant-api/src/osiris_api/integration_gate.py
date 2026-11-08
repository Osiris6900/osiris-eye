"""Fail-closed, paper-only integration gate for authenticated research observations.

This endpoint does not execute orders or validate cryptographic signatures itself.
Upstream signature verification must be performed by the trusted ingress.
"""
from datetime import datetime, timezone
from math import isfinite

from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter(prefix="/platform/integration", tags=["paper-integration"])


class VerifiedObservation(BaseModel):
    source: str = Field(min_length=1)
    signature_verified: bool = False
    authorized_source: bool = False
    license_authorized: bool = False
    available_at: datetime
    event_time: datetime
    price: float = Field(gt=0)


class ResearchIntent(BaseModel):
    model_id: str = Field(min_length=1)
    approved_for_paper: bool = False
    quantity: float = Field(gt=0)
    max_notional: float = Field(gt=0)


class PaperGateRequest(BaseModel):
    observation: VerifiedObservation
    intent: ResearchIntent
    as_of: datetime
    max_age_seconds: float = Field(default=30, ge=0, le=86400)
    kill_switch: bool = True
    risk_approved: bool = False


@router.post("/paper-gate")
def paper_gate(request: PaperGateRequest):
    obs, intent = request.observation, request.intent
    reasons = []
    if not (obs.signature_verified and obs.authorized_source):
        reasons.append("untrusted_observation")
    if not obs.license_authorized:
        reasons.append("data_not_licensed")
    if any(t.tzinfo is None or t.utcoffset() is None
           for t in (obs.available_at, obs.event_time, request.as_of)):
        reasons.append("timezone_required")
    else:
        age = (request.as_of - obs.available_at).total_seconds()
        if obs.event_time > obs.available_at or age < 0:
            reasons.append("point_in_time_violation")
        elif age > request.max_age_seconds:
            reasons.append("stale_observation")
    if not intent.approved_for_paper:
        reasons.append("model_not_approved")
    if request.kill_switch:
        reasons.append("kill_switch_active")
    if not request.risk_approved:
        reasons.append("risk_not_approved")
    notional = obs.price * intent.quantity
    if not isfinite(notional) or notional > intent.max_notional:
        reasons.append("notional_limit")
    allowed = not reasons
    return {
        "status": "paper_eligible" if allowed else "blocked",
        "paper_intent_allowed": allowed,
        "live_execution_enabled": False,
        "order_submitted": False,
        "reasons": reasons,
        "model_id": intent.model_id,
    }
