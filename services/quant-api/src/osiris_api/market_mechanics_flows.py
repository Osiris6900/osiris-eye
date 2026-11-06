"""Caller-supplied flow scenarios and historical market-mechanics diagnostics.

These endpoints neither discover live institutional positions nor predict prices.
Rates, weights, volatility and leverage use fractions/multiples, never percentages.
"""
from __future__ import annotations

import itertools
from datetime import UTC, datetime
from math import fsum, isfinite, sqrt
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .market_mechanics_validation import MechanicsRoute

router = APIRouter(prefix="/research/market-mechanics", tags=["market-mechanics"],
                   route_class=MechanicsRoute)
Amount = Annotated[float, Field(ge=0, le=1e30)]
Positive = Annotated[float, Field(gt=0, le=1e30)]
Fraction = Annotated[float, Field(ge=0, le=1)]
Rate = Annotated[float, Field(ge=-1, le=1)]


class Scenario(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", allow_inf_nan=False)


class LiquidationCluster(Scenario):
    side: Literal["long", "short"]
    liquidation_price: Positive
    notional_usd: Amount


class FundingLiquidationsIn(Scenario):
    spot: Positive
    funding_rate: Rate
    funding_interval_hours: Annotated[float, Field(gt=0, le=168)]
    position_notional_usd: Amount = 0.0
    clusters: list[LiquidationCluster] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def validate_sides(self):
        for cluster in self.clusters:
            if cluster.side == "long" and cluster.liquidation_price > self.spot:
                raise ValueError("long liquidation prices must be at or below spot")
            if cluster.side == "short" and cluster.liquidation_price < self.spot:
                raise ValueError("short liquidation prices must be at or above spot")
        return self


class TreasuryBalances(Scenario):
    fed_assets_billions: Amount
    tga_billions: Amount
    reverse_repo_billions: Amount


class TreasuryRepoIn(Scenario):
    previous: TreasuryBalances
    current: TreasuryBalances
    repo_rate: Rate
    reference_rate: Rate


class IndexConstituent(Scenario):
    symbol: str = Field(min_length=1, max_length=40)
    current_weight: Fraction
    target_weight: Fraction
    price: Positive
    adv_shares: Amount

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value):
        if not value.strip():
            raise ValueError("symbol must not be blank")
        return value.strip().upper()


