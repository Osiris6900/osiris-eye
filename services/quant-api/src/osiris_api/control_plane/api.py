"""Fail-closed RBAC/MFA APIs for the persistent research control plane."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sqlite3

from fastapi import APIRouter, Body, HTTPException, Request, Response
from fastapi.responses import PlainTextResponse

from ..admin_centre import AdminAuth
from .agents import ResearchAgents
from .coordination import Coordinator
from .database import Database
from .experiments import ExperimentRegistry
from .features import FeatureStore
from .identity import IdentityService
from .models import ModelService
from .observability import Observability
from .replication import Replicator
from .secrets import EncryptedSecrets

router = APIRouter(prefix="/control-plane", tags=["platform-control-plane"])
SCOPES = {
    "viewer": {"read"},
    "analyst": {"read", "research-read", "research"},
    "risk_officer": {"read", "research-read", "research", "review"},
    "operator": {"read", "research-read", "research", "review", "operations"},
    "security_admin": {"read", "research-read", "research", "review", "operations", "security"},
}


def database() -> Database:
    path = os.getenv("OSIRIS_CONTROL_DB", "")
    if not path:
        raise HTTPException(503, "control_plane_not_configured")
    try:
        return Database(path)
    except (ValueError, OSError, sqlite3.Error):
        raise HTTPException(503, "control_storage_unavailable") from None


def identities(db: Database) -> IdentityService:
    try:
        key = base64.b64decode(os.getenv("OSIRIS_CONTROL_MASTER_KEY", ""), validate=True)
        return IdentityService(EncryptedSecrets(db, key))
    except ValueError:
        raise HTTPException(503, "external_control_master_key_required") from None


def principal(
    request: Request, db: Database, scope: str = "read", *, bootstrap: bool = False
) -> dict:
    auth = AdminAuth.from_environment()
    if not auth.configured:
        raise HTTPException(503, "admin_identity_not_configured")
    config_digest = hashlib.sha256(os.getenv("OSIRIS_ADMIN_TOKENS", "").encode()).hexdigest()
    supplied = request.headers.get("authorization", "")
    if supplied.startswith("Session "):
        try:
            actor = identities(db).session(supplied[8:], config_digest)
        except ValueError:
            raise HTTPException(401, "invalid_session") from None
    else:
        role = auth.resolve(supplied)
        if role is None:
            raise HTTPException(401, "authentication_required")
        actor = {
            "role": role,
            "actor": hashlib.sha256(("osiris-role:" + role).encode()).hexdigest(),
            "config_digest": config_digest,
        }
        if not bootstrap and scope not in {"read", "research-read"}:
            raise HTTPException(403, "mfa_session_required")
    if scope not in SCOPES.get(actor["role"], set()):
        raise HTTPException(403, "permission_denied")
    return actor


def run(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        raise HTTPException(422, str(exc)) from None
    except OSError:
        raise HTTPException(503, "dependent_service_unavailable") from None


@router.get("/status")
def status(request: Request):
    db = database()
    actor = principal(request, db)
    return {
        "configured": True,
        "role": actor["role"],
        "storage": "single-authority-sqlite",
        "live_execution": False,
        "independent_host_acceptance": False,
        "recent_job_count": len(Coordinator(db).jobs(limit=10)),
    }


@router.post("/backtest/simulate")
def simulate_backtest(request: Request, payload: dict = Body(...)):
    from .agents import execute_tool

    db = database()
    principal(request, db, "research-read")
    return run(execute_tool, "backtest", payload, db)


@router.post("/auth/enroll")
def enroll(request: Request, response: Response):
    db = database()
    actor = principal(request, db, bootstrap=True)
    response.headers["Cache-Control"] = "no-store"
    if request.url.scheme != "https" and (
        os.getenv("OSIRIS_ENV", "").lower() in {"production", "prod"}
        or request.client.host not in {"127.0.0.1", "::1", "testclient"}
    ):
        raise HTTPException(400, "tls_required_for_mfa")
    seed = run(identities(db).enroll, actor["actor"])
    return {"secret": seed, "algorithm": "SHA1", "digits": 6, "period": 30, "expires_seconds": 600}


@router.post("/experiments")
def create_experiment(request: Request, payload: dict = Body(...)):
    db = database()
    actor = principal(request, db, "research")
    if set(payload) != {"id", "payload"}:
        raise HTTPException(422, "invalid_experiment_request")
    return run(ExperimentRegistry(db).run, payload["id"], actor["actor"], payload["payload"])


@router.get("/experiments")
def list_experiments(request: Request, limit: int = 100):
    db = database()
    principal(request, db, "research-read")
    return {"experiments": run(ExperimentRegistry(db).list, limit=limit)}


@router.get("/experiments/{experiment_id}")
def get_experiment(experiment_id: str, request: Request):
    db = database()
    principal(request, db, "research-read")
    item = run(ExperimentRegistry(db).get, experiment_id)
    if item is None:
        raise HTTPException(404, "experiment_not_found")
    return item


@router.post("/experiments/{experiment_id}/reproduce")
def reproduce_experiment(experiment_id: str, request: Request):
    db = database()
    principal(request, db, "research")
    return run(ExperimentRegistry(db).reproduce, experiment_id)


@router.post("/auth/login")
def login(request: Request, response: Response, payload: dict = Body(...)):
    db = database()
    actor = principal(request, db, bootstrap=True)
    response.headers["Cache-Control"] = "no-store"
    if request.url.scheme != "https" and (
        os.getenv("OSIRIS_ENV", "").lower() in {"production", "prod"}
        or request.client.host not in {"127.0.0.1", "::1", "testclient"}
    ):
        raise HTTPException(400, "tls_required_for_sessions")
    token = run(
        identities(db).login,
        actor["actor"],
        actor["role"],
        payload.get("code"),
        actor["config_digest"],
    )
    return {"session_token": token, "authorization_scheme": "Session", "expires_seconds": 900}


@router.post("/auth/logout")
def logout(request: Request):
    db = database()
    actor = principal(request, db)
    header = request.headers.get("authorization", "")
    if not header.startswith("Session "):
        raise HTTPException(401, "session_required")
    run(identities(db).logout, header[8:], actor["config_digest"])
    return {"revoked": True}


@router.post("/agents/requests")
def agent_request(request: Request, payload: dict = Body(...)):
    db = database()
    actor = principal(request, db, "research")
    return run(
        ResearchAgents(db).request,
        payload.get("id"),
        actor["actor"],
        actor["role"],
        payload.get("tool"),
        payload.get("payload"),
    )


@router.post("/agents/{request_id}/approve")
def agent_approve(request_id: str, request: Request):
    db = database()
    actor = principal(request, db, "review")
    return run(ResearchAgents(db).approve, request_id, actor["actor"], actor["role"])


@router.get("/jobs")
def jobs(request: Request):
    db = database()
    principal(request, db, "research-read")
    return {"jobs": Coordinator(db).jobs()}


@router.post("/workers/claim")
def claim(request: Request, payload: dict = Body(...)):
    db = database()
    actor = principal(request, db, "operations")
    return {"job": run(Coordinator(db).claim, actor["actor"], payload.get("kinds"), ttl=60)}


@router.post("/workers/{job_id}/finish")
def finish(job_id: str, request: Request, payload: dict = Body(...)):
    db = database()
    actor = principal(request, db, "operations")
    if type(payload.get("failed", False)) is not bool:
        raise HTTPException(422, "invalid_failed_flag")
    return run(
        Coordinator(db).finish,
        job_id,
        actor["actor"],
        payload.get("claim_token"),
        payload.get("result"),
        failed=payload.get("failed", False),
    )


@router.post("/leases/acquire")
def acquire(request: Request, payload: dict = Body(...)):
    db = database()
    actor = principal(request, db, "operations")
    return run(
        Coordinator(db).acquire, payload.get("name"), actor["actor"], ttl=payload.get("ttl", 30)
    )


@router.post("/leases/write")
def fenced_write(request: Request, payload: dict = Body(...)):
    db = database()
    actor = principal(request, db, "operations")
    return run(
        Coordinator(db).write,
        payload.get("name"),
        actor["actor"],
        payload.get("epoch"),
        payload.get("id"),
        payload.get("value"),
    )


@router.post("/features")
def add_feature(request: Request, payload: dict = Body(...)):
    db = database()
    principal(request, db, "research")
    return run(FeatureStore(db).put, **payload)


@router.post("/features/query")
def query_features(request: Request, payload: dict = Body(...)):
    db = database()
    principal(request, db, "research-read")
    return run(FeatureStore(db).point_in_time, **payload)


@router.post("/models/{model_id}/predict")
def predict(model_id: str, request: Request, payload: dict = Body(...)):
    db = database()
    principal(request, db, "research-read")
    return {
        "prediction": run(ModelService(db).predict, model_id, payload.get("features")),
        "live_execution": False,
    }


def replicator(db):
    try:
        keys = json.loads(os.getenv("OSIRIS_REPLICATION_PUBLIC_KEYS", "{}"))
        return Replicator(
            db, {key: base64.b64decode(value, validate=True) for key, value in keys.items()}
        )
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(503, "replication_keys_not_configured") from None


@router.post("/replication/events")
def replicate(request: Request, payload: dict = Body(...)):
    db = database()
    principal(request, db, "operations")
    return run(replicator(db).apply, payload.get("events"))


@router.get("/replication/events/{origin}")
def export_events(origin: str, request: Request, after: int = 0):
    db = database()
    principal(request, db, "operations")
    return {"events": run(replicator(db).export, origin, after=after)}


@router.get("/metrics", response_class=PlainTextResponse)
def metrics(request: Request):
    db = database()
    principal(request, db)
    return Observability(db).prometheus()


@router.get("/reports")
def reports(request: Request):
    db = database()
    actor = principal(request, db, "research-read")
    return {
        "jobs": Coordinator(db).jobs(),
        "experiments": db.list("agent-requests", limit=20),
        "models": db.list("models", limit=20),
        "shadow_runs": db.list("shadow-runs", limit=20),
        "role": actor["role"],
        "live_execution": False,
        "independent_review": "pending",
    }


@router.post("/depth/{instrument}/capture")
def capture_depth(instrument: str, request: Request):
    from .depth import DepthService

    db = database()
    principal(request, db, "research")
    return run(
        lambda: DepthService(
            db, entitlement_reference=os.getenv("OSIRIS_L2_ENTITLEMENT_REFERENCE", "")
        ).capture(instrument)
    )


@router.post("/depth/{instrument}/impact")
def depth_impact(instrument: str, request: Request, payload: dict = Body(...)):
    from .depth import DepthService

    db = database()
    principal(request, db, "research-read")
    return run(
        lambda: DepthService(
            db, entitlement_reference=os.getenv("OSIRIS_L2_ENTITLEMENT_REFERENCE", "")
        ).impact(instrument, payload.get("side"), payload.get("quantity"))
    )


@router.post("/shadow/{run_id}/compare")
def compare_shadow(run_id: str, request: Request, payload: dict = Body(...)):
    from .shadow import ShadowService

    db = database()
    principal(request, db, "research-read")
    return run(ShadowService(db).compare, run_id, payload.get("paper_equity"))


@router.post("/attribution/{order_id}")
def tag_order(order_id: str, request: Request, payload: dict = Body(...)):
    from .accounting import AttributionService

    db = database()
    principal(request, db, "research")
    run(
        AttributionService(db).tag_order,
        order_id,
        payload.get("strategy"),
        payload.get("reference_price"),
    )
    return {"tagged": True}


@router.get("/attribution")
def attribution(request: Request):
    from .accounting import AttributionService

    db = database()
    principal(request, db, "research-read")
    path = os.getenv("OSIRIS_PAPER_EXECUTION_DB", "")
    if not path:
        raise HTTPException(503, "canonical_paper_ledger_not_configured")
    return run(AttributionService(db).report, path)


@router.post("/telemetry/collect")
def collect_peers(request: Request):
    from .peer_telemetry import collect

    db = database()
    principal(request, db, "operations")
    return run(
        collect, db, os.getenv("OSIRIS_P2P_METRICS_URL", "http://127.0.0.1:9090/peer-metrics")
    )


@router.get("/telemetry/peers")
def peers(request: Request):
    db = database()
    principal(request, db)
    return db.get("peer-telemetry", "latest") or {"available": False}


@router.post("/clock/measure")
def measure_clock(request: Request):
    from .clock import probe

    db = database()
    principal(request, db, "operations")
    host = os.getenv("OSIRIS_NTP_HOST", "")
    if not host:
        raise HTTPException(503, "clock_source_not_configured")
    return run(probe, host)


@router.post("/models/{model_id}/predict-features")
def predict_features(model_id: str, request: Request, payload: dict = Body(...)):
    db = database()
    principal(request, db, "research-read")
    return run(
        ModelService(db).predict_from_store,
        model_id,
        payload.get("entity"),
        payload.get("as_of_ms"),
    )


@router.post("/models/promote")
def promote_model(request: Request, payload: dict = Body(...)):
    db = database()
    principal(request, db, "security")
    try:
        public_key = base64.b64decode(os.environ["OSIRIS_MODEL_REVIEW_PUBLIC_KEY"], validate=True)
    except (KeyError, ValueError):
        raise HTTPException(503, "model_review_key_not_configured") from None
    return run(ModelService(db).promote, payload, public_key)


@router.post("/secrets/{name}")
def set_secret(name: str, request: Request, payload: dict = Body(...)):
    db = database()
    principal(request, db, "security")
    if name.startswith("totp:"):
        raise HTTPException(403, "protected_identity_secret")
    if request.url.scheme != "https" and (
        os.getenv("OSIRIS_ENV", "").lower() in {"production", "prod"}
        or request.client.host not in {"127.0.0.1", "::1", "testclient"}
    ):
        raise HTTPException(400, "tls_required_for_credentials")
    service = identities(db).secrets
    version = run(
        service.put, name, payload.get("value"), expected_version=payload.get("expected_version", 0)
    )
    return {"stored": True, "version": version}
