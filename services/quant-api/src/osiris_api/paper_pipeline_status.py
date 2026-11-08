"""Read-only status of the most recent in-process paper workflow evaluation.

No order execution or durable workflow status is claimed. A process restart
clears this view; deployments with multiple workers need a shared store.
"""
from datetime import datetime, timezone
from threading import Lock

from fastapi import APIRouter

router = APIRouter()
_lock = Lock()
_latest = None


def record_paper_workflow(result: dict) -> None:
    global _latest
    risk = result.get("risk", {})
    approved = risk.get("approved_for_paper") is True
    snapshot = {
        "mode": "offline-paper",
        "feed": "missing",
        "proposal": "approved" if approved else "rejected",
        "risk": "pass" if approved else "blocked",
        "order": "none",
        "instrument": str(result.get("symbol", "")),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": "in-process-paper-workflow",
        "detail": "Paper evaluation only; no broker orders or fills.",
    }
    with _lock:
        _latest = snapshot


@router.get("/paper/pipeline/status")
def get_paper_pipeline_status():
    with _lock:
        if _latest is None:
            return {
                "mode": "offline-paper",
                "feed": "missing",
                "proposal": "none",
                "risk": "blocked",
                "order": "none",
                "instrument": "",
                "timestamp": "",
                "source": "unavailable",
                "detail": "No paper workflow evaluation has run in this process.",
            }
        return dict(_latest)
