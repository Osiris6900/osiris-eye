from datetime import datetime, timezone
import socket
import threading
import time

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from osiris_api.control_plane.accounting import AttributionService
from osiris_api.control_plane.broker import AlpacaPaperBroker, decode_fix, encode_fix
from osiris_api.control_plane.clock import measure_reply, ntp_stamp, probe
from osiris_api.control_plane.custody import S3Custody, sign_bundle, verify_bundle
from osiris_api.control_plane.database import Database
from osiris_api.control_plane.deployment import RolloutController
from osiris_api.control_plane.depth import DepthService
from osiris_api.control_plane.observability import Observability
from osiris_api.control_plane.vendors import VendorBarAdapter
from osiris_api.risk import RiskLimits


class FakeTransport:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


@pytest.mark.parametrize(
    "provider,response",
    [
        (
            "alpaca",
            {
                "bars": [
                    {"t": "2026-01-01T00:00:00Z", "o": 100, "h": 102, "l": 99, "c": 101, "v": 50}
                ]
            },
        ),
        (
            "oanda",
            {
                "candles": [
                    {
                        "complete": True,
                        "time": "2026-01-01T00:00:00Z",
                        "mid": {"o": "100", "h": "102", "l": "99", "c": "101"},
                        "volume": 50,
                    }
                ]
            },
        ),
        (
            "polygon",
            {"results": [{"t": 1767225600000, "o": 100, "h": 102, "l": 99, "c": 101, "v": 50}]},
        ),
        (
            "twelvedata",
            {
                "values": [
                    {
                        "datetime": "2026-01-01",
                        "open": "100",
                        "high": "102",
                        "low": "99",
                        "close": "101",
                        "volume": "50",
                    }
                ]
            },
        ),
    ],
)
def test_real_vendor_formats_normalize_without_backdating(provider, response):
    transport = FakeTransport([response])
    adapter = VendorBarAdapter(
        provider,
        {"key": "TEST-KEY", "secret": "TEST-SECRET"},
        entitlement_reference="fixture-only",
        transport=transport,
        clock=lambda: datetime(2026, 1, 3, tzinfo=timezone.utc),
    )
    bars = adapter.fetch_bars("TEST", "2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z")
    assert bars[0]["close"] == 101
    assert bars[0]["available_at"] == "2026-01-03T00:00:00+00:00"
    assert "TEST-KEY" not in transport.calls[0][1]
    assert "TEST-SECRET" not in str(bars)


def test_pagination_cannot_forward_credentials_to_another_host():
    transport = FakeTransport([{"results": [], "next_url": "https://attacker.invalid/steal"}])
    adapter = VendorBarAdapter(
        "polygon", {"key": "TEST-KEY"}, entitlement_reference="fixture", transport=transport
    )
    with pytest.raises(ValueError, match="unsafe_provider_pagination"):
        adapter.fetch_bars("TEST", "2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z")
    assert len(transport.calls) == 1


def test_paper_broker_risk_idempotency_and_uncertain_outcome(tmp_path):
    db = Database(str(tmp_path / "broker.db"))
    order = {
        "client_order_id": "order-1",
        "symbol": "TEST",
        "side": "BUY",
        "quantity": "1",
        "limit_price": "100",
    }
    context = dict(
        limits=RiskLimits(), position=0, gross=0, daily_pnl=0, kill_switch=False, fresh=True
    )
    ack = {"id": "broker-1", "client_order_id": "order-1", "status": "accepted"}
    transport = FakeTransport([ValueError("unknown-network-outcome"), ack])
    broker = AlpacaPaperBroker(
        db, {"key": "TEST", "secret": "TEST"}, enabled=True, transport=transport
    )
    with pytest.raises(ValueError, match="kill_switch"):
        broker.submit(order, **{**context, "kill_switch": True})
    assert not transport.calls
    with pytest.raises(ValueError, match="outcome_unknown"):
        broker.submit(order, **context)
    with pytest.raises(ValueError, match="reconcile"):
        broker.submit(order, **context)
    assert len(transport.calls) == 1
    assert broker.reconcile("order-1") == ack
    assert broker.submit(order, **context) == ack
    assert len(transport.calls) == 2
    assert all(url.startswith("https://paper-api.alpaca.markets/") for _, url, _ in transport.calls)


def test_fix_body_length_checksum_and_duplicate_tags():
    raw = encode_fix([(35, "D"), (34, "1"), (11, "order-1"), (55, "TEST")])
    assert decode_fix(raw)[55] == "TEST"
    with pytest.raises(ValueError):
        decode_fix(raw.replace(b"TEST", b"FAKE"))
    with pytest.raises(ValueError):
        encode_fix([(35, "D"), (35, "D")])


def test_l2_capture_replay_rejection_and_partial_depth(tmp_path):
    db = Database(str(tmp_path / "depth.db"))
    snapshot = {
        "sequence": 1,
        "bids": [["100", "2", 1]],
        "asks": [["101", "1", 1], ["102", "1", 1]],
    }
    service = DepthService(
        db,
        entitlement_reference="fixture-only",
        transport=FakeTransport([snapshot, snapshot]),
        clock=lambda: 100,
    )
    service.capture("BTC-USD")
    result = service.impact("BTC-USD", "buy", "3")
    assert result["unfilled_quantity"] == "1"
    assert result["average_price"] == "101.5"
    with pytest.raises(ValueError, match="replayed"):
        service.capture("BTC-USD")
    stale = DepthService(db, entitlement_reference="fixture", clock=lambda: 106)
    with pytest.raises(ValueError, match="stale"):
        stale.impact("BTC-USD", "buy", "1")


