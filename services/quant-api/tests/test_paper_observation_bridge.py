"""Observation-to-paper integration uses current gates and durable idempotency."""
import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from osiris_api.data_store import ResearchStore
from osiris_api.p2p_ingestion import DATASET as OBSERVATION_DATASET
from osiris_api.p2p_ingestion import (
    ENABLE_ENV,
    PAPER_INTENT_ENV,
    ingest_p2p_observation,
)
from osiris_api.paper_observation_bridge import (
    PAPER_DATASET,
    PaperBridgeRefused,
    evaluate_stored_observation,
)
from osiris_api.risk import RiskLimits

NOW = datetime(2026, 11, 7, 12, 0, 0, tzinfo=timezone.utc)
PROVIDERS = {"binance-public", "coinbase-public"}


@pytest.fixture
def store(tmp_path):
    return ResearchStore(str(tmp_path / "research.sqlite3"))


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv(ENABLE_ENV, "1")
    monkeypatch.setenv(PAPER_INTENT_ENV, "1")


def _observation(payload=None, **overrides):
    quote = payload if payload is not None else {"last": 101.25, "source": "test_quote"}
    encoded = json.dumps(quote, separators=(",", ":"), sort_keys=True)
    observation = {
        "schema": "osiris.observation.v1",
        "instrument": "BTC-USD",
        "observed_at": (NOW - timedelta(seconds=30)).isoformat(),
        "provider": "binance-public",
        "payload": encoded,
        "payload_sha256": hashlib.sha256(encoded.encode()).hexdigest(),
    }
    observation.update(overrides)
    return observation


def _ingest(store, observation=None, now=NOW):
    return ingest_p2p_observation(
        store, observation or _observation(), allowed_providers=PROVIDERS, now=now,
    )


def _evaluate(store, record, **overrides):
    arguments = {
        "record_key": record["record_key"], "source": record["source"],
        "allowed_providers": PROVIDERS, "signals": [0.0, 0.5, 0.5],
        "asset_returns": [0.0, 0.01, -0.02], "quantity": 1.0, "now": NOW,
    }
    arguments.update(overrides)
    return evaluate_stored_observation(store, **arguments)


def test_stored_observation_creates_blocked_durable_intent_without_execution_controls(store, enabled):
    record = _ingest(store)
    result = _evaluate(store, record)
    assert result["symbol"] == "BTC-USD"
    assert result["paper_order_intent"]["reference_price"] == 101.25
    assert result["paper_order_intent"]["side"] == "BUY"
    assert result["paper_order_intent"]["status"] == "BLOCKED_BY_RISK"
    assert result["risk"]["approved_for_paper"] is False
    assert result["risk"]["reason"] == "risk_service_unavailable"
    assert result["risk"]["limits_evaluated"] is False
    assert result["backtest"]["periods"] == 2
    assert result["persistence"]["status"] == "stored"
    assert result["observation_provenance"]["provider"] == "binance-public"
    assert result["execution"] == {
        "mode": "paper_only", "broker_order_submitted": False,
        "live_execution": False, "fill_simulated": False,
    }
    durable = store.get(
        PAPER_DATASET, "paper:observation-bridge", result["persistence"]["record_key"],
    )
    assert durable is not None
    assert durable["payload"]["result"]["paper_intent_id"] == result["paper_intent_id"]


def test_identical_evaluation_is_idempotent_replay(store, enabled):
    record = _ingest(store)
    first = _evaluate(store, record)
    second = _evaluate(store, record)
    assert second["paper_intent_id"] == first["paper_intent_id"]
    assert second["persistence"]["status"] == "idempotent_replay"
    assert second["persistence"]["duplicate_created"] is False
    assert len(store.list(PAPER_DATASET)) == 1


def test_legacy_approved_intent_never_reapproves_on_idempotent_replay(
    store, enabled, monkeypatch,
):
    """Previously stored approvals must not bypass current pretrade gates."""
    import osiris_api.paper_observation_bridge as bridge

    original = bridge.evaluate_paper_workflow

    def simulate_legacy_approval(**kwargs):
        # This explicit test-only override simulates a historical durable
        # approval from before T16; it is not a runtime control override.
        return original(
            **kwargs,
            enforce_execution_controls=True,
            kill_switch_active=False,
            market_data_fresh=True,
            risk_service_available=True,
            current_gross_exposure=0.0,
        )

    record = _ingest(store)
    monkeypatch.setattr(bridge, "evaluate_paper_workflow", simulate_legacy_approval)
    legacy = _evaluate(store, record)
    assert legacy["risk"]["approved_for_paper"] is True
    assert legacy["paper_order_intent"]["status"] == "PAPER_INTENT_APPROVED"

    monkeypatch.setattr(bridge, "evaluate_paper_workflow", original)
    replay = _evaluate(store, record)
    assert replay["persistence"]["status"] == "idempotent_replay"
    assert replay["paper_intent_id"] == legacy["paper_intent_id"]
    assert replay["risk"]["approved_for_paper"] is False
    assert replay["risk"]["reason"] == "risk_service_unavailable"
    assert replay["paper_order_intent"]["status"] == "BLOCKED_BY_RISK"
    assert replay["execution"]["broker_order_submitted"] is False
    assert replay["execution"]["live_execution"] is False
    # Original historical record is preserved as evidence, not rewritten.
    assert len(store.list(PAPER_DATASET)) == 1


