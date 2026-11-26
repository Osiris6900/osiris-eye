"""Deterministic, immutable model-drift diagnostics and fail-closed quarantine."""

from __future__ import annotations

import bisect
import json
import math

from .database import Database, canonical, digest, identifier
from .features import FeatureStore
from .models import ModelService

REPORTS = "model-drift-reports"
QUARANTINES = "model-quarantines"
POLICY = "snapshot-psi-and-candidate-mse-v1"
MINIMUM_ROWS = 8
PSI_THRESHOLD = 0.25
MSE_RATIO_THRESHOLD = 2.0
MSE_ABSOLUTE_TOLERANCE = 1e-12


def _sealed(value: object, hash_field: str, error: str) -> dict:
    if not isinstance(value, dict) or hash_field not in value:
        raise ValueError(error)
    body = dict(value)
    expected = body.pop(hash_field)
    if expected != digest(body):
        raise ValueError(error)
    return body


def _validated_quarantine(value: object, model_id: str) -> dict:
    body = _sealed(value, "quarantine_sha256", "model_quarantine_digest_mismatch")
    if (
        body.get("model_id") != model_id
        or body.get("active") is not True
        or not isinstance(body.get("reasons"), list)
        or not body["reasons"]
        or body.get("independent_review_required") is not True
        or body.get("live_execution") is not False
    ):
        raise ValueError("invalid_model_quarantine")
    return value


def active_quarantine(db: Database, model_id: str) -> dict | None:
    item = db.get(QUARANTINES, identifier(model_id))
    if item is None:
        return None
    return _validated_quarantine(item["value"], model_id)


def _psi(baseline: list[float], candidate: list[float]) -> float:
    if min(len(baseline), len(candidate)) < MINIMUM_ROWS:
        raise ValueError("insufficient_drift_rows")
    ordered = sorted(baseline)
    edges = [
        ordered[int(len(ordered) * fraction)]
        for fraction in (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)
    ]
    baseline_counts = [0] * 10
    candidate_counts = [0] * 10
    for value in baseline:
        baseline_counts[bisect.bisect_right(edges, value)] += 1
    for value in candidate:
        candidate_counts[bisect.bisect_right(edges, value)] += 1
    result = 0.0
    for expected_count, actual_count in zip(baseline_counts, candidate_counts):
        expected = max(expected_count / len(baseline), 1e-6)
        actual = max(actual_count / len(candidate), 1e-6)
        result += (actual - expected) * math.log(actual / expected)
    return max(0.0, float(result))


