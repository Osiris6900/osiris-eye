"""Authenticated operator endpoints for the first Hybrid P2P identity milestone."""
from typing import Any
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from .p2p_identity import CHALLENGE_TTL_SECONDS, P2PIdentityRegistry

router = APIRouter(prefix="/p2p", tags=["hybrid-p2p"])
identity_registry = P2PIdentityRegistry()

_CAPACITY_ERRORS = frozenset({"challenge_capacity_exceeded", "node_registry_capacity_exceeded"})
_ENROLLMENT_ERRORS = frozenset({
    "invalid_base64_value", "invalid_ed25519_public_key", "invalid_challenge_id",
    "challenge_missing_or_already_used", "challenge_expired", "invalid_node_signature",
})


def _registry_error(exc: ValueError | TypeError, *, enrollment: bool = False) -> HTTPException:
    code = str(exc)
    if code in _CAPACITY_ERRORS:
        return HTTPException(status_code=429, detail=code,
                             headers={"Retry-After": str(CHALLENGE_TTL_SECONDS)})
    if enrollment and code in _ENROLLMENT_ERRORS:
        return HTTPException(status_code=422, detail=code)
    if enrollment and isinstance(exc, TypeError):
        return HTTPException(status_code=422, detail="invalid_enrollment_request")
    # Only documented input errors are client-visible. Unexpected registry
    # failures must not expose filesystem/database details in the response.
    return HTTPException(status_code=500, detail="identity_registry_error")

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
    try:
        return identity_registry.create_challenge()
    except (ValueError, TypeError) as exc:
        raise _registry_error(exc) from exc

@router.post("/enroll")
def p2p_enroll(request: EnrollmentRequest) -> dict[str, Any]:
    try:
        return identity_registry.enroll(request.public_key, request.signature, request.challenge_id)
    except (ValueError, TypeError) as exc:
        raise _registry_error(exc, enrollment=True) from exc
