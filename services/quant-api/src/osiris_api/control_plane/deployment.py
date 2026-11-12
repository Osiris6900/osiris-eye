"""Signed research-service canary deployment, health checks and executable rollback."""

from __future__ import annotations

import re
from urllib.parse import quote, urlsplit

from .custody import verify_bundle
from .database import Database, identifier


class KubernetesTarget:
    def __init__(
        self,
        transport,
        api_origin: str,
        namespace: str,
        deployment: str,
        container: str,
        token: str,
    ):
        parsed = urlsplit(api_origin)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("https_kubernetes_origin_required")
        if not deployment.startswith("osiris-research-") or container != "research":
            raise ValueError("research_deployment_target_required")
        self.transport, self.token, self.container = transport, token, identifier(container)
        self.url = (
            api_origin.rstrip("/")
            + "/apis/apps/v1/namespaces/"
            + quote(identifier(namespace), safe="")
            + "/deployments/"
            + quote(identifier(deployment), safe="")
        )

    def state(self):
        data = self.transport.request(
            "GET", self.url, headers={"Authorization": "Bearer " + self.token}
        )
        containers = data["spec"]["template"]["spec"]["containers"]
        image = next((c["image"] for c in containers if c["name"] == self.container), None)
        if not image:
            raise ValueError("deployment_container_missing")
        desired = data["spec"].get("replicas", 1)
        status = data.get("status", {})
        return {
            "image": image,
            "replicas": desired,
            # Old ready pods may survive while a new ReplicaSet is still pending.
            "ready": all(status.get(field, 0) == desired for field in (
                "replicas", "updatedReplicas", "readyReplicas", "availableReplicas",
            ))
            and status.get("observedGeneration", -1)
            >= data["metadata"]["generation"],
        }

    def apply(self, image: str, replicas: int):
        if (
            not isinstance(image, str)
            or not re.fullmatch(r"[A-Za-z0-9./:_-]+@sha256:[0-9a-f]{64}", image)
            or type(replicas) is not int
            or not 0 <= replicas <= 100
        ):
            raise ValueError("immutable_image_and_bounded_replicas_required")
        return self.transport.request(
            "PATCH",
            self.url,
            headers={
                "Authorization": "Bearer " + self.token,
                "Content-Type": "application/strategic-merge-patch+json",
            },
            body={
                "spec": {
                    "replicas": replicas,
                    "template": {
                        "spec": {"containers": [{"name": self.container, "image": image}]}
                    },
                }
            },
        )


class RolloutController:
    def __init__(self, db: Database, public_key: bytes, production, canary, health_check, *, sleep):
        if production is canary or (
            getattr(production, "url", None)
            and getattr(production, "url", None) == getattr(canary, "url", None)
        ):
            raise ValueError("distinct_canary_and_production_targets_required")
        self.db, self.key, self.production, self.canary = db, public_key, production, canary
        self.health, self.sleep = health_check, sleep

    def _wait_ready(self, target, expected):
        for _ in range(30):
            self.sleep(1)
            current = target.state()
            if (current["ready"] and current["image"] == expected["image"]
                    and current["replicas"] == expected["replicas"]):
                return True
        return False

    def deploy(self, signed_release: dict) -> dict:
        release = verify_bundle(signed_release, self.key)
        if (
            not isinstance(release, dict)
            or set(release) != {"release_id", "image", "scope"}
            or release["scope"] != "research-only"
        ):
            raise ValueError("signed_research_release_required")
        release_id = identifier(release["release_id"])
        # Persist claim before external effects. An uncertain deployment must be reconciled,
        # never repeated automatically under the same release ID.
        self.db.put("rollouts", release_id, {"status": "started", "release": release})
        original, old_canary = self.production.state(), self.canary.state()
        if any(
            not re.fullmatch(r"[A-Za-z0-9./:_-]+@sha256:[0-9a-f]{64}", target["image"])
            for target in (original, old_canary)
        ):
            self.db.put(
                "rollouts",
                release_id,
                {"status": "blocked_nonimmutable_previous_image"},
                expected_version=1,
            )
            raise ValueError("immutable_previous_images_required_for_rollback")
        try:
            self.canary.apply(release["image"], 1)
            if not self._wait_ready(self.canary, {"image": release["image"], "replicas": 1}):
                raise ValueError("canary_readiness_timeout")
            for _ in range(3):
                self.sleep(1)
                current = self.canary.state()
                if (
                    not current["ready"]
                    or current["image"] != release["image"]
                    or current["replicas"] != 1
                    or self.health() is not True
                ):
                    raise ValueError("canary_health_failed")
            self.production.apply(release["image"], original["replicas"])
            if not self._wait_ready(self.production, {
                "image": release["image"], "replicas": original["replicas"],
            }):
                raise ValueError("production_readiness_timeout")
            outcome = {"status": "promoted", "image": release["image"], "live_execution": False}
        except Exception:  # noqa: BLE001 - any target failure requires best-effort rollback
            restored = True
            # Try both targets even if one rollback request fails. An accepted
            # PATCH is not proof that the prior workload has actually recovered.
            for target, expected in ((self.production, original), (self.canary, old_canary)):
                try:
                    target.apply(expected["image"], expected["replicas"])
                    if not self._wait_ready(target, expected):
                        restored = False
                except Exception:  # noqa: BLE001 - still attempt recovery of the other target
                    restored = False
            outcome = {"status": "rolled_back" if restored else "rollback_failed_operator_required",
                       "live_execution": False}
            if restored:
                outcome["image"] = original["image"]
        else:
            try:
                self.canary.apply(old_canary["image"], old_canary["replicas"])
                if not self._wait_ready(self.canary, old_canary):
                    outcome["status"] = "promoted_canary_cleanup_required"
            except Exception:  # noqa: BLE001 - persist uncertain cleanup for operator recovery
                outcome["status"] = "promoted_canary_cleanup_required"
        self.db.put("rollouts", release_id, outcome, expected_version=1)
        return outcome
