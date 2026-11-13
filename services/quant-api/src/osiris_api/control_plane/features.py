"""Persistent feature values with event-time AND availability-time constraints."""

from __future__ import annotations

import math
import re

from .database import MAX_SQLITE_INTEGER, Database, identifier


class FeatureStore:
    def __init__(self, db: Database):
        self.db = db

    def put(
        self,
        feature: str,
        entity: str,
        event_ms: int,
        available_ms: int,
        value: float,
        source_hash: str,
    ):
        feature, entity = identifier(feature), identifier(entity)
        if (
            type(event_ms) is not int
            or type(available_ms) is not int
            or not 0 <= event_ms <= available_ms <= MAX_SQLITE_INTEGER
            or type(value) not in (int, float)
            or not math.isfinite(value)
            or not isinstance(source_hash, str)
            or not re.fullmatch(r"[0-9a-f]{64}", source_hash)
        ):
            raise ValueError("invalid_feature")
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT value,source_hash FROM cp_features WHERE feature=? AND entity=? AND event_ms=? AND available_ms=?",
                (feature, entity, event_ms, available_ms),
            ).fetchone()
            if row:
                if row["value"] != value or row["source_hash"] != source_hash:
                    raise ValueError("immutable_feature_conflict")
                return {"duplicate": True}
            conn.execute(
                "INSERT INTO cp_features VALUES (?,?,?,?,?,?)",
                (feature, entity, event_ms, available_ms, value, source_hash),
            )
        return {"duplicate": False}

    def point_in_time(self, entity: str, feature_names: list[str], as_of_ms: int) -> dict:
        entity = identifier(entity)
        if (
            not isinstance(feature_names, list)
            or not 1 <= len(feature_names) <= 100
            or len(set(feature_names)) != len(feature_names)
            or type(as_of_ms) is not int
            or not 0 <= as_of_ms <= MAX_SQLITE_INTEGER
        ):
            raise ValueError("invalid_feature_query")
        rows = {}
        with self.db.transaction() as conn:
            for name in feature_names:
                name = identifier(name)
                row = conn.execute(
                    "SELECT * FROM cp_features WHERE feature=? AND entity=? AND event_ms<=? AND available_ms<=? "
                    "ORDER BY event_ms DESC,available_ms DESC LIMIT 1",
                    (name, entity, as_of_ms, as_of_ms),
                ).fetchone()
                if row is None:
                    raise ValueError("point_in_time_feature_unavailable")
                rows[name] = dict(row)
        return {
            "entity": entity,
            "as_of_ms": as_of_ms,
            "features": rows,
            "time_provenance_independently_verified": False,
        }
