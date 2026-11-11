"""Human-reviewed research requests and allowlisted, resource-limited subprocess tools."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

from .coordination import Coordinator
from .database import Database, canonical, identifier

TOOLS = {"backtest", "model_train", "shadow_run", "risk_summary"}
ROLES = {
    "analyst": {"backtest", "model_train", "shadow_run"},
    "risk_officer": {"risk_summary", "backtest"},
    "operator": TOOLS,
    "security_admin": TOOLS,
}


class ResearchAgents:
    def __init__(self, db: Database):
        self.db, self.queue = db, Coordinator(db)

    def request(self, request_id: str, actor: str, role: str, tool: str, payload: dict):
        request_id, actor = identifier(request_id), identifier(actor)
        if tool not in ROLES.get(role, set()) or not isinstance(payload, dict):
            raise ValueError("tool_permission_denied")
        fields = {
            "backtest": (
                {"signals", "asset_returns"},
                {"signals", "asset_returns", "cost_bps_per_unit_turnover"},
            ),
            "risk_summary": ({"returns", "confidence"}, {"returns", "confidence"}),
            "model_train": (
                {"model_id", "rows"},
                {"model_id", "rows", "holdout", "ridge", "feature_names"},
            ),
            "shadow_run": (
                {"run_id", "ticks"},
                {
                    "run_id",
                    "ticks",
                    "initial_cash",
                    "max_order_amount",
                    "daily_loss_limit",
                    "fee_bps",
                },
            ),
        }
        required, allowed = fields[tool]
        if not required <= set(payload) or set(payload) - allowed:
            raise ValueError("unexpected_research_payload_fields")
        canonical(payload)
        value = {
            "requester": actor,
            "role": role,
            "tool": tool,
            "payload": payload,
            "status": "awaiting_review",
        }
        self.db.put("agent-requests", request_id, value)
        return {"id": request_id, "status": "awaiting_review"}

    def approve(self, request_id: str, approver: str, role: str):
        if role not in {"operator", "risk_officer", "security_admin"}:
            raise ValueError("review_permission_denied")
        item = self.db.get("agent-requests", identifier(request_id))
        if not item or item["value"]["requester"] == identifier(approver):
            raise ValueError("independent_approver_required")
        request = item["value"]
        if request["status"] not in {"awaiting_review", "approved"}:
            raise ValueError("request_not_reviewable")
        if request["status"] == "awaiting_review":
            self.db.put(
                "agent-requests",
                request_id,
                {**request, "status": "approved", "approver": approver},
                expected_version=item["version"],
            )
        return self.queue.submit(request_id, request["tool"], request["payload"])


def _limits():
    import resource

    resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
    resource.setrlimit(resource.RLIMIT_AS, (1_073_741_824, 1_073_741_824))
    resource.setrlimit(resource.RLIMIT_FSIZE, (8_388_608, 8_388_608))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def execute_tool(kind: str, payload: dict, db: Database):
    """No code strings, shell commands, URLs, credentials or arbitrary plugins."""
    if kind not in TOOLS or not isinstance(payload, dict):
        raise ValueError("unsupported_tool")
    if kind == "backtest":
        from ..backtesting import simulate_signals

        if set(payload) - {"signals", "asset_returns", "cost_bps_per_unit_turnover"} or not {
            "signals",
            "asset_returns",
        } <= set(payload):
            raise ValueError("invalid_backtest_payload")
        if any(
            not isinstance(payload[k], list) or not 2 <= len(payload[k]) <= 10000
            for k in ("signals", "asset_returns")
        ):
            raise ValueError("invalid_backtest_size")
        return simulate_signals(**payload)
    if kind == "risk_summary":
        from ..platform_modules import historical_var_es

        if (
            set(payload) != {"returns", "confidence"}
            or not isinstance(payload["returns"], list)
            or not 2 <= len(payload["returns"]) <= 10000
        ):
            raise ValueError("invalid_risk_payload")
        var, es = historical_var_es(payload["returns"], payload["confidence"])
        return {"historical_var": var, "expected_shortfall": es, "live_execution": False}
    if kind == "model_train":
        from .models import ModelService

        if set(payload) - {"model_id", "rows", "holdout", "ridge", "feature_names"} or not {
            "model_id",
            "rows",
        } <= set(payload):
            raise ValueError("invalid_model_payload")
        return ModelService(db).train(**payload)
    from .shadow import ShadowService

    if set(payload) - {
        "run_id",
        "ticks",
        "initial_cash",
        "max_order_amount",
        "daily_loss_limit",
        "fee_bps",
    } or not {"run_id", "ticks"} <= set(payload):
        raise ValueError("invalid_shadow_payload")
    return ShadowService(db).run(**payload)


class JobWorker:
    def __init__(self, queue, database: Database, worker: str):
        self.queue, self.db, self.worker = queue, database, identifier(worker)

    def once(self):
        claim = self.queue.claim(self.worker, sorted(TOOLS), ttl=60)
        if claim is None:
            return None
        failed = False
        try:
            if claim["kind"] not in TOOLS:
                raise ValueError("unsupported_tool")
            with tempfile.TemporaryDirectory(prefix="osiris-research-worker-") as folder:
                result = subprocess.run(
                    [
                        sys.executable,
                        "-I",
                        "-m",
                        "osiris_api.control_plane.agent_worker",
                        "--kind",
                        claim["kind"],
                        "--database",
                        self.db.path,
                    ],
                    input=canonical(claim["payload"]),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    cwd=folder,
                    timeout=30,
                    env={
                        "PATH": os.defpath,
                        "LANG": "C.UTF-8",
                        "OPENBLAS_NUM_THREADS": "1",
                        "OMP_NUM_THREADS": "1",
                    },
                    preexec_fn=_limits if sys.platform == "linux" else None,
                    check=False,
                )
            if result.returncode or len(result.stdout) > 1_048_576:
                raise ValueError("restricted_tool_failed")
            output = json.loads(result.stdout)
        except (ValueError, OSError, subprocess.SubprocessError):
            failed, output = True, {"error": "restricted_tool_failed"}
        return self.queue.finish(
            claim["id"], self.worker, claim["claim_token"], output, failed=failed
        )
