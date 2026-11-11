"""Ghost trading mode router: paper authorization or fail-closed live preview.

No live broker adapter, credentials, orders or execution authorization.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .paper_execution_gate import PaperOrderContext, authorize_paper_order
from .risk import RiskLimits

Mode = Literal["paper", "live"]


@dataclass(frozen=True)
class GhostTradeRequest:
    mode: str
    context: PaperOrderContext
    source_consistent: bool
    peer_authenticated: bool
    provenance_verified: bool


def route_ghost_trade(
    request: GhostTradeRequest, limits: RiskLimits = RiskLimits(),
) -> dict:
    """Provide paper risk decision or live-blocked readiness diagnostics.

    'paper_authorized' means the simulation gate passed, not an order/fill.
    'live' is unconditionally blocked, even when all input claims are true.
    """
    if request.mode not in ("paper", "live"):
        return {"mode": request.mode, "status": "rejected", "reason": "unknown_mode",
                "broker_called": False, "live_execution_authorized": False}
    checks = {
        "source_consistent": request.source_consistent is True,
        "peer_authenticated": request.peer_authenticated is True,
        "provenance_verified": request.provenance_verified is True,
    }
    allowed, reason = authorize_paper_order(request.context, limits)
    checks["paper_risk_gate"] = allowed
    if request.mode == "live":
        return {"mode": "live", "status": "blocked",
                "reason": "live_release_gate_not_approved",
                "checks": checks, "paper_risk_reason": reason,
                "broker_called": False, "live_execution_authorized": False,
                "next_step": "independent_review_licensing_and_explicit_operator_signoff"}
    for key in ("source_consistent", "peer_authenticated", "provenance_verified"):
        if not checks[key]:
            return {"mode": "paper", "status": "rejected", "reason": key + "_required",
                    "checks": checks, "broker_called": False,
                    "live_execution_authorized": False}
    return {"mode": "paper", "status": "paper_authorized" if allowed else "rejected",
            "reason": "paper_only" if allowed else reason,
            "checks": checks, "broker_called": False,
            "live_execution_authorized": False}
