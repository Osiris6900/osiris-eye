from decimal import Decimal

import pytest

from osiris_api.control_plane.database import Database
from osiris_api.control_plane.features import FeatureStore
from osiris_api.control_plane.models import ModelService
from osiris_api.control_plane.shadow import ShadowService


def test_point_in_time_values_exclude_future_backfills(tmp_path):
    db = Database(str(tmp_path / "features.db"))
    features = FeatureStore(db)
    features.put("price", "A", 10, 10, 10.0, "a" * 64)
    features.put("price", "A", 15, 30, 999.0, "b" * 64)
    assert features.point_in_time("A", ["price"], 20)["features"]["price"]["value"] == 10
    assert features.point_in_time("A", ["price"], 30)["features"]["price"]["value"] == 999
    with pytest.raises(ValueError, match="immutable"):
        features.put("price", "A", 10, 10, 99.0, "a" * 64)
    assert (
        FeatureStore(Database(db.path)).point_in_time("A", ["price"], 20)["features"]["price"][
            "value"
        ]
        == 10
    )


def training_rows():
    return [
        {
            "event_ms": i * 10,
            "available_ms": i * 10,
            "label_available_ms": i * 10 + 1,
            "features": [float(i)],
            "target": 2.0 * i + 1,
        }
        for i in range(20)
    ]


def test_training_holdout_and_persistent_prediction(tmp_path):
    db = Database(str(tmp_path / "models.db"))
    result = ModelService(db).train("model-a", training_rows(), ridge=1e-6)
    assert result["holdout_mse"] < 1e-8
    assert result["holdout_mse"] < result["baseline_mse"]
    assert ModelService(Database(db.path)).predict("model-a", [20.0]) == pytest.approx(
        41.0, abs=1e-4
    )
    FeatureStore(db).put("f0", "TEST", 200, 200, 20.0, "a" * 64)
    assert ModelService(db).predict_from_store("model-a", "TEST", 200)[
        "prediction"
    ] == pytest.approx(41.0, abs=1e-4)
    rows = training_rows()
    rows[0]["label_available_ms"] = 10000
    with pytest.raises(ValueError, match="leakage"):
        ModelService(db).train("model-b", rows)
    item = db.get("models", "model-a")
    item["value"]["coefficients"][0] += 1
    db.put("models", "model-a", item["value"], expected_version=item["version"])
    with pytest.raises(ValueError, match="tampered"):
        ModelService(db).predict("model-a", [20.0])


def test_shadow_is_lagged_durable_idempotent_and_risk_stopped(tmp_path):
    db = Database(str(tmp_path / "shadow.db"))
    ticks = [
        {"event_ms": 1, "price": "100", "target": 1},
        {"event_ms": 2, "price": "100", "target": 1},
        {"event_ms": 3, "price": "1", "target": 1},
    ]
    service = ShadowService(db)
    result = service.run("run-a", ticks, daily_loss_limit="10")
    assert result["risk_halted"]
    assert result["broker_orders"] == 0
    assert result["fills"][0]["event_ms"] == 2
    assert Decimal(result["cash"]) >= 0
    assert ShadowService(Database(db.path)).run("run-a", ticks, daily_loss_limit="10") == result
    assert service.compare("run-a", result["equity"])["max_absolute_difference"] == "0"
