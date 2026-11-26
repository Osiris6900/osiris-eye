import socket
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal

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
    ack = {"id": "broker-1", "client_order_id": "order-1", "status": "accepted",
           "symbol": "TEST", "side": "buy", "qty": "1", "limit_price": "100",
           "type": "limit", "time_in_force": "day", "filled_qty": "0",
           "filled_avg_price": None}
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
    stale = DepthService(db, entitlement_reference="fixture-only", clock=lambda: 106)
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
            packet[0], packet[1] = 0x24, 0x24
            packet[1] = 0x24
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
            return {"ObjectLockConfiguration": {"ObjectLockEnabled": True}}

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

        def get_object(self, **kwargs):
            from io import BytesIO

            assert kwargs["VersionId"] == "version-1"
            return {
                "VersionId": "version-1",
                "ContentLength": len(self.put["Body"]),
                "ServerSideEncryption": "aws:kms",
                "Body": BytesIO(self.put["Body"]),
            }

    s3 = S3()
    result = S3Custody(s3, "fixture-bucket", "fixture-kms").retain("evidence-1", bundle, public)
    assert result["version_id"] == "version-1"
    assert result["content_readback_verified"] is True
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


def test_execution_tca_uses_actual_fills_and_decomposes_costs(tmp_path, monkeypatch):
    from osiris_api import paper_execution

    ledger = tmp_path / "paper-tca.db"
    monkeypatch.setenv("OSIRIS_PAPER_EXECUTION_DB", str(ledger))
    paper_execution.initialize_paper_account("10000")
    observed = "2026-11-11T00:00:00+00:00"
    now = "2026-11-11T00:00:01+00:00"

    def order(key, side, quantity, price, fee, *, working=True):
        return paper_execution.submit_paper_order(
            client_order_id=key,
            symbol="TEST",
            side=side,
            quantity=quantity,
            price=price,
            fee=fee,
            observed_at=observed,
            now=now,
            working=working,
        )

    order("buy-tca", "BUY", "4", "103", "4")
    paper_execution.apply_paper_fill(
        client_order_id="buy-tca", client_fill_id="buy-fill-1", quantity="1",
        price="101", fee="0.25", observed_at=observed, now=now,
    )
    paper_execution.apply_paper_fill(
        client_order_id="buy-tca", client_fill_id="buy-fill-2", quantity="1",
        price="102", fee="0.25", observed_at=observed, now=now,
    )

    # Establish a real position, but deliberately leave this fill without a benchmark.
    order("seed-position", "BUY", "2", "100", "0", working=False)
    order("sell-tca", "SELL", "2", "97", "0.5")
    paper_execution.apply_paper_fill(
        client_order_id="sell-tca", client_fill_id="sell-fill-1", quantity="2",
        price="98", fee="0.5", observed_at=observed, now=now,
    )

    service = AttributionService(Database(str(tmp_path / "control-tca.db")))
    service.tag_order(
        "buy-tca", "strategy-a", "100", arrival_bid="99", arrival_ask="101"
    )
    service.tag_order(
        "sell-tca", "strategy-b", "100", arrival_bid="99", arrival_ask="101"
    )
    report = service.transaction_costs(str(ledger))

    assert report["fill_count"] == 4
    assert report["analyzed_fill_count"] == 3
    assert report["missing_reference_fill_count"] == 1
    assert report["reference_coverage_complete"] is False
    assert report["unfilled_opportunity_cost_included"] is False
    assert report["production_calibrated"] is False
    assert report["live_execution"] is False

    buy = next(row for row in report["orders"] if row["client_order_id"] == "buy-tca")
    assert buy["requested_quantity"] == "4"
    assert buy["filled_quantity"] == "2"
    assert buy["unfilled_quantity"] == "2"
    assert buy["partial_fill"] is True
    assert buy["average_fill_price"] == "101.5"
    assert Decimal(buy["slippage"]) == Decimal(3)
    assert Decimal(buy["fees"]) == Decimal("0.50")
    assert Decimal(buy["executed_shortfall"]) == Decimal("3.50")
    assert Decimal(buy["executed_shortfall_bps"]) == Decimal(175)
    assert Decimal(buy["spread_cost"]) == Decimal(2)
    assert Decimal(buy["impact_cost"]) == Decimal(1)
    assert Decimal(buy["quote_slippage"]) == (
        Decimal(buy["spread_cost"]) + Decimal(buy["impact_cost"])
    )

    sell = next(row for row in report["orders"] if row["client_order_id"] == "sell-tca")
    assert sell["partial_fill"] is False
    assert Decimal(sell["slippage"]) == Decimal(4)
    assert Decimal(sell["executed_shortfall"]) == Decimal("4.5")
    assert Decimal(sell["executed_shortfall_bps"]) == Decimal(225)
    assert Decimal(sell["spread_cost"]) == Decimal(2)
    assert Decimal(sell["impact_cost"]) == Decimal(2)

    instrument = report["instruments"]["TEST"]
    assert instrument["order_count"] == 2
    assert instrument["fill_count"] == 3
    assert Decimal(instrument["slippage"]) == Decimal(7)
    assert Decimal(instrument["fees"]) == Decimal(1)
    assert Decimal(instrument["executed_shortfall"]) == Decimal(8)
    assert Decimal(instrument["executed_shortfall_bps"]) == Decimal(200)
    assert Decimal(instrument["spread_cost"]) == Decimal(4)
    assert Decimal(instrument["impact_cost"]) == Decimal(3)
    assert set(report["strategies"]) == {"strategy-a", "strategy-b"}


