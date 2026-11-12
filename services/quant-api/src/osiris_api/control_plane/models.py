"""Reproducible ridge training with chronological holdout and versioned artifacts."""

from __future__ import annotations

import math

import numpy as np

from .database import Database, canonical, digest, identifier


class ModelService:
    def __init__(self, db: Database):
        self.db = db

    def train(
        self,
        model_id: str,
        rows: list[dict],
        *,
        holdout: int = 5,
        ridge: float = 1e-3,
        feature_names: list[str] | None = None,
    ) -> dict:
        model_id = identifier(model_id)
        if (
            not isinstance(rows, list)
            or not 12 <= len(rows) <= 10000
            or type(holdout) is not int
            or not 2 <= holdout <= len(rows) // 2
            or type(ridge) not in (int, float)
            or not math.isfinite(ridge)
            or ridge <= 0
        ):
            raise ValueError("invalid_training_configuration")
        canonical(rows)
        previous, width = -1, None
        for row in rows:
            required = {"event_ms", "available_ms", "label_available_ms", "features", "target"}
            if not isinstance(row, dict) or set(row) != required:
                raise ValueError("invalid_training_row")
            if (
                any(
                    type(row[k]) is not int
                    for k in ("event_ms", "available_ms", "label_available_ms")
                )
                or row["event_ms"] <= previous
                or row["available_ms"] > row["event_ms"]
                or row["available_ms"] < 0
                or row["label_available_ms"] < row["event_ms"]
            ):
                raise ValueError("training_time_or_feature_leakage")
            values = row["features"]
            if not isinstance(values, list) or not 1 <= len(values) <= 50:
                raise ValueError("invalid_feature_vector")
            width = len(values) if width is None else width
            if len(values) != width or any(
                type(x) not in (int, float) or not math.isfinite(x) or abs(x) > 1e12
                for x in values + [row["target"]]
            ):
                raise ValueError("invalid_training_numeric")
            previous = row["event_ms"]
        split = len(rows) - holdout
        names = feature_names if feature_names is not None else [f"f{i}" for i in range(width)]
        if not isinstance(names, list) or len(names) != width or len(set(names)) != width:
            raise ValueError("invalid_feature_names")
        names = [identifier(name) for name in names]
        if any(row["label_available_ms"] >= rows[split]["event_ms"] for row in rows[:split]):
            raise ValueError("training_label_leakage_into_holdout")
        x = np.asarray([r["features"] for r in rows], dtype=float)
        y = np.asarray([r["target"] for r in rows], dtype=float)
        mean, scale = x[:split].mean(axis=0), x[:split].std(axis=0)
        scale[scale == 0] = 1
        design = np.column_stack([np.ones(len(rows)), (x - mean) / scale])
        penalty = np.eye(width + 1) * ridge
        penalty[0, 0] = 0
        try:
            coefficients = np.linalg.solve(
                design[:split].T @ design[:split] + penalty, design[:split].T @ y[:split]
            )
        except np.linalg.LinAlgError:
            raise ValueError("training_matrix_unusable") from None
        predicted = design[split:] @ coefficients
        mse = float(np.mean((predicted - y[split:]) ** 2))
        baseline_mse = float(np.mean((y[:split].mean() - y[split:]) ** 2))
        if (
            not np.isfinite(coefficients).all()
            or not math.isfinite(mse)
            or not math.isfinite(baseline_mse)
        ):
            raise ValueError("nonfinite_training_result")
        artifact = {
            "kind": "ridge-v1",
            "dataset_sha256": digest(rows),
            "feature_names": names,
            "mean": mean.tolist(),
            "scale": scale.tolist(),
            "coefficients": coefficients.tolist(),
            "ridge": ridge,
            "train_rows": split,
            "holdout_rows": holdout,
            "holdout_mse": mse,
            "baseline_mse": baseline_mse,
            "holdout_start_ms": rows[split]["event_ms"],
            "independent_model_approval": False,
            "live_execution": False,
        }
        artifact["artifact_sha256"] = digest(artifact)
        self.db.put("models", model_id, artifact)
        return {"model_id": model_id, **artifact}

    def predict(self, model_id: str, features: list[float]) -> float:
        item = self.db.get("models", identifier(model_id))
        if not item:
            raise ValueError("model_not_found")
        artifact = dict(item["value"])
        expected = artifact.pop("artifact_sha256")
        if digest(artifact) != expected:
            raise ValueError("model_artifact_tampered")
        if (
            not isinstance(features, list)
            or len(features) != len(artifact["mean"])
            or any(
                type(x) not in (int, float) or not math.isfinite(x) or abs(x) > 1e12
                for x in features
            )
        ):
            raise ValueError("invalid_prediction_features")
        value = artifact["coefficients"][0] + sum(
            coef * (x - mean) / scale
            for coef, x, mean, scale in zip(
                artifact["coefficients"][1:], features, artifact["mean"], artifact["scale"]
            )
        )
        if not math.isfinite(value):
            raise ValueError("nonfinite_prediction")
        return float(value)

    def predict_from_store(self, model_id: str, entity: str, as_of_ms: int):
        from .features import FeatureStore

        item = self.db.get("models", identifier(model_id))
        if not item:
            raise ValueError("model_not_found")
        names = item["value"]["feature_names"]
        point = FeatureStore(self.db).point_in_time(entity, names, as_of_ms)
        return {
            "prediction": self.predict(
                model_id, [point["features"][name]["value"] for name in names]
            ),
            "feature_sources": point,
            "live_execution": False,
        }

    def promote(self, bundle: dict, public_key: bytes):
        from .custody import verify_bundle

        decision = verify_bundle(bundle, public_key)
        if (
            not isinstance(decision, dict)
            or set(decision)
            != {"alias", "model_id", "artifact_sha256", "previous_version", "scope"}
            or decision["scope"] != "paper-research"
        ):
            raise ValueError("signed_paper_research_model_decision_required")
        alias, model_id = identifier(decision["alias"]), identifier(decision["model_id"])
        item = self.db.get("models", model_id)
        if not item or item["value"]["artifact_sha256"] != decision["artifact_sha256"]:
            raise ValueError("reviewed_model_digest_mismatch")
        self.predict(model_id, item["value"]["mean"])
        version = self.db.put(
            "model-aliases",
            alias,
            {
                "model_id": model_id,
                "artifact_sha256": decision["artifact_sha256"],
                "scope": "paper-research",
            },
            expected_version=decision["previous_version"],
        )
        return {"alias": alias, "model_id": model_id, "version": version, "live_execution": False}