def test_changed_signal_creates_distinct_durable_intent(store, enabled):
    record = _ingest(store)
    _evaluate(store, record)
    _evaluate(store, record, signals=[0.0, -0.5, -0.5])
    assert len(store.list(PAPER_DATASET)) == 2


def test_paper_opt_in_is_checked_before_observation_access(store, monkeypatch):
    monkeypatch.delenv(PAPER_INTENT_ENV, raising=False)
    with pytest.raises(PaperBridgeRefused, match="paper_intent_disabled"):
        evaluate_stored_observation(
            store, record_key="missing", source="missing", allowed_providers=[],
            signals=[], asset_returns=[], quantity=1, now=NOW,
        )


def test_current_provider_revocation_blocks_paper_evaluation(store, enabled):
    record = _ingest(store)
    with pytest.raises(PaperBridgeRefused, match="provider_not_authorized"):
        _evaluate(store, record, allowed_providers={"coinbase-public"})
    assert store.list(PAPER_DATASET) == []


def test_stale_stored_observation_is_rejected_at_evaluation_time(store, enabled):
    record = _ingest(store)
    with pytest.raises(PaperBridgeRefused, match="observation_stale"):
        _evaluate(store, record, now=NOW + timedelta(seconds=301))
    assert store.list(PAPER_DATASET) == []


def test_risk_rejection_is_durable_but_never_live(store, enabled):
    record = _ingest(store)
    result = _evaluate(
        store, record, quantity=1000,
        limits=RiskLimits(max_order_notional=1000.0),
    )
    assert result["paper_order_intent"]["status"] == "BLOCKED_BY_RISK"
    # Without a trusted, available risk control plane, pretrade must block
    # before sizing limits are evaluated, even for an oversized order.
    assert result["risk"]["reason"] == "risk_service_unavailable"
    assert result["risk"]["limits_evaluated"] is False
    assert result["persistence"]["status"] == "stored"
    assert result["execution"]["live_execution"] is False


def test_hold_signal_records_no_order(store, enabled):
    record = _ingest(store)
    result = _evaluate(store, record, signals=[0.0, 0.0, 0.0])
    assert result["paper_order_intent"]["status"] == "PAPER_HOLD"
    assert result["risk"] == {"approved_for_paper": False, "reason": "hold_signal_no_order"}


def test_missing_stored_observation_fails_closed(store, enabled):
    with pytest.raises(PaperBridgeRefused, match="stored_observation_not_found"):
        evaluate_stored_observation(
            store, record_key="BTC-USD:" + "0" * 32, source="p2p:binance-public",
            allowed_providers=PROVIDERS, signals=[0.0, 1.0],
            asset_returns=[0.0, 0.01], quantity=1, now=NOW,
        )


def test_missing_market_price_is_rejected(store, enabled):
    record = _ingest(store, _observation({"volume_24h": 10}))
    with pytest.raises(PaperBridgeRefused, match="price_missing"):
        _evaluate(store, record)


def test_ambiguous_market_prices_are_rejected(store, enabled):
    record = _ingest(store, _observation({"last": 100, "price": 101}))
    with pytest.raises(PaperBridgeRefused, match="price_ambiguous"):
        _evaluate(store, record)


def test_invalid_signal_history_creates_no_intent(store, enabled):
    record = _ingest(store)
    with pytest.raises(PaperBridgeRefused, match="invalid_paper_workflow_input"):
        _evaluate(store, record, signals=[2.0, 2.0])
    assert store.list(PAPER_DATASET) == []


def test_feed_admission_can_be_required_for_paper_evaluation(store, enabled):
    record = _ingest(store)
    with pytest.raises(PaperBridgeRefused, match="feed_admission_required"):
        _evaluate(store, record, require_feed_admission=True)
    assert store.list(PAPER_DATASET) == []


def test_api_endpoint_evaluates_through_same_durable_bridge(store, enabled, monkeypatch):
    from fastapi.testclient import TestClient

    import osiris_api.main as main

    monkeypatch.setattr(main, "store", store)
    monkeypatch.delenv("OSIRIS_API_TOKEN", raising=False)
    current = datetime.now(timezone.utc)
    record = _ingest(
        store, _observation(observed_at=current.isoformat()), now=current,
    )
    response = TestClient(main.app).post("/paper/workflow/from-observation", json={
        "record_key": record["record_key"], "source": record["source"],
        "allowed_providers": sorted(PROVIDERS), "signals": [0.0, 0.5],
        "asset_returns": [0.0, 0.01], "quantity": 1,
    })
    assert response.status_code == 200
    assert response.json()["persistence"]["status"] == "stored"
    assert response.json()["execution"]["live_execution"] is False


def test_exact_store_get_returns_none_for_unknown_record(store):
    assert store.get(OBSERVATION_DATASET, "p2p:none", "none") is None
    with pytest.raises(ValueError, match="non-empty strings"):
        store.get(OBSERVATION_DATASET, "p2p:none", 123)