@pytest.mark.parametrize(
    "kwargs,error",
    [
        ({"arrival_bid": "99"}, "arrival_quote_pair_required"),
        ({"arrival_bid": "101", "arrival_ask": "99"}, "crossed_or_locked"),
        ({"arrival_bid": "100", "arrival_ask": "100"}, "crossed_or_locked"),
        ({"arrival_bid": "98", "arrival_ask": "101"}, "midpoint"),
    ],
)
def test_execution_tca_arrival_quotes_fail_closed(tmp_path, kwargs, error):
    service = AttributionService(Database(str(tmp_path / "control.db")))
    with pytest.raises(ValueError, match=error):
        service.tag_order("order-1", "strategy-a", "100", **kwargs)


def test_execution_tca_tags_are_immutable_and_legacy_tags_remain_compatible(tmp_path):
    service = AttributionService(Database(str(tmp_path / "control.db")))
    service.tag_order("order-1", "strategy-a", "99")
    service.tag_order("order-1", "strategy-a", "99")
    assert service.db.get("order-strategies", "order-1")["value"] == {
        "strategy": "strategy-a", "reference_price": "99"
    }
    service.tag_order(
        "order-2", "strategy-a", "100", arrival_bid="99", arrival_ask="101"
    )
    service.tag_order(
        "order-2", "strategy-a", "100", arrival_bid="99", arrival_ask="101"
    )
    with pytest.raises(ValueError, match="order_strategy_is_immutable"):
        service.tag_order(
            "order-2", "strategy-a", "100", arrival_bid="98", arrival_ask="102"
        )


def test_execution_tca_refuses_a_tampered_canonical_ledger(tmp_path, monkeypatch):
    import sqlite3

    from osiris_api import paper_execution

    ledger = tmp_path / "paper.db"
    monkeypatch.setenv("OSIRIS_PAPER_EXECUTION_DB", str(ledger))
    paper_execution.initialize_paper_account("1000")
    now = "2026-11-11T00:00:01+00:00"
    paper_execution.submit_paper_order(
        client_order_id="buy-1", symbol="TEST", side="BUY", quantity="1", price="100",
        fee="0", observed_at=now, now=now,
    )
    service = AttributionService(Database(str(tmp_path / "control.db")))
    service.tag_order("buy-1", "strategy-a", "100")
    with sqlite3.connect(ledger) as connection:
        connection.execute("UPDATE paper_exec_fills SET price='999' WHERE client_order_id='buy-1'")
    with pytest.raises(ValueError, match="canonical_ledger"):
        service.transaction_costs(str(ledger))


def test_execution_tca_api_requires_research_access(tmp_path, monkeypatch):
    import base64
    import json

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from osiris_api import paper_execution
    from osiris_api.control_plane.api import router

    control = tmp_path / "control.db"
    monkeypatch.setenv("OSIRIS_CONTROL_DB", str(control))
    monkeypatch.setenv("OSIRIS_CONTROL_MASTER_KEY", base64.b64encode(b"k" * 32).decode())
    monkeypatch.setenv("OSIRIS_ADMIN_TOKENS", json.dumps({
        "analyst": "fixture-analyst-token", "viewer": "fixture-viewer-token",
    }))
    ledger = tmp_path / "paper.db"
    monkeypatch.setenv("OSIRIS_PAPER_EXECUTION_DB", str(ledger))
    paper_execution.initialize_paper_account("1000")
    now = "2026-11-11T00:00:01+00:00"
    paper_execution.submit_paper_order(
        client_order_id="buy-1", symbol="TEST", side="BUY", quantity="1", price="100",
        fee="0", observed_at=now, now=now,
    )
    AttributionService(Database(str(control))).tag_order("buy-1", "strategy-a", "100")
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    assert client.get("/control-plane/execution/tca").status_code == 401
    viewer = {"Authorization": "Bearer fixture-viewer-token"}
    assert client.get("/control-plane/execution/tca", headers=viewer).status_code == 403
    analyst = {"Authorization": "Bearer fixture-analyst-token"}
    response = client.get("/control-plane/execution/tca", headers=analyst)
    assert response.status_code == 200, response.text
    assert response.json()["analyzed_fill_count"] == 1
    assert response.json()["live_execution"] is False
    monkeypatch.delenv("OSIRIS_PAPER_EXECUTION_DB")
    assert client.get("/control-plane/execution/tca", headers=analyst).status_code == 503