def _request(payload: object) -> dict:
    required = {
        "model_id",
        "baseline_snapshot_id",
        "candidate_snapshot_id",
        "candidate_labels",
        "evaluation_as_of_ms",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise ValueError("invalid_drift_payload")
    try:
        request = {
            "model_id": identifier(payload["model_id"]),
            "baseline_snapshot_id": identifier(payload["baseline_snapshot_id"]),
            "candidate_snapshot_id": identifier(payload["candidate_snapshot_id"]),
            "candidate_labels": payload["candidate_labels"],
            "evaluation_as_of_ms": payload["evaluation_as_of_ms"],
        }
    except (TypeError, ValueError):
        raise ValueError("invalid_drift_payload") from None
    if request["baseline_snapshot_id"] == request["candidate_snapshot_id"]:
        raise ValueError("distinct_drift_snapshots_required")
    if type(request["evaluation_as_of_ms"]) is not int or request["evaluation_as_of_ms"] < 0:
        raise ValueError("invalid_drift_evaluation_time")
    labels = request["candidate_labels"]
    if not isinstance(labels, list) or not MINIMUM_ROWS <= len(labels) <= 2000:
        raise ValueError("invalid_drift_labels")
    for label in labels:
        if (
            not isinstance(label, dict)
            or set(label) != {"available_ms", "value"}
            or type(label["available_ms"]) is not int
            or label["available_ms"] < 0
            or type(label["value"]) not in (int, float)
            or not math.isfinite(label["value"])
            or abs(label["value"]) > 1e12
        ):
            raise ValueError("invalid_drift_labels")
    canonical(request)
    return request


def _fingerprint(report_id: str, actor: str, request: dict) -> str:
    return digest({"report_id": report_id, "actor": actor, "request": request})


def _validated_report(value: object) -> dict:
    body = _sealed(value, "report_sha256", "model_drift_report_digest_mismatch")
    try:
        request = _request(body["request"])
        if (
            body["input_sha256"] != _fingerprint(body["report_id"], body["actor"], request)
            or body["model_id"] != request["model_id"]
            or body["baseline_snapshot_id"] != request["baseline_snapshot_id"]
            or body["candidate_snapshot_id"] != request["candidate_snapshot_id"]
            or body["live_execution"] is not False
            or body["independent_model_approval"] is not False
        ):
            raise ValueError("model_drift_report_digest_mismatch")
    except KeyError:
        raise ValueError("invalid_model_drift_report") from None
    return value


class ModelDriftMonitor:
    def __init__(self, db: Database):
        self.db = db

    def get(self, report_id: str) -> dict | None:
        item = self.db.get(REPORTS, identifier(report_id))
        return None if item is None else _validated_report(item["value"])

    def quarantine(self, model_id: str) -> dict | None:
        return active_quarantine(self.db, model_id)

    def evaluate(self, report_id: str, actor: str, payload: object) -> dict:
        report_id, actor = identifier(report_id), identifier(actor)
        request = _request(payload)
        input_sha256 = _fingerprint(report_id, actor, request)
        old = self.get(report_id)
        if old is not None:
            if old["input_sha256"] != input_sha256:
                raise ValueError("model_drift_report_id_conflict")
            return {"report": old, "replayed": True, "live_execution": False}

        store = FeatureStore(self.db)
        baseline = store.get_snapshot(request["baseline_snapshot_id"])
        candidate = store.get_snapshot(request["candidate_snapshot_id"])
        if baseline is None or candidate is None:
            raise ValueError("drift_snapshot_not_found")
        artifact = ModelService.verified_artifact(self.db, request["model_id"])
        names = artifact["feature_names"]
        if baseline["feature_names"] != names or candidate["feature_names"] != names:
            raise ValueError("drift_snapshot_feature_order_mismatch")
        baseline_rows, candidate_rows = baseline["rows"], candidate["rows"]
        labels = request["candidate_labels"]
        if min(len(baseline_rows), len(candidate_rows)) < MINIMUM_ROWS:
            raise ValueError("insufficient_drift_rows")
        if len(labels) != len(candidate_rows):
            raise ValueError("drift_label_count_mismatch")
        evaluation_as_of_ms = request["evaluation_as_of_ms"]
        for row in baseline_rows + candidate_rows:
            if row["as_of_ms"] > evaluation_as_of_ms:
                raise ValueError("drift_snapshot_not_available_at_evaluation")
        for row, label in zip(candidate_rows, labels):
            if not row["as_of_ms"] <= label["available_ms"] <= evaluation_as_of_ms:
                raise ValueError("drift_label_not_available_at_evaluation")

        baseline_vectors = [row["values"] for row in baseline_rows]
        candidate_vectors = [row["values"] for row in candidate_rows]
        baseline_predictions = [
            ModelService.predict_artifact(artifact, vector) for vector in baseline_vectors
        ]
        candidate_predictions = [
            ModelService.predict_artifact(artifact, vector) for vector in candidate_vectors
        ]
        input_metrics = []
        alerts = []
        for index, name in enumerate(names):
            psi = _psi(
                [vector[index] for vector in baseline_vectors],
                [vector[index] for vector in candidate_vectors],
            )
            drifted = psi > PSI_THRESHOLD
            input_metrics.append({"feature": name, "psi": psi, "drifted": drifted})
            if drifted:
                alerts.append({"code": "input_drift", "feature": name, "severity": "critical"})
        output_psi = _psi(baseline_predictions, candidate_predictions)
        output_drifted = output_psi > PSI_THRESHOLD
        if output_drifted:
            alerts.append({"code": "output_drift", "severity": "critical"})
        candidate_mse = float(
            sum(
                (prediction - label["value"]) ** 2
                for prediction, label in zip(candidate_predictions, labels)
            )
            / len(labels)
        )
        holdout_mse = artifact["holdout_mse"]
        if holdout_mse == 0:
            calibration_ratio = 0.0 if candidate_mse == 0 else None
        else:
            calibration_ratio = candidate_mse / holdout_mse
        calibration_decayed = candidate_mse > max(
            MSE_RATIO_THRESHOLD * holdout_mse, MSE_ABSOLUTE_TOLERANCE
        )
        if calibration_decayed:
            alerts.append({"code": "calibration_decay", "severity": "critical"})

        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT body FROM cp_documents WHERE collection=? AND id=?", (REPORTS, report_id)
            ).fetchone()
            if row is not None:
                stored = _validated_report(json.loads(row["body"]))
                if stored["input_sha256"] != input_sha256:
                    raise ValueError("model_drift_report_id_conflict")
                return {"report": stored, "replayed": True, "live_execution": False}
            row = conn.execute(
                "SELECT body FROM cp_documents WHERE collection=? AND id=?",
                (QUARANTINES, request["model_id"]),
            ).fetchone()
            quarantine = (
                None
                if row is None
                else _validated_quarantine(json.loads(row["body"]), request["model_id"])
            )
            if quarantine is None and alerts:
                quarantine = {
                    "model_id": request["model_id"],
                    "report_id": report_id,
                    "model_artifact_sha256": artifact["artifact_sha256"],
                    "evaluation_as_of_ms": evaluation_as_of_ms,
                    "reasons": [alert["code"] for alert in alerts],
                    "active": True,
                    "independent_review_required": True,
                    "live_execution": False,
                }
                quarantine["quarantine_sha256"] = digest(quarantine)
                conn.execute(
                    "INSERT INTO cp_documents VALUES (?,?,?,?)",
                    (QUARANTINES, request["model_id"], 1, canonical(quarantine).decode()),
                )
            report = {
                "report_id": report_id,
                "actor": actor,
                "input_sha256": input_sha256,
                "policy": POLICY,
                "request": request,
                "model_id": request["model_id"],
                "model_artifact_sha256": artifact["artifact_sha256"],
                "baseline_snapshot_id": baseline["snapshot_id"],
                "baseline_snapshot_sha256": baseline["snapshot_sha256"],
                "candidate_snapshot_id": candidate["snapshot_id"],
                "candidate_snapshot_sha256": candidate["snapshot_sha256"],
                "baseline_rows": len(baseline_rows),
                "candidate_rows": len(candidate_rows),
                "input_drift": input_metrics,
                "output_drift": {"psi": output_psi, "drifted": output_drifted},
                "calibration": {
                    "candidate_mse": candidate_mse,
                    "model_holdout_mse": holdout_mse,
                    "model_baseline_mse": artifact["baseline_mse"],
                    "candidate_to_holdout_ratio": calibration_ratio,
                    "decayed": calibration_decayed,
                },
                "thresholds": {
                    "psi": PSI_THRESHOLD,
                    "mse_ratio": MSE_RATIO_THRESHOLD,
                    "mse_absolute_tolerance": MSE_ABSOLUTE_TOLERANCE,
                },
                "alerts": alerts,
                "quarantined": quarantine is not None,
                "quarantine": quarantine,
                "diagnostic_only": True,
                "independent_model_approval": False,
                "live_execution": False,
            }
            report["report_sha256"] = digest(report)
            conn.execute(
                "INSERT INTO cp_documents VALUES (?,?,?,?)",
                (REPORTS, report_id, 1, canonical(report).decode()),
            )
        return {"report": report, "replayed": False, "live_execution": False}
