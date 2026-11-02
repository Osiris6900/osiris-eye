import pytest
from osiris_api.research import relative_iv_zscore,estimated_one_way_cost,expiry_gamma_feature,implied_minus_forecast_vol,lagged_correlation,market_regime

def test_iv_zscore(): assert relative_iv_zscore(.3,[.1,.2,.3])==pytest.approx(1.22474487)
def test_zero_dispersion(): assert relative_iv_zscore(.2,[.2,.2])==0
def test_liquidity(): assert estimated_one_way_cost(.02,.01)==pytest.approx(.03)
def test_gamma(): assert expiry_gamma_feature(-4,4)==2
def test_vol_spread(): assert implied_minus_forecast_vol(.25,.2)==pytest.approx(.05)
def test_lag_corr(): assert lagged_correlation([1,2,3,4],[2,3,4,5])==pytest.approx(1)
def test_regime(): assert market_regime(.4,4)["risk_restricted"] is True
def test_reject_invalid():
    with pytest.raises(ValueError): relative_iv_zscore(-.1,[.2,.3])