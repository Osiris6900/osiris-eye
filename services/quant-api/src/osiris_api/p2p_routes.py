"""Authenticated operator endpoints for the first Hybrid P2P identity milestone."""
from typing import Any
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from .p2p_identity import P2PIdentityRegistry

router = APIRouter(prefix="/p2p", tags=["hybrid-p2p"])
identity_registry = P2PIdentityRegistry()

class EnrollmentRequest(BaseModel):
    public_key: str = Field(min_length=40, max_length=128)
    signature: str = Field(min_length=40, max_length=256)
    challenge_id: str = Field(min_length=20, max_length=128)

@router.get("/status")
def p2p_status() -> dict[str, Any]:
    return identity_registry.status()

@router.get("/nodes")
def p2p_nodes() -> dict[str, Any]:
    return {"nodes": identity_registry.list_nodes(), "live_execution": False}

@router.post("/challenge")
def p2p_challenge() -> dict[str, Any]:
    return identity_registry.create_challenge()

@router.post("/enroll")
def p2p_enroll(request: EnrollmentRequest) -> dict[str, Any]:
    try:
        return identity_registry.enroll(request.public_key, request.signature, request.challenge_id)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