class IndexRebalancingIn(Scenario):
    tracking_aum: Positive
    currency: str = Field(min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")
    constituents: list[IndexConstituent] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def unique_symbols(self):
        symbols = [row.symbol for row in self.constituents]
        if len(set(symbols)) != len(symbols):
            raise ValueError("constituent symbols must be unique")
        if (fsum(row.current_weight for row in self.constituents) > 1 + 1e-9
                or fsum(row.target_weight for row in self.constituents) > 1 + 1e-9):
            raise ValueError("current and target subset weights must each total at most one")
        return self


class VolatilityControlIn(Scenario):
    aum: Positive
    currency: str = Field(min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")
    target_annual_volatility: Annotated[float, Field(gt=0, le=10)]
    observed_annual_volatility: Annotated[float, Field(gt=0, le=10)]
    max_leverage: Annotated[float, Field(gt=0, le=100)] = 1.0
    current_leverage: Annotated[float, Field(ge=0, le=100)]


def _timestamp(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("timestamps must be timezone-aware ISO 8601 values") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("timestamps must include a timezone")
    return result.astimezone(UTC)


class TimedReturn(Scenario):
    timestamp: str = Field(min_length=1, max_length=40)
    return_fraction: Annotated[float, Field(ge=-1, le=100)]

    @field_validator("timestamp")
    @classmethod
    def valid_timestamp(cls, value):
        _timestamp(value)
        return value


class LeadLagIn(Scenario):
    source_symbol: str = Field(min_length=1, max_length=40, pattern=r"\S")
    target_symbol: str = Field(min_length=1, max_length=40, pattern=r"\S")
    source: list[TimedReturn] = Field(min_length=6, max_length=2000)
    target: list[TimedReturn] = Field(min_length=6, max_length=2000)
    max_lag: int = Field(default=1, ge=0, le=20)

    @model_validator(mode="after")
    def aligned_chronology(self):
        if self.source_symbol.strip().upper() == self.target_symbol.strip().upper():
            raise ValueError("source and target must be distinct assets")
        source_times = [_timestamp(row.timestamp) for row in self.source]
        target_times = [_timestamp(row.timestamp) for row in self.target]
        if source_times != target_times:
            raise ValueError("source and target must have identical aligned timestamps")
        if any(a >= b for a, b in itertools.pairwise(source_times)):
            raise ValueError("timestamps must be unique and strictly chronological")
        if len(self.source) - self.max_lag < 3:
            raise ValueError("each scanned lag requires at least three aligned pairs")
        # Lags are observation counts; equal spacing prevents an irregular row lag
        # being presented as a consistent time interval.
        spacings = [(b - a).total_seconds() for a, b in itertools.pairwise(source_times)]
        if any(delta != spacings[0] for delta in spacings[1:]):
            raise ValueError("lead-lag observations must be equally spaced")
        return self


CATALOG = [
    {
        "id": "mechanics_funding_liquidations",
        "name": "Funding Rate & Liquidation Clusters",
        "endpoint": "/research/market-mechanics/funding-liquidations",
        "evidence_strength": "strong_mechanical_basis",
        "mechanism": "Leveraged positions can generate forced flows at liquidation thresholds.",
        "limitations": [
            "Clusters are caller-supplied scenarios, not observed exchange liquidation inventory.",
            "Positive funding means longs pay shorts; rate is per supplied interval.",
            "Funding and cluster proximity alone do not predict price direction or cascades.",
        ],
        "sample_input": {
            "spot": 100.0, "funding_rate": 0.0001, "funding_interval_hours": 8.0,
            "position_notional_usd": 10000.0,
            "clusters": [
                {"side": "long", "liquidation_price": 95.0, "notional_usd": 1000000.0},
                {"side": "short", "liquidation_price": 105.0, "notional_usd": 500000.0},
            ],
        },
    },
    {
        "id": "mechanics_treasury_repo",
        "name": "Treasury Liquidity & Repo Market",
        "endpoint": "/research/market-mechanics/treasury-repo",
        "evidence_strength": "important_complex",
        "mechanism": "Treasury cash balances and secured funding affect financial conditions.",
        "limitations": [
            "Fed assets minus TGA minus reverse repo is a simplified proxy, not measured reserves.",
            "Balance observations must share publication cutoffs and USD billions units.",
            "Repo/reference spreads require comparable tenor and collateral; no causal price forecast.",
        ],
        "sample_input": {
            "previous": {"fed_assets_billions": 7000.0, "tga_billions": 800.0,
                         "reverse_repo_billions": 200.0},
            "current": {"fed_assets_billions": 6980.0, "tga_billions": 750.0,
                        "reverse_repo_billions": 180.0},
            "repo_rate": 0.045, "reference_rate": 0.043,
        },
    },
    {
        "id": "mechanics_index_rebalancing",
        "name": "Index Rebalancing",
        "endpoint": "/research/market-mechanics/index-rebalancing",
        "evidence_strength": "well_documented",
        "mechanism": "Index weight changes create mechanical passive tracking demand.",
        "limitations": [
            "Tracking AUM and weights are supplied assumptions; holdings may already anticipate flows.",
            "All prices and AUM must use the declared common currency; ADV is shares per day.",
            "A subset of constituents is allowed; flows omit cash, derivatives and implementation timing.",
        ],
        "sample_input": {
            "tracking_aum": 1000000000.0, "currency": "USD",
            "constituents": [
                {"symbol": "ABC", "current_weight": 0.01, "target_weight": 0.015,
                 "price": 100.0, "adv_shares": 1000000.0},
            ],
        },
    },
    {
        "id": "mechanics_volatility_control",
        "name": "Volatility-Control Funds",
        "endpoint": "/research/market-mechanics/volatility-control",
        "evidence_strength": "real_conditional",
        "mechanism": "A volatility target can require lower exposure as measured volatility rises.",
        "limitations": [
            "A single-asset target-volatility rule is an illustrative fund scenario, not actual holdings.",
            "Volatility inputs must use the same annualization; smoothing and rebalance delays are omitted.",
            "Exposure change depends on supplied current leverage and does not predict market direction.",
        ],
        "sample_input": {
            "aum": 100000000.0, "currency": "USD", "target_annual_volatility": 0.1,
            "observed_annual_volatility": 0.2, "max_leverage": 1.0, "current_leverage": 1.0,
        },
    },
    {
        "id": "mechanics_cross_asset_lead_lag",
        "name": "Cross-Asset Lead-Lag",
        "endpoint": "/research/market-mechanics/cross-asset-lead-lag",
        "evidence_strength": "present_unstable",
        "mechanism": "Information transmission can create historical cross-market lead-lag dependence.",
        "limitations": [
            "Historical correlations are exploratory and unstable; they do not establish causality.",
            "Use point-in-time available observations, compatible calendars and publication cutoffs.",
            "Lag scans incur multiple testing; validate any hypothesis on a separate future holdout.",
        ],
        "sample_input": {
            "source_symbol": "SOURCE", "target_symbol": "TARGET", "max_lag": 1,
            "source": [
                {"timestamp": f"2026-01-0{i + 1}T00:00:00Z", "return_fraction": value}
                for i, value in enumerate([0.01, -0.02, 0.03, 0.0, 0.02, -0.01, 0.04, -0.03])
            ],
            "target": [
                {"timestamp": f"2026-01-0{i + 1}T00:00:00Z", "return_fraction": value}
                for i, value in enumerate([0.0, 0.01, -0.02, 0.03, 0.0, 0.02, -0.01, 0.04])
            ],
        },
    },
]


def _response(index: int, **values):
    """Guard arithmetic results too: finite inputs can still overflow ratios."""
    def finite_tree(value):
        if isinstance(value, float) and not isfinite(value):
            raise HTTPException(422, "scenario arithmetic exceeds finite numeric range")
        if isinstance(value, dict):
            for item in value.values():
                finite_tree(item)
        elif isinstance(value, list):
            for item in value:
                finite_tree(item)

    finite_tree(values)
    return {"research_only": True, "execution_enabled": False,
            "evidence_strength": CATALOG[index]["evidence_strength"],
            "limitations": CATALOG[index]["limitations"], **values}


@router.post("/funding-liquidations")
def funding_liquidations(data: FundingLiquidationsIn):
    rows = [{"side": row.side, "liquidation_price": row.liquidation_price,
             "scenario_notional_usd": row.notional_usd,
             "signed_distance_fraction": row.liquidation_price / data.spot - 1,
             "forced_flow_if_triggered": "sell" if row.side == "long" else "buy"}
            for row in data.clusters]
    return _response(
        0, funding_interval_hours=data.funding_interval_hours,
        funding_rate_per_interval=data.funding_rate,
        long_funding_payment_usd_per_interval=data.position_notional_usd * data.funding_rate,
        short_funding_payment_usd_per_interval=-data.position_notional_usd * data.funding_rate,
        funding_payer="longs" if data.funding_rate > 0 else "shorts" if data.funding_rate < 0 else "none",
        clusters=rows,
        long_scenario_notional_usd=fsum(r.notional_usd for r in data.clusters if r.side == "long"),
        short_scenario_notional_usd=fsum(r.notional_usd for r in data.clusters if r.side == "short"),
        live_liquidation_inventory_connected=False,
    )


@router.post("/treasury-repo")
def treasury_repo(data: TreasuryRepoIn):
    def proxy(balance):
        return balance.fed_assets_billions - balance.tga_billions - balance.reverse_repo_billions
    before, after = proxy(data.previous), proxy(data.current)
    return _response(
        1, previous_liquidity_proxy_usd_billions=before,
        current_liquidity_proxy_usd_billions=after,
        liquidity_proxy_change_usd_billions=after - before,
        repo_reference_spread_bps=(data.repo_rate - data.reference_rate) * 10000,
        measured_bank_reserves=False, price_direction_forecast=None,
    )


@router.post("/index-rebalancing")
def index_rebalancing(data: IndexRebalancingIn):
    rows = []
    for security in data.constituents:
        notional = data.tracking_aum * (security.target_weight - security.current_weight)
        shares = notional / security.price
        rows.append({"symbol": security.symbol, "estimated_flow_notional": notional,
                     "estimated_flow_shares": shares,
                     "adv_participation_fraction": abs(shares) / security.adv_shares
                     if security.adv_shares else None,
                     "adv_available": security.adv_shares > 0,
                     "flow_side": "buy" if notional > 0 else "sell" if notional < 0 else "unchanged"})
    return _response(2, currency=data.currency, constituents=rows,
                     net_estimated_flow_notional=fsum(row["estimated_flow_notional"] for row in rows),
                     gross_estimated_flow_notional=fsum(abs(row["estimated_flow_notional"]) for row in rows),
                     constituent_subset_allowed=True)


@router.post("/volatility-control")
def volatility_control(data: VolatilityControlIn):
    # Avoid overflowing target/observed when a subnormal observed vol is supplied.
    capped = data.target_annual_volatility >= data.max_leverage * data.observed_annual_volatility
    target = data.max_leverage if capped else data.target_annual_volatility / data.observed_annual_volatility
    change = target - data.current_leverage
    return _response(3, currency=data.currency, target_leverage=target,
                     leverage_cap_binding=capped,
                     current_exposure_notional=data.aum * data.current_leverage,
                     target_exposure_notional=data.aum * target,
                     estimated_rebalance_notional=data.aum * change,
                     rebalance_side="buy" if change > 0 else "sell" if change < 0 else "unchanged")


def _correlation(x, y):
    if len(x) < 3:
        return None
    mx, my = fsum(x) / len(x), fsum(y) / len(y)
    dx, dy = [value - mx for value in x], [value - my for value in y]
    # Separate square roots also avoid a needless product underflow.
    sx, sy = sqrt(fsum(value * value for value in dx)), sqrt(fsum(value * value for value in dy))
    if sx == 0 or sy == 0:
        return None
    return max(-1.0, min(1.0, fsum((a / sx) * (b / sy) for a, b in zip(dx, dy))))


@router.post("/cross-asset-lead-lag")
def cross_asset_lead_lag(data: LeadLagIn):
    source, target = [r.return_fraction for r in data.source], [r.return_fraction for r in data.target]
    relationships = []
    for lag in range(-data.max_lag, data.max_lag + 1):
        x, y = source, target
        if lag > 0:
            x, y = source[:-lag], target[lag:]
        elif lag < 0:
            x, y = source[-lag:], target[:lag]
        midpoint = len(x) // 2
        first, second = _correlation(x[:midpoint], y[:midpoint]), _correlation(x[midpoint:], y[midpoint:])
        corr = _correlation(x, y)
        relationships.append({
            "lag_observations": lag, "paired_observations": len(x), "correlation": corr,
            "correlation_status": "available" if corr is not None else "undefined_constant_series",
            "first_half_correlation": first, "second_half_correlation": second,
            "half_sample_correlation_change": second - first if first is not None and second is not None else None,
        })
    return _response(4, source_symbol=data.source_symbol, target_symbol=data.target_symbol,
                     positive_lag_meaning="source leads target", relationships=relationships,
                     hypothesis_only=True, holdout_validation_performed=False)
