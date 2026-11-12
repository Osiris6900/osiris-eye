"""Explicit operator commands. No service or external operation starts on import."""

from __future__ import annotations

import argparse
import base64
import json
import os
import time
from pathlib import Path

from .agents import JobWorker
from .clock import probe
from .coordination import Coordinator
from .custody import S3Custody
from .database import Database
from .deployment import KubernetesTarget, RolloutController
from .observability import Observability
from .peer_telemetry import collect
from .replication import Replicator
from .secrets import resolve_reference
from .vendors import HttpJson


class RemoteQueue:
    def __init__(self, origin: str, session_token: str, transport):
        self.origin, self.transport = origin.rstrip("/"), transport
        self.headers = {"Authorization": "Session " + session_token}

    def claim(self, worker, kinds, *, ttl=60):
        return self.transport.request(
            "POST",
            self.origin + "/control-plane/workers/claim",
            headers=self.headers,
            body={"kinds": kinds},
        )["job"]

    def finish(self, job_id, worker, token, result, *, failed=False):
        from .database import identifier

        return self.transport.request(
            "POST",
            self.origin + "/control-plane/workers/" + identifier(job_id) + "/finish",
            headers=self.headers,
            body={"claim_token": token, "result": result, "failed": failed},
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=[
            "worker",
            "clock",
            "collect-peers",
            "export-telemetry",
            "replicate",
            "retain-evidence",
            "rollout",
        ],
    )
    parser.add_argument("--database", default=os.getenv("OSIRIS_CONTROL_DB"))
    parser.add_argument("--input")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if not args.database:
        parser.error("--database or OSIRIS_CONTROL_DB is required")
    db, transport = Database(args.database), HttpJson()
    if args.command == "worker":
        origin = os.getenv("OSIRIS_PLATFORM_API_URL", "")
        queue = (
            RemoteQueue(
                origin, resolve_reference(os.getenv("OSIRIS_WORKER_SESSION_REF", "")), transport
            )
            if origin
            else Coordinator(db)
        )
        worker = JobWorker(queue, db, os.getenv("OSIRIS_WORKER_ID", "research-worker"))
        while True:
            result = worker.once()
            if result:
                print(json.dumps(result), flush=True)
            if args.once:
                return
            time.sleep(1)
    elif args.command == "clock":
        print(json.dumps(probe(os.environ["OSIRIS_NTP_HOST"])))
    elif args.command == "collect-peers":
        print(
            json.dumps(
                collect(
                    db, os.getenv("OSIRIS_P2P_METRICS_URL", "http://127.0.0.1:9090/peer-metrics")
                )
            )
        )
    elif args.command == "export-telemetry":
        print(
            json.dumps(
                Observability(db).flush(
                    transport,
                    trace_endpoint=os.getenv("OSIRIS_OTLP_TRACES_URL"),
                    alert_endpoint=os.getenv("OSIRIS_ALERT_WEBHOOK_URL"),
                )
            )
        )
    elif args.command == "replicate":
        keys = json.loads(os.environ["OSIRIS_REPLICATION_PUBLIC_KEYS"])
        replica = Replicator(
            db, {name: base64.b64decode(key, validate=True) for name, key in keys.items()}
        )
        print(json.dumps(replica.apply(json.loads(Path(args.input).read_text()))))
    elif args.command == "retain-evidence":
        if os.getenv("OSIRIS_S3_CUSTODY_ENABLED") != "1":
            raise ValueError("s3_custody_not_enabled")
        import boto3  # Optional infrastructure extra; never loaded by normal API startup.

        client = boto3.client("s3")
        bundle = json.loads(Path(args.input).read_text())
        public = base64.b64decode(os.environ["OSIRIS_EVIDENCE_PUBLIC_KEY"], validate=True)
        custody = S3Custody(
            client, os.environ["OSIRIS_EVIDENCE_BUCKET"], os.environ["OSIRIS_EVIDENCE_KMS_KEY_ID"]
        )
        print(json.dumps(custody.retain(bundle["sha256"], bundle, public)))
    else:
        if os.getenv("OSIRIS_RESEARCH_ROLLOUT_ENABLED") != "1":
            raise ValueError("research_rollout_not_enabled")
        token = resolve_reference(os.environ["OSIRIS_KUBE_TOKEN_REF"])
        target_args = (
            transport,
            os.environ["OSIRIS_KUBE_API"],
            os.environ["OSIRIS_RESEARCH_NAMESPACE"],
        )
        production = KubernetesTarget(
            *target_args, os.environ["OSIRIS_RESEARCH_DEPLOYMENT"], "research", token
        )
        canary = KubernetesTarget(
            *target_args, os.environ["OSIRIS_RESEARCH_CANARY_DEPLOYMENT"], "research", token
        )

        def healthy():
            return transport.request("GET", os.environ["OSIRIS_RESEARCH_CANARY_HEALTH_URL"]).get(
                "status"
            ) in {"ok", "healthy"}

        rollout = RolloutController(
            db,
            base64.b64decode(os.environ["OSIRIS_RELEASE_PUBLIC_KEY"], validate=True),
            production,
            canary,
            healthy,
            sleep=time.sleep,
        )
        print(json.dumps(rollout.deploy(json.loads(Path(args.input).read_text()))))


if __name__ == "__main__":
    main()
