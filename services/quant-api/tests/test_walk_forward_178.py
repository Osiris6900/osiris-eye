"""Walk-forward leakage, cost, determinism and governance tests (#178)."""
from datetime import datetime, timedelta, timezone

import pytest

from osiris_api.authorized_feeds import FEED_TICK_SCHEMA, SyntheticAuthorizedAdapter, ingest_adapter_ticks
from osiris_api.walk_forward import (
    MomentumReferenceModel,
    WalkForwardRejection,
    governance_record,
    run_walk_forward,
)

NOW = datetime(2026, 11, 8, 12, 0, 0, tzinfo=timezone.utc)
START = NOW - timedelta(hours=3)


def make_ticks(prices, symbol="BTC-USD"):
    ticks = []
    for i, price in enumerate(prices):
        event = START + timedelta(seconds=60 * i)
        ticks.append({
            "schema": FEED_TICK_SCHEMA,
            "symbol": symbol,
            "source": "unit-test",
            "event_time": event.isoformat(),
            "available_time": (event + timedelta(seconds=1)).isoformat(),
            "received_time": (event + timedelta(seconds=2)).isoformat(),
            "price": str(price),
        })
    return ticks


def synthetic_ticks(count=24):
    adapter = SyntheticAuthorizedAdapter(
        seed="research-178", symbols=["BTC-USD"], start=START,
        step_seconds=60, count=count,
    )
    result = ingest_adapter_ticks(adapter, instruments=["BTC-USD"], now=NOW)
    return result["admitted"]


def run(ticks, **overrides):
    config = {
        "symbol": "BTC-USD", "model_factory": MomentumReferenceModel,
        "now": NOW, "train_min": 8, "test_size": 4, "step": 4,
        "cost_bps_per_unit_turnover": 0.0,
    }
    config.update(overrides)
    return run_walk_forward(ticks, **config)


def test_deterministic_report_hash():
    first = run(synthetic_ticks())
    second = run(synthetic_ticks())
    assert first["report_sha256"] == second["report_sha256"]
    assert first["data_sha256"] == second["data_sha256"]
    assert len(first["folds_detail"]) == 3


def test_future_mutation_does_not_change_earlier_folds():
    ticks = make_ticks([100 + (i % 5) * 0.3 + i * 0.1 for i in range(24)])
    baseline = run(ticks)
    poisoned = make_ticks(
        [100 + (i % 5) * 0.3 + i * 0.1 if i < 20 else 500 + i for i in range(24)]
    )
    mutated = run(poisoned)
    assert baseline["data_sha256"] != mutated["data_sha256"]
    assert baseline["folds_detail"][0] == mutated["folds_detail"][0]
    assert baseline["folds_detail"][1] == mutated["folds_detail"][1]


def test_costs_reduce_net_returns():
    free = run(synthetic_ticks(), cost_bps_per_unit_turnover=0.0)
    charged = run(synthetic_ticks(), cost_bps_per_unit_turnover=50.0)
    assert free["aggregate"]["total_costs_paid"] == 0.0
    assert charged["aggregate"]["total_costs_paid"] > 0.0
    assert (
        charged["aggregate"]["oos_total_return_net"]
        < free["aggregate"]["oos_total_return_net"]
    )


def test_exposure_out_of_bounds_rejected():
    class BadModel:
        def fit(self, visible):
            pass

        def exposure(self, visible):
            return 5.0

    with pytest.raises(WalkForwardRejection, match="model_exposure_out_of_bounds"):
        run(synthetic_ticks(), model_factory=BadModel)


def test_symbol_mismatch_rejected():
    ticks = make_ticks([100 + i * 0.1 for i in range(16)], symbol="ETH-USD")
    with pytest.raises(WalkForwardRejection, match="tick_symbol_mismatch"):
        run(ticks)


def test_insufficient_points_rejected():
    with pytest.raises(WalkForwardRejection, match="insufficient_points_for_one_fold"):
        run(synthetic_ticks(count=6))


def test_naive_now_rejected():
    with pytest.raises(WalkForwardRejection, match="now_missing_timezone"):
        run(synthetic_ticks(), now=NOW.replace(tzinfo=None))


def test_report_is_out_of_sample_and_honest():
    report = run(synthetic_ticks())
    assert report["in_sample_metrics"] == "not_reported"
    assert report["execution"] == "not_simulated"
    assert report["live_trading_authorized"] is False
    assert report["signal_alignment"] == "exposure_at_decision_i-1_applied_to_return_ending_at_i"
    assert report["aggregate"]["folds"] == 3
    assert all(fold["test_decisions"] == 4 for fold in report["folds_detail"])


def test_drift_flagged_on_regime_shift():
    flat = [100.0, 100.01, 99.99, 100.0, 100.01, 99.99, 100.0, 100.01, 99.99]
    wild = [105.0, 99.7, 104.8, 99.4, 104.9, 99.3, 105.1]
    report = run(make_ticks(flat + wild))
    assert report["drift"]["flagged"] is True
    assert report["drift"]["psi"] > report["drift"]["threshold"]


def test_drift_not_flagged_in_stable_regime():
    cycle = [0.001, -0.0005, 0.002, -0.001]
    prices = [100.0]
    for i in range(23):
        prices.append(prices[-1] * (1 + cycle[i % 4]))
    report = run(make_ticks(prices))
    assert report["drift"]["flagged"] is False


def test_governance_record_binds_report():
    report = run(synthetic_ticks())
    record = governance_record(
        model_id="momentum-reference", model_version="0.1.0",
        params={"window": 4, "scale": 0.5}, report=report,
    )
    assert record["status"] == "research_only"
    assert record["approvals"] == []
    assert record["live_trading_authorized"] is False
    assert record["data_sha256"] == report["data_sha256"]
    assert record["report_sha256"] == report["report_sha256"]
    assert record["out_of_sample"] == report["aggregate"]
    assert record["drift"] == report["drift"]


def test_governance_record_rejects_foreign_report():
    with pytest.raises(WalkForwardRejection, match="report_not_validated_walk_forward"):
        governance_record(
            model_id="momentum-reference", model_version="0.1.0",
            params={}, report={"schema": "other"},
        )