def test_actual_loopback_sntp_exchange_and_nonce_rejection():
    server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server.bind(("127.0.0.1", 0))
    server.settimeout(3)
    port = server.getsockname()[1]

    def reply():
        try:
            request, address = server.recvfrom(48)
            packet = bytearray(48)
            packet[0] = 0x24
            packet[1] = 1
            packet[24:32] = request[40:48]
            packet[32:40] = ntp_stamp(time.time())
            packet[40:48] = ntp_stamp(time.time())
            server.sendto(packet, address)
        finally:
            server.close()

    thread = threading.Thread(target=reply)
    thread.start()
    result = probe("127.0.0.1", port)
    thread.join(timeout=3)
    assert abs(result["offset_ms"]) < 100
    assert result["time_source_authenticated"] is False
    with pytest.raises(ValueError):
        measure_reply(bytes(48), b"12345678", 100, 101, 1)


def test_otlp_and_webhook_outbox_progress_after_first_page(tmp_path):
    service = Observability(Database(str(tmp_path / "metrics.db")), clock=lambda: 100)
    service.observe("platform", 200, 5)
    service.observe("platform", 500, 10)
    service.alert("platform", "backend-down", severity="critical")
    transport = FakeTransport([{}, {}, {}])
    for _ in range(3):
        assert (
            service.flush(
                transport,
                trace_endpoint="https://telemetry.invalid/v1/traces",
                alert_endpoint="https://alerts.invalid/hook",
                limit=1,
            )["delivered"]
            == 1
        )
    assert (
        service.flush(transport, trace_endpoint="https://telemetry.invalid/v1/traces", limit=1)[
            "delivered"
        ]
        == 0
    )
    assert (
        'osiris_requests_total{component="platform",status_class="2xx"} 1' in service.prometheus()
    )
    assert all(call[2]["headers"]["Idempotency-Key"] for call in transport.calls)


def test_signed_compliance_retention_requires_verified_bucket_and_version():
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    bundle = sign_bundle({"run": "fixture-only", "live_execution": False}, private)
    assert verify_bundle(bundle, public)["live_execution"] is False

    class S3:
        def get_object_lock_configuration(self, **kwargs):
            return {"ObjectLockConfiguration": {"ObjectLockEnabled": "Enabled"}}

        def get_bucket_versioning(self, **kwargs):
            return {"Status": "Enabled"}

        def put_object(self, **kwargs):
            self.put = kwargs
            return {"VersionId": "version-1"}

        def get_object_retention(self, **kwargs):
            return {
                "Retention": {
                    "Mode": self.put["ObjectLockMode"],
                    "RetainUntilDate": self.put["ObjectLockRetainUntilDate"],
                }
            }

    s3 = S3()
    result = S3Custody(s3, "fixture-bucket", "fixture-kms").retain("evidence-1", bundle, public)
    assert result["version_id"] == "version-1"
    assert s3.put["ObjectLockMode"] == "COMPLIANCE"
    assert s3.put["IfNoneMatch"] == "*"
    bundle["payload"]["run"] = "tampered"
    with pytest.raises(ValueError):
        verify_bundle(bundle, public)


def test_canary_failure_rolls_back_and_cannot_repeat_release(tmp_path):
    db = Database(str(tmp_path / "rollout.db"))
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    old, new = "registry/research@sha256:" + "a" * 64, "registry/research@sha256:" + "b" * 64

    class Target:
        def __init__(self):
            self.image, self.replicas = old, 2

        def state(self):
            return {"image": self.image, "replicas": self.replicas, "ready": True}

        def apply(self, image, replicas):
            self.image, self.replicas = image, replicas

    production, canary = Target(), Target()
    controller = RolloutController(
        db, public, production, canary, lambda: False, sleep=lambda _: None
    )
    release = sign_bundle(
        {"release_id": "release-1", "image": new, "scope": "research-only"}, private
    )
    assert controller.deploy(release)["status"] == "rolled_back"
    assert production.image == old and canary.image == old
    with pytest.raises(ValueError):
        controller.deploy(release)


def test_attribution_reads_actual_canonical_fills(tmp_path, monkeypatch):
    from osiris_api import paper_execution

    ledger = tmp_path / "paper.db"
    monkeypatch.setenv("OSIRIS_PAPER_EXECUTION_DB", str(ledger))
    paper_execution.initialize_paper_account("1000")
    observed = "2026-11-11T00:00:00+00:00"
    now = "2026-11-11T00:00:01+00:00"
    for oid, side, price in [("buy-1", "BUY", "100"), ("sell-1", "SELL", "110")]:
        assert (
            paper_execution.submit_paper_order(
                client_order_id=oid,
                symbol="TEST",
                side=side,
                quantity="1",
                price=price,
                fee="1",
                observed_at=observed,
                now=now,
            )["status"]
            == "filled"
        )
    service = AttributionService(Database(str(tmp_path / "control.db")))
    service.tag_order("buy-1", "strategy-a", "99")
    service.tag_order("sell-1", "strategy-a", "112")
    report = service.report(str(ledger))
    assert report["realized_pnl"] == "8"
    assert report["strategies"]["strategy-a"]["fees"] == "2"
    assert report["strategies"]["strategy-a"]["slippage"] == "3"
