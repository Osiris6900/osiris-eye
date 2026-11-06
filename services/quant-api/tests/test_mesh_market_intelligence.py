"""Tests for mesh-native market intelligence (consensus + research)."""
import math

import pytest

from osiris_api.mesh_market_intelligence import (
    consensus_price,
    correlation_matrix,
    explain_move,
    lead_lag_scan,
    pearson,
    quote_value,
    regime_label,
    returns_series,
)


def _reports(values, sources=None):
    sources = sources or [f"src-{(i % 2) + 1}" for i in range(len(values))]
    return [
        {"observer": f"peer-{i}", "source": sources[i], "value": v}
        for i, v in enumerate(values)
    ]


# ------------------------------------------------------------ consensus

def test_consensus_accepts_agreeing_quorum():
    result = consensus_price(_reports([100.0, 100.2, 99.9, 100.1]))
    assert result["accepted"] is True
    assert result["price"] == pytest.approx(100.05)
    assert result["outliers_rejected"] == 0
    assert result["live_execution"] is False


def test_consensus_rejects_single_source_even_with_quorum():
    reports = [{"observer": f"p{i}", "source": "only-one", "value": 100.0}
               for i in range(4)]
    result = consensus_price(reports)
    assert result["accepted"] is False
    assert result["reason"] == "source_quorum_not_met"


def test_consensus_rejects_lying_source_outlier():
    result = consensus_price(_reports([100.0, 100.1, 99.9, 250.0]))
    assert result["accepted"] is True
    assert result["price"] == pytest.approx(100.0)
    assert result["outliers_rejected"] == 1


def test_consensus_fails_closed_on_disagreement():
    result = consensus_price(_reports([100.0, 110.0, 120.0]))
    assert result["accepted"] is False
    assert result["reason"] == "consensus_disagreement"


def test_consensus_fails_closed_below_observer_quorum():
    result = consensus_price(_reports([100.0, 100.1]))
    assert result["accepted"] is False
    assert result["reason"] == "observer_quorum_not_met"


def test_consensus_rejects_invalid_reports():
    with pytest.raises(ValueError):
        consensus_price(_reports([100.0, float("nan"), 100.0]))
    with pytest.raises(ValueError):
        consensus_price([{"observer": "p", "source": "", "value": 1.0}])


def test_quote_value_extracts_recognized_fields():
    assert quote_value({"payload": '{"last": 69000.5}'}) == 69000.5
    assert quote_value({"payload": '{"close": 5810.5}'}) == 5810.5
    assert quote_value({"payload": '{"rate_per_usd": 0.921}'}) == 0.921
    with pytest.raises(ValueError):
        quote_value({"payload": '{"unknown": 1}'})
    with pytest.raises(ValueError):
        quote_value({"payload": "not-json"})
    with pytest.raises(ValueError):
        quote_value({"payload": '{"last": -5}'})


# ------------------------------------------------------------ research

def test_returns_series_basic():
    assert returns_series([100.0, 110.0, 99.0]) == pytest.approx([0.1, -0.1])
    assert returns_series([100.0]) == []
    with pytest.raises(ValueError):
        returns_series([100.0, 0.0])


def test_pearson_known_values():
    assert pearson([1, 2, 3], [2, 4, 6]) == pytest.approx(1.0)
    assert pearson([1, 2, 3], [6, 4, 2]) == pytest.approx(-1.0)
    with pytest.raises(ValueError):
        pearson([1, 1, 1], [1, 2, 3])


def test_correlation_matrix_shape():
    matrix = correlation_matrix({
        "A": [0.01, -0.02, 0.03, 0.005],
        "B": [0.012, -0.018, 0.028, 0.006],
    })
    assert set(matrix) == {"A", "B"}
    assert matrix["A"]["A"] == pytest.approx(1.0)
    assert matrix["A"]["B"] > 0.9


def test_lead_lag_detects_leader():
    leader = [0.01, -0.02, 0.03, -0.01, 0.02, -0.03, 0.01, 0.02, -0.01, 0.03]
    follower = [0.0] + leader[:-1]  # same pattern one period later
    scan = lead_lag_scan(leader, follower, max_lag=3)
    assert scan["lag"] == 1
    assert scan["direction"] == "x_leads"
    assert scan["correlation"] > 0.9
    assert scan["causality"] == "not_established"


def test_regime_labels_trend_and_volatility():
    baseline = [0.001 * ((i % 3) - 1) for i in range(40)]
    up_trend = baseline + [0.01] * 20
    regime = regime_label(up_trend, window=20)
    assert regime["trend"] == "up"
    down_trend = baseline + [-0.01] * 20
    assert regime_label(down_trend, window=20)["trend"] == "down"
    with pytest.raises(ValueError):
        regime_label([0.01] * 10, window=20)


def test_explain_move_structure_and_honesty():
    target = [0.001 * math.sin(i) for i in range(39)] + [0.05]
    context = {
        "BBB": [0.0008 * math.sin(i) for i in range(39)] + [0.04],
        "CCC": [-0.001 * math.sin(i) for i in range(39)] + [-0.03],
    }
    result = explain_move(target, context, window=20)
    assert result["direction"] == "up"
    assert result["hypothesis"] is True
    assert result["causality"] == "not_established"
    assert result["live_execution"] is False
    assert result["breadth_compared"] == 2
    assert "regime" in result
    assert result["magnitude_z_score"] != 0.0
