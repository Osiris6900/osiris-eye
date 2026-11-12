"""Immutable, reproducible records for the existing bounded research backtest.

These local hashes bind installed source files and caller-supplied arrays. They
are not remote execution attestations, licensed datasets or model approvals.
"""
from __future__ import annotations

import hashlib
import json
import platform
from datetime import UTC, datetime
from pathlib import Path

from .agents import execute_tool
from .database import Database, canonical, digest, identifier

SCHEMA = "osiris.research_experiment.v1"
COLLECTION = "research-experiments"


def code_manifest() -> dict:
    root = Path(__file__).resolve().parents[1]
    names = ("backtesting.py", "control_plane/agents.py", "control_plane/database.py",
             "control_plane/experiments.py")
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in names}


def runtime_manifest() -> dict:
    return {"python": platform.python_version(), "implementation": platform.python_implementation(),
            "system": platform.system(), "machine": platform.machine()}


def _inputs(payload: dict) -> dict:
    if not isinstance(payload, dict) or set(payload) - {
        "signals", "asset_returns", "cost_bps_per_unit_turnover",
    } or not {"signals", "asset_returns"} <= set(payload):
        raise ValueError("invalid_experiment_payload")
    if any(not isinstance(payload[field], list) or not 2 <= len(payload[field]) <= 10000
           for field in ("signals", "asset_returns")):
        raise ValueError("invalid_experiment_size")
    # A detached canonical copy is used for both hashing and execution.
    return json.loads(canonical({"cost_bps_per_unit_turnover": 0.0, **payload}))


def _manifest(inputs: dict) -> dict:
    sources = code_manifest()
    data = {field: inputs[field] for field in ("signals", "asset_returns")}
    parameters = {"cost_bps_per_unit_turnover": inputs["cost_bps_per_unit_turnover"]}
    return {
        "tool": "backtest", "code_files": sources, "code_sha256": digest(sources),
        "data_sha256": digest(data), "parameters": parameters,
        "parameters_sha256": digest(parameters), "inputs_sha256": digest(inputs),
        "runtime": runtime_manifest(), "data_provenance": "caller_supplied_arrays",
        "execution_attestation": "local_source_and_result_hashes_only",
    }


def _verified(value: dict) -> dict:
    try:
        sealed = {key: item for key, item in value.items() if key != "receipt_sha256"}
        inputs, manifest = value["inputs"], value["manifest"]
        if (
            value["schema"] != SCHEMA
            or value["receipt_sha256"] != digest(sealed)
            or value["manifest_sha256"] != digest(manifest)
            or value["result_sha256"] != digest(value["result"])
            or manifest["inputs_sha256"] != digest(inputs)
            or manifest["code_sha256"] != digest(manifest["code_files"])
            or manifest["data_sha256"] != digest({k: inputs[k] for k in ("signals", "asset_returns")})
            or manifest["parameters"] != {"cost_bps_per_unit_turnover": inputs["cost_bps_per_unit_turnover"]}
            or manifest["parameters_sha256"] != digest(manifest["parameters"])
            or value["live_execution"] is not False
            or value["deployment_authorized"] is not False
        ):
            raise ValueError("experiment_record_tampered")
    except (KeyError, ValueError, TypeError, AttributeError):
        raise ValueError("experiment_record_tampered") from None
    return value


class ExperimentRegistry:
    def __init__(self, db: Database, *, clock=lambda: datetime.now(UTC)):
        self.db, self.clock = db, clock

    def get(self, experiment_id: str) -> dict | None:
        item = self.db.get(COLLECTION, identifier(experiment_id))
        if item is None:
            return None
        value = _verified(item["value"])
        if item["version"] != 1 or value["experiment_id"] != experiment_id:
            raise ValueError("experiment_record_tampered")
        return value

    def list(self, *, limit: int = 100) -> list[dict]:
        rows = self.db.list(COLLECTION, limit=limit)
        result = []
        for row in rows:
            value = _verified(row["value"])
            if row["version"] != 1 or value["experiment_id"] != row["id"]:
                raise ValueError("experiment_record_tampered")
            result.append({field: value[field] for field in (
                "experiment_id", "requester", "created_at", "status", "manifest_sha256",
                "result_sha256", "receipt_sha256", "live_execution", "deployment_authorized",
            )})
        return result

    def run(self, experiment_id: str, actor: str, payload: dict) -> dict:
        experiment_id, actor = identifier(experiment_id), identifier(actor)
        inputs = _inputs(payload)
        manifest = _manifest(inputs)
        fingerprint = digest({"requester": actor, "manifest": manifest})

        def replay():
            existing = self.get(experiment_id)
            if existing is None:
                return None
            if existing["request_fingerprint"] != fingerprint:
                raise ValueError("experiment_id_conflict")
            return {"experiment": existing, "replayed": True}

        prior = replay()
        if prior is not None:
            return prior
        result = execute_tool("backtest", inputs, self.db)
        canonical(result)
        if _manifest(inputs) != manifest:
            raise ValueError("experiment_code_or_runtime_changed_during_run")
        stamp = self.clock()
        if not isinstance(stamp, datetime) or stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError("experiment_timestamp_must_be_aware")
        record = {
            "schema": SCHEMA, "experiment_id": experiment_id, "requester": actor,
            "created_at": stamp.astimezone(UTC).isoformat(), "status": "completed_research",
            "inputs": inputs, "manifest": manifest, "manifest_sha256": digest(manifest),
            "request_fingerprint": fingerprint, "result": result, "result_sha256": digest(result),
            "live_execution": False, "deployment_authorized": False,
        }
        record["receipt_sha256"] = digest(record)
        try:
            self.db.put(COLLECTION, experiment_id, record)
        except ValueError as exc:
            if str(exc) != "version_conflict":
                raise
            # A concurrent identical request may have committed first. No record
            # is ever overwritten, including one created by another requester.
            prior = replay()
            if prior is None:
                raise ValueError("experiment_concurrent_write_failed") from None
            return prior
        return {"experiment": record, "replayed": False}

    def reproduce(self, experiment_id: str) -> dict:
        record = self.get(experiment_id)
        if record is None:
            raise ValueError("experiment_not_found")
        manifest = _manifest(record["inputs"])
        if manifest != record["manifest"]:
            return {"experiment_id": experiment_id, "reproduced": False,
                    "reason": "code_or_runtime_changed", "live_execution": False}
        result = execute_tool("backtest", record["inputs"], self.db)
        if _manifest(record["inputs"]) != manifest:
            raise ValueError("experiment_code_or_runtime_changed_during_run")
        actual = digest(result)
        return {"experiment_id": experiment_id, "reproduced": actual == record["result_sha256"],
                "expected_result_sha256": record["result_sha256"], "actual_result_sha256": actual,
                "live_execution": False, "deployment_authorized": False}
