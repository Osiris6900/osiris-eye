"""Regressions for fail-closed boundaries found during the workstream audit."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from decimal import Decimal

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from osiris_api.control_plane.broker import money
from osiris_api.control_plane.clock import measure_reply, ntp_stamp
from osiris_api.control_plane.custody import sign_bundle
from osiris_api.control_plane.database import Database
from osiris_api.control_plane.deployment import KubernetesTarget, RolloutController
from osiris_api.control_plane.observability import Observability


@pytest.mark.parametrize("value", ["1e-999999999", "-1e-999999999", "0e999999999", "1e-1001"])
def test_decimal_exponents_are_bounded_before_downstream_arithmetic(value):
    with pytest.raises(ValueError, match="decimal_precision_out_of_range"):
        money(value, positive=False)


def test_exact_decimal_risk_precision_is_preserved():
    assert money("100.000000000000000001") == Decimal("100.000000000000000001")
    assert money("1e-200") == Decimal("1e-200")


@pytest.mark.parametrize("field", ["sent", "received", "elapsed"])
@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf"), True, "100"])
def test_clock_measurement_never_reports_nonfinite_or_coerced_times(field, invalid):
    packet = bytearray(48)
    packet[0], packet[1] = 0x24, 1
    packet[24:32] = b"12345678"
    packet[32:40] = packet[40:48] = ntp_stamp(100)
    timing = {"sent": 100, "received": 100.01, "elapsed": 0.01, field: invalid}
    with pytest.raises(ValueError, match="untrusted_or_invalid_ntp_reply"):
        measure_reply(bytes(packet), b"12345678", **timing)


class Transport:
    def __init__(self, response=None):
        self.response = {} if response is None else response
        self.calls = []

    def request(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return deepcopy(self.response)


@pytest.mark.parametrize("enabled", ["alert", "trace"])
def test_disabled_export_kind_cannot_starve_the_enabled_destination(tmp_path, enabled):
    db = Database(str(tmp_path / "control.db"))
    service = Observability(db, clock=lambda: 100)
    # Queue the disabled kind first, with a limit of one.
    if enabled == "alert":
        trace = service.observe("platform", 200, 5)
        alert = service.alert("platform", "down")
    else:
        alert = service.alert("platform", "down")
        trace = service.observe("platform", 200, 5)
    transport = Transport()
    endpoints = {enabled + "_endpoint": "https://configured.invalid/ingest"}
    assert service.flush(transport, limit=1, **endpoints)["delivered"] == 1
    assert len(transport.calls) == 1
    delivered, pending = (alert, trace) if enabled == "alert" else (trace, alert)
    assert db.get("telemetry-outbox", delivered)["value"]["delivered"] is True
    assert db.get("telemetry-outbox", pending)["value"]["delivered"] is False
    assert service.flush(transport, limit=1, **endpoints)["delivered"] == 0


@pytest.mark.parametrize("response", [
    {"partialSuccess": {"rejectedSpans": "1", "errorMessage": "fixture rejection"}},
    {"partialSuccess": {"rejectedSpans": 1}}, {"partialSuccess": None},
    {"partialSuccess": {"rejectedSpans": True}}, [],
])
def test_rejected_trace_export_remains_durable_and_can_retry(tmp_path, response):
    db = Database(str(tmp_path / "control.db"))
    service = Observability(db, clock=lambda: 100)
    trace = service.observe("platform", 500, 10)
    transport = Transport(response)
    with pytest.raises(ValueError, match="trace_export_not_accepted"):
        service.flush(transport, trace_endpoint="https://configured.invalid/v1/traces")
    assert db.get("telemetry-outbox", trace)["value"]["delivered"] is False
    transport.response = {"partialSuccess": {"rejectedSpans": "0"}}
    assert service.flush(transport, trace_endpoint="https://configured.invalid/v1/traces")["delivered"] == 1
    assert transport.calls[0][1]["headers"] == transport.calls[1][1]["headers"]


def test_concurrent_deduplicated_alerts_commit_one_outbox_item(tmp_path):
    db = Database(str(tmp_path / "control.db"))
    service = Observability(db, clock=lambda: 100)
    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(lambda _: service.alert("platform", "down"), range(24)))
    assert len(set(ids)) == 1
    assert len(db.list("telemetry-outbox")) == 1
    assert service.flush(Transport())["delivered"] == 0


def deployment_response():
    return {
        "metadata": {"generation": 2},
        "spec": {"replicas": 2, "template": {"spec": {"containers": [
            {"name": "research", "image": "registry/research@sha256:" + "a" * 64},
        ]}}},
        "status": {"observedGeneration": 2, "replicas": 2, "updatedReplicas": 2,
                   "readyReplicas": 2, "availableReplicas": 2},
    }


@pytest.mark.parametrize("changes", [
    {}, {"updatedReplicas": 0}, {"availableReplicas": 1}, {"replicas": 3},
    {"readyReplicas": 1}, {"observedGeneration": 1},
])
def test_kubernetes_readiness_requires_the_complete_updated_workload(changes):
    response = deployment_response()
    response["status"].update(changes)
    target = KubernetesTarget(Transport(response), "https://cluster.invalid", "research",
                              "osiris-research-test", "research", "FIXTURE")
    assert target.state()["ready"] is (not changes)


@pytest.mark.parametrize("rollback_behavior", ["healthy", "unready", "rejected"])
def test_rollback_reports_success_only_after_both_targets_recover(tmp_path, rollback_behavior):
    old, new = "registry/research@sha256:" + "a" * 64, "registry/research@sha256:" + "b" * 64

    class Target:
        def __init__(self, behavior="healthy"):
            self.image, self.replicas, self.ready = old, 2, True
            self.behavior, self.calls = behavior, []

        def state(self):
            return {"image": self.image, "replicas": self.replicas, "ready": self.ready}

        def apply(self, image, replicas):
            self.calls.append((image, replicas))
            if image == old and self.behavior == "rejected":
                raise ValueError("fixture rollback request rejected")
            self.image, self.replicas = image, replicas
            self.ready = not (image == old and self.behavior == "unready")

    production, canary = Target(rollback_behavior), Target()
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    db = Database(str(tmp_path / "control.db"))
    rollout = RolloutController(db, public, production, canary, lambda: False, sleep=lambda _: None)
    bundle = sign_bundle({"release_id": "test", "image": new, "scope": "research-only"}, key)
    result = rollout.deploy(bundle)
    expected = "rolled_back" if rollback_behavior == "healthy" else "rollback_failed_operator_required"
    assert result["status"] == expected
    assert db.get("rollouts", "test")["value"] == result
    assert canary.calls[-1] == (old, 2)
    assert result["live_execution"] is False
