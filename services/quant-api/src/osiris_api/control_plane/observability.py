"""Persistent low-cardinality metrics, OTLP/HTTP trace outbox and alert webhooks."""

from __future__ import annotations

import json
import secrets
import time
from urllib.parse import urlsplit

from .database import Database, canonical, digest, identifier


class Observability:
    def __init__(self, db: Database, *, clock=time.time):
        self.db, self.clock = db, clock

    def observe(self, component: str, status: int, duration_ms: float):
        component = identifier(component)
        if (
            component not in {"platform", "vendor", "worker", "depth", "shadow"}
            or type(status) is not int
            or not 100 <= status <= 599
            or not 0 <= duration_ms <= 3_600_000
        ):
            raise ValueError("invalid_metric")
        key = f"{component}:{status // 100}xx"
        with self.db.transaction() as conn:
            old = conn.execute(
                "SELECT body FROM cp_documents WHERE collection='metrics' AND id=?", (key,)
            ).fetchone()
            metric = json.loads(old[0]) if old else {"count": 0, "duration_ms": 0}
            metric["count"] += 1
            metric["duration_ms"] += duration_ms
            conn.execute(
                "INSERT INTO cp_documents VALUES ('metrics',?,1,?) ON CONFLICT(collection,id) "
                "DO UPDATE SET version=version+1,body=excluded.body",
                (key, canonical(metric).decode()),
            )
        trace_id, span_id, ended = (
            secrets.token_hex(16),
            secrets.token_hex(8),
            int(self.clock() * 1e9),
        )
        span = {
            "traceId": trace_id,
            "spanId": span_id,
            "name": component,
            "startTimeUnixNano": str(ended - int(duration_ms * 1e6)),
            "endTimeUnixNano": str(ended),
            "attributes": [
                {"key": "http.response.status_code", "value": {"intValue": str(status)}}
            ],
            "status": {"code": 2 if status >= 500 else 1},
        }
        self.db.put(
            "telemetry-outbox", trace_id, {"kind": "trace", "payload": span, "delivered": False}
        )
        return trace_id

    def alert(self, component: str, code: str, *, severity: str = "warning"):
        component, code = identifier(component), identifier(code)
        if severity not in {"warning", "critical"}:
            raise ValueError("invalid_alert_severity")
        bucket = int(self.clock() // 300)
        payload = {"component": component, "code": code, "severity": severity, "window": bucket}
        key = digest(payload)
        if self.db.get("telemetry-outbox", key) is None:
            self.db.put(
                "telemetry-outbox", key, {"kind": "alert", "payload": payload, "delivered": False}
            )
        return key

    def prometheus(self) -> str:
        lines = [
            "# TYPE osiris_requests_total counter",
            "# TYPE osiris_request_duration_ms_sum counter",
        ]
        for row in self.db.list("metrics"):
            component, status = row["id"].split(":")
            labels = f'component="{component}",status_class="{status}"'
            lines.extend(
                [
                    f"osiris_requests_total{{{labels}}} {row['value']['count']}",
                    f"osiris_request_duration_ms_sum{{{labels}}} {row['value']['duration_ms']}",
                ]
            )
        return "\n".join(lines) + "\n"

    def flush(
        self,
        transport,
        *,
        trace_endpoint: str | None = None,
        alert_endpoint: str | None = None,
        headers: dict | None = None,
        limit: int = 100,
    ) -> dict:
        for endpoint in (trace_endpoint, alert_endpoint):
            if endpoint:
                parsed = urlsplit(endpoint)
                if (
                    parsed.scheme != "https"
                    or not parsed.hostname
                    or parsed.username
                    or parsed.password
                    or parsed.fragment
                ):
                    raise ValueError("https_telemetry_endpoint_required")
        delivered = 0
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("invalid_export_limit")
        with self.db.transaction() as conn:
            pending = conn.execute(
                "SELECT id,version,body FROM cp_documents WHERE collection='telemetry-outbox' "
                "AND json_extract(body,'$.delivered')=0 ORDER BY rowid LIMIT ?",
                (limit,),
            ).fetchall()
        for record in pending:
            row = {
                "id": record["id"],
                "version": record["version"],
                "value": json.loads(record["body"]),
            }
            item = row["value"]
            endpoint = trace_endpoint if item["kind"] == "trace" else alert_endpoint
            if item["delivered"] or not endpoint:
                continue
            payload = (
                {
                    "resourceSpans": [
                        {
                            "resource": {
                                "attributes": [
                                    {
                                        "key": "service.name",
                                        "value": {"stringValue": "osiris-control-plane"},
                                    }
                                ]
                            },
                            "scopeSpans": [
                                {"scope": {"name": "osiris"}, "spans": [item["payload"]]}
                            ],
                        }
                    ]
                }
                if item["kind"] == "trace"
                else item["payload"]
            )
            transport.request(
                "POST",
                endpoint,
                body=payload,
                headers={**(headers or {}), "Idempotency-Key": row["id"]},
            )
            self.db.put(
                "telemetry-outbox",
                row["id"],
                {**item, "delivered": True},
                expected_version=row["version"],
            )
            delivered += 1
        return {"delivered": delivered, "delivery_semantics": "at_least_once_with_idempotency_key"}
