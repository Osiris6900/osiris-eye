"""Persistent point-in-time features and immutable, provenance-bound snapshots."""

from __future__ import annotations

import json
import math
import re

from .database import MAX_SQLITE_INTEGER, Database, canonical, digest, identifier

SNAPSHOTS = "feature-snapshots"
SNAPSHOT_POLICY = "latest-event-then-availability-at-as-of-v1"


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
                "SELECT value,source_hash FROM cp_features "
                "WHERE feature=? AND entity=? AND event_ms=? AND available_ms=?",
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

    @staticmethod
    def _names(feature_names: object) -> list[str]:
        if not isinstance(feature_names, list) or not 1 <= len(feature_names) <= 100:
            raise ValueError("invalid_feature_query")
        try:
            names = [identifier(name) for name in feature_names]
        except (TypeError, ValueError):
            raise ValueError("invalid_feature_query") from None
        if len(set(names)) != len(names):
            raise ValueError("invalid_feature_query")
        return names

    @staticmethod
    def _as_of(as_of_ms: object) -> int:
        if type(as_of_ms) is not int or not 0 <= as_of_ms <= MAX_SQLITE_INTEGER:
            raise ValueError("invalid_feature_query")
        return as_of_ms

    @staticmethod
    def _point(conn, entity: str, names: list[str], as_of_ms: int) -> dict:
        rows = {}
        for name in names:
            row = conn.execute(
                "SELECT * FROM cp_features WHERE feature=? AND entity=? "
                "AND event_ms<=? AND available_ms<=? "
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

    def point_in_time(self, entity: str, feature_names: list[str], as_of_ms: int) -> dict:
        entity = identifier(entity)
        names, as_of_ms = self._names(feature_names), self._as_of(as_of_ms)
        with self.db.transaction() as conn:
            return self._point(conn, entity, names, as_of_ms)

    @staticmethod
    def _vector_parts(point: dict, names: list[str]) -> tuple[list[float], str]:
        values = [point["features"][name]["value"] for name in names]
        vector_sha256 = digest(
            {
                "feature_names": names,
                "values": values,
                "features": point["features"],
            }
        )
        return values, vector_sha256

    def vector(self, entity: str, feature_names: list[str], as_of_ms: int) -> dict:
        """Select the same ordered vector for dataset generation and model serving."""
        entity = identifier(entity)
        names, as_of_ms = self._names(feature_names), self._as_of(as_of_ms)
        with self.db.transaction() as conn:
            point = self._point(conn, entity, names, as_of_ms)
        values, vector_sha256 = self._vector_parts(point, names)
        return {
            "entity": entity,
            "as_of_ms": as_of_ms,
            "feature_names": names,
            "values": values,
            "feature_vector_sha256": vector_sha256,
            "feature_sources": point,
            "live_execution": False,
        }

    @staticmethod
    def _definition(feature_names: object, joins: object) -> tuple[list[str], list[dict]]:
        names = FeatureStore._names(feature_names)
        if not isinstance(joins, list) or not 1 <= len(joins) <= 1000:
            raise ValueError("invalid_snapshot_joins")
        normalized = []
        seen = set()
        try:
            for join in joins:
                if not isinstance(join, dict) or set(join) != {"entity", "as_of_ms"}:
                    raise ValueError("invalid_snapshot_joins")
                entity = identifier(join["entity"])
                as_of_ms = FeatureStore._as_of(join["as_of_ms"])
                if (entity, as_of_ms) in seen:
                    raise ValueError("duplicate_snapshot_join")
                seen.add((entity, as_of_ms))
                normalized.append({"entity": entity, "as_of_ms": as_of_ms})
        except (TypeError, ValueError) as exc:
            if str(exc) in {"duplicate_snapshot_join", "invalid_snapshot_joins"}:
                raise
            raise ValueError("invalid_snapshot_joins") from None
        if len(names) * len(normalized) > 2000:
            raise ValueError("snapshot_too_large")
        return names, sorted(normalized, key=lambda row: (row["entity"], row["as_of_ms"]))

    @staticmethod
    def _fingerprint(snapshot_id: str, actor: str, names: list[str], joins: list[dict]) -> str:
        return digest(
            {
                "snapshot_id": snapshot_id,
                "actor": actor,
                "feature_names": names,
                "joins": joins,
            }
        )

    @staticmethod
    def _validated_snapshot(snapshot: object) -> dict:
        if not isinstance(snapshot, dict) or "snapshot_sha256" not in snapshot:
            raise ValueError("invalid_feature_snapshot")
        body = dict(snapshot)
        stored_sha256 = body.pop("snapshot_sha256")
        if stored_sha256 != digest(body):
            raise ValueError("feature_snapshot_digest_mismatch")
        try:
            names, joins = FeatureStore._definition(body["feature_names"], body["joins"])
            if (
                body["input_sha256"]
                != FeatureStore._fingerprint(body["snapshot_id"], body["actor"], names, joins)
                or body["dataset_sha256"] != digest({"feature_names": names, "rows": body["rows"]})
                or len(body["rows"]) != len(joins)
            ):
                raise ValueError("feature_snapshot_digest_mismatch")
            for row, join in zip(body["rows"], joins):
                if (
                    row["entity"] != join["entity"]
                    or row["as_of_ms"] != join["as_of_ms"]
                    or row["feature_names"] != names
                ):
                    raise ValueError("feature_snapshot_digest_mismatch")
                point = {"features": row["features"]}
                values, vector_sha256 = FeatureStore._vector_parts(point, names)
                if row["values"] != values or row["feature_vector_sha256"] != vector_sha256:
                    raise ValueError("feature_snapshot_digest_mismatch")
        except (KeyError, TypeError):
            raise ValueError("invalid_feature_snapshot") from None
        return snapshot

    def create_snapshot(self, snapshot_id: str, actor: str, payload: object) -> dict:
        snapshot_id, actor = identifier(snapshot_id), identifier(actor)
        if not isinstance(payload, dict) or set(payload) != {"feature_names", "joins"}:
            raise ValueError("invalid_snapshot_payload")
        names, joins = self._definition(payload["feature_names"], payload["joins"])
        input_sha256 = self._fingerprint(snapshot_id, actor, names, joins)
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT body FROM cp_documents WHERE collection=? AND id=?",
                (SNAPSHOTS, snapshot_id),
            ).fetchone()
            if row is not None:
                stored = self._validated_snapshot(json.loads(row["body"]))
                if stored["input_sha256"] != input_sha256:
                    raise ValueError("feature_snapshot_id_conflict")
                return {"snapshot": stored, "replayed": True, "live_execution": False}
            rows = []
            for join in joins:
                point = self._point(conn, join["entity"], names, join["as_of_ms"])
                values, vector_sha256 = self._vector_parts(point, names)
                rows.append(
                    {
                        "entity": point["entity"],
                        "as_of_ms": point["as_of_ms"],
                        "feature_names": names,
                        "values": values,
                        "feature_vector_sha256": vector_sha256,
                        "features": point["features"],
                        "time_provenance_independently_verified": False,
                    }
                )
            snapshot = {
                "snapshot_id": snapshot_id,
                "actor": actor,
                "input_sha256": input_sha256,
                "policy": SNAPSHOT_POLICY,
                "feature_names": names,
                "joins": joins,
                "rows": rows,
                "dataset_sha256": digest({"feature_names": names, "rows": rows}),
                "time_provenance_independently_verified": False,
                "independent_model_approval": False,
                "live_execution": False,
            }
            snapshot["snapshot_sha256"] = digest(snapshot)
            conn.execute(
                "INSERT INTO cp_documents VALUES (?,?,?,?)",
                (SNAPSHOTS, snapshot_id, 1, canonical(snapshot).decode()),
            )
        return {"snapshot": snapshot, "replayed": False, "live_execution": False}

    def get_snapshot(self, snapshot_id: str) -> dict | None:
        item = self.db.get(SNAPSHOTS, identifier(snapshot_id))
        return None if item is None else self._validated_snapshot(item["value"])
