"""Authenticated observation -> research signal -> risk decision, paper-only.

This endpoint makes no broker calls and does not persist or execute orders.
"""
import hashlib
import hmac
import math
import os
import time

from fastapi import APIRouter
from pydantic import BaseModel, Field

from .risk import check_order

router = APIRouter(prefix="/integration", tags=["paper-integration"])


class PaperSignalRequest(BaseModel):
    peer_id: str = Field(min_length=1, max_length=128)
    instrument: str = Field(min_length=1, max_length=64)
    observed_at: int = Field(ge=0)
    price: float = Field(gt=0, allow_inf_nan=False)
    signal: float = Field(ge=-1, le=1, allow_inf_nan=False)
    quantity: float = Field(gt=0, allow_inf_nan=False)
    current_position_notional: float = Field(default=0, ge=0, allow_inf_nan=False)
    daily_pnl: float = Field(default=0, allow_inf_nan=False)
    signature: str = Field(pattern=r"^[0-9a-f]{64}$")


def observation_message(request: PaperSignalRequest) -> bytes:
    """Canonical wire format; fixed decimal formatting is part of the contract."""
    return (
        f"{request.peer_id}|{request.instrument}|{request.observed_at}|"
        f"{request.price:.8f}|{request.signal:.8f}"
    ).encode("utf-8")


@router.post("/paper-signal")
def evaluate_paper_signal(request: PaperSignalRequest):
    """Fail closed on missing trust config, bad signature, stale feed and risk."""
    secret = os.getenv("OSIRIS_OBSERVATION_HMAC_KEY", "")
    allowed = {peer.strip() for peer in os.getenv("OSIRIS_TRUSTED_PEERS", "").split(",") if peer.strip()}
    if not secret or not allowed:
        return {"approved": False, "reason": "trust_not_configured", "mode": "paper_only"}
    if request.peer_id not in allowed:
        return {"approved": False, "reason": "untrusted_peer", "mode": "paper_only"}
    expected = hmac.new(secret.encode(), observation_message(request), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, request.signature):
        return {"approved": False, "reason": "invalid_observation_signature", "mode": "paper_only"}
    now = int(time.time())
    if request.observed_at > now + 5 or now - request.observed_at > 30:
        return {"approved": False, "reason": "stale_or_future_observation", "mode": "paper_only"}
    if not math.isfinite(request.price * request.quantity):
        return {"approved": False, "reason": "invalid_notional", "mode": "paper_only"}
    if request.signal <= 0:
        return {"approved": False, "reason": "research_signal_abstains", "mode": "paper_only"}
    approved, reason = check_order(
        quantity=request.quantity, price=request.price,
        current_position_notional=request.current_position_notional,
        daily_pnl=request.daily_pnl,
    )
    return {"approved": approved, "reason": reason, "mode": "paper_only",
            "instrument": request.instrument, "notional": request.quantity * request.price}
