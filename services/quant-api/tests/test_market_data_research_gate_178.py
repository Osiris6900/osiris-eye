"""Cross-workstream matrix gate: market data -> research (#178).

Failure condition under test: stale or unentitled data must never reach
research as valid. Fixture covers delayed, missing/malformed and stale ticks
plus an unentitled adapter, per docs/CROSS_WORKSTREAM_ACCEPTANCE_MATRIX.md.
"""
from datetime import datetime, timedelta, timezone

import pytest

from osiris_api.authorized_feeds import (
    FeedAdapterRejection,
    SyntheticAuthorizedAdapter,
    ingest_adapter_ticks,
    ticks_digest,
)
from osiris_api.walk_forward import (
    MomentumReferenceModel,
    governance_record,
    run_walk_forward,
)

NOW = datetime(2026, 11, 8, 12, 0, 0, tzinfo=timezone.utc)
START = NOW - timedelta(hours=3)


def test_rejected_ticks_never_reach_research():
    adapter = SyntheticAuthorizedAdapter(
        seed="gate-178", symbols=["BTC-USD"], start=START, step_seconds=60,
        count=24, inject={"delayed", "malformed", "stale", "future_available", "bad_price"},
    )
    ingested = ingest_adapter_ticks(adapter, instruments=["BTC-USD"], now=NOW)
    assert ingested["counts"] == {"fetched": 24, "admitted": 19, "rejected": 5}
    rejected_reasons = {item["reason"] for item in ingested["rejected"]}
    assert rejected_reasons == {
        "tick_stale_beyond_delay",
        "tick_source_required",
        "tick_available_in_future",
        "tick_price_not_positive_finite",
    }

    report = run_walk_forward(
        ingested["admitted"],
        symbol="BTC-USD",
        model_factory=MomentumReferenceModel,
        now=NOW,
        train_min=6,
        test_size=3,
        step=3,
        cost_bps_per_unit_turnover=25.0,
    )
    # Research consumed exactly the admitted set — nothing else.
    assert report["data_sha256"] == ticks_digest(ingested["admitted"])
    assert report["points"] == 19
    assert report["aggregate"]["folds"] >= 3

    record = governance_record(
        model_id="momentum-reference", model_version="0.1.0",
        params={"window": 4, "scale": 0.5}, report=report,
    )
    assert record["status"] == "research_only"
    assert record["live_trading_authorized"] is False
    assert record["data_sha256"] == report["data_sha256"]


def test_unentitled_adapter_reaches_research_with_zero_ticks():
    class UnentitledAdapter:
        name = "shadow-vendor"
        fetched = False

        def fetch(self, *, now):
            self.fetched = True
            return []

    spy = UnentitledAdapter()
    with pytest.raises(FeedAdapterRejection, match="adapter_not_registered"):
        ingest_adapter_ticks(spy, instruments=["BTC-USD"], now=NOW)
    assert spy.fetched is False
