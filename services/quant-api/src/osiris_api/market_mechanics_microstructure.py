"""Caller-supplied market-mechanics scenarios; no price forecasts or order routing.

All contracts in an options request must refer to one underlying, currency and
expiry. The caller remains responsible for provenance and point-in-time alignment.
"""
from __future__ import annotations

import itertools
from datetime import date
from math import isfinite
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .market_mechanics_validation import MechanicsRoute

router = APIRouter(prefix="/research/market-mechanics", tags=["market-mechanics"],
                   route_class=MechanicsRoute)

Nonnegative = Annotated[float, Field(ge=0, le=1e15)]
Positive = Annotated[float, Field(gt=0, le=1e15)]
Fraction = Annotated[float, Field(ge=0, le=1)]


class MechanicsInput(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", allow_inf_nan=False)


class DealerOption(MechanicsInput):
    strike: Positive
    option_type: Literal["call", "put"]
    gamma_per_underlying_unit: Nonnegative
    dealer_contracts: Annotated[float, Field(ge=-1e15, le=1e15)]
    contract_multiplier: Positive


class DealerGexInput(MechanicsInput):
    underlying: str = Field(min_length=1, max_length=80, pattern=r"\S")
    expiry: str
    spot: Positive
    options: list[DealerOption] = Field(min_length=1, max_length=1000)

    @field_validator("expiry")
    @classmethod
    def valid_expiry(cls, value: str) -> str:
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError("expiry must be YYYY-MM-DD")
        return value

    @model_validator(mode="after")
    def unique_contracts(self):
        keys = [(row.strike, row.option_type) for row in self.options]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate strike/option_type; aggregate positions first")
        return self


class ExpiryStrike(MechanicsInput):
    strike: Positive
    call_open_interest: Nonnegative
    put_open_interest: Nonnegative


class ExpiryInput(MechanicsInput):
    underlying: str = Field(min_length=1, max_length=80, pattern=r"\S")
    expiry: str
    spot: Positive
    hours_to_expiry: Annotated[float, Field(ge=0, le=87600)]
    contract_multiplier: Positive
    chain: list[ExpiryStrike] = Field(min_length=1, max_length=500)

    _valid_expiry = field_validator("expiry")(DealerGexInput.valid_expiry.__func__)

    @model_validator(mode="after")
    def unique_strikes(self):
        strikes = [row.strike for row in self.chain]
        if len(strikes) != len(set(strikes)):
            raise ValueError("duplicate strikes; aggregate one expiry first")
        return self


class BookLevel(MechanicsInput):
    price: Positive
    quantity: Positive


class LiquidityInput(MechanicsInput):
    side: Literal["buy", "sell"]
    order_quantity: Positive
    bids: list[BookLevel] = Field(min_length=1, max_length=1000)
    asks: list[BookLevel] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def valid_book(self):
        bid_prices = [row.price for row in self.bids]
        ask_prices = [row.price for row in self.asks]
        if len(set(bid_prices)) != len(bid_prices) or len(set(ask_prices)) != len(ask_prices):
            raise ValueError("duplicate price levels; aggregate depth first")
        if max(bid_prices) >= min(ask_prices):
            raise ValueError("book must be uncrossed with a positive spread")
        return self


class AggressorTrade(MechanicsInput):
    aggressor_side: Literal["buy", "sell", "unknown"]
    quantity: Positive


class OrderFlowInput(MechanicsInput):
    trades: list[AggressorTrade] = Field(min_length=1, max_length=1000)


class DarkPoolPrint(MechanicsInput):
    price: Positive
    quantity: Positive


class DarkPoolInput(MechanicsInput):
    dark_pool_prints: list[DarkPoolPrint] = Field(max_length=1000)
    consolidated_volume: Positive
    reference_price: Positive

    @model_validator(mode="after")
    def valid_share(self):
        if sum(row.quantity for row in self.dark_pool_prints) > self.consolidated_volume:
            raise ValueError("dark pool volume exceeds matching consolidated volume")
        return self


CATALOG = [
    {
        "id": "mechanics_dealer_gex", "name": "Dealer Gamma Exposure (GEX)",
        "endpoint": "/research/market-mechanics/dealer-gex",
        "evidence_strength": "strong_mechanical_basis",
        "mechanism": "Dealer delta hedging can dampen moves when long gamma or amplify them when short gamma.",
        "limitations": ["Signed dealer inventory must be supplied; open interest alone cannot identify dealer positioning.",
                        "Local constant-gamma scenario only; realized hedging, jumps and pinning are not predicted."],
        "sample_input": {"underlying": "DEMO", "expiry": "2026-12-18", "spot": 100,
                         "options": [{"strike": 100, "option_type": "call", "gamma_per_underlying_unit": 0.02,
                                      "dealer_contracts": -10, "contract_multiplier": 100}]},
    },
    {
        "id": "mechanics_expiry_max_pain", "name": "Options Expiry & Max Pain",
        "endpoint": "/research/market-mechanics/expiry-max-pain",
        "evidence_strength": "mixed_expiry_effects_weak_max_pain",
        "mechanism": "Expiry changes hedging and settlement incentives; max pain minimizes supplied intrinsic open-interest payouts.",
        "limitations": ["Max pain has weak predictive evidence; it is not a price target or a pinning forecast.",
                        "One underlying, expiry, currency and common multiplier; premiums and dealer inventory are excluded."],
        "sample_input": {"underlying": "DEMO", "expiry": "2026-12-18", "spot": 101, "hours_to_expiry": 6,
                         "contract_multiplier": 100, "chain": [
                             {"strike": 95, "call_open_interest": 10, "put_open_interest": 0},
                             {"strike": 100, "call_open_interest": 5, "put_open_interest": 5},
                             {"strike": 105, "call_open_interest": 0, "put_open_interest": 10}]},
    },
    {
        "id": "mechanics_liquidity_vacuum", "name": "Liquidity Vacuum",
        "endpoint": "/research/market-mechanics/liquidity-vacuum",
        "evidence_strength": "strong_mechanical_basis",
        "mechanism": "A marketable order consumes resting depth and can cross price gaps when displayed liquidity is thin.",
        "limitations": ["Static displayed-book sweep; cancellations, hidden liquidity, replenishment and latency are excluded.",
                        "Unfilled quantity is reported without extrapolating prices beyond supplied depth."],
        "sample_input": {"side": "buy", "order_quantity": 15, "bids": [{"price": 99, "quantity": 20}],
                         "asks": [{"price": 101, "quantity": 5}, {"price": 105, "quantity": 5}]},
    },
    {
        "id": "mechanics_order_flow_imbalance", "name": "Order Flow Imbalance",
        "endpoint": "/research/market-mechanics/order-flow-imbalance",
        "evidence_strength": "strong_short_horizon_evidence",
        "mechanism": "Buyer- versus seller-initiated executed volume measures short-horizon aggressive trading pressure.",
        "limitations": ["This is executed trade-flow imbalance, distinct from displayed bid/ask depth or order-event OFI.",
                        "Aggressor classification can be wrong; unknown volume remains unclassified and pressure is not a forecast."],
        "sample_input": {"trades": [{"aggressor_side": "buy", "quantity": 75},
                                    {"aggressor_side": "sell", "quantity": 25},
                                    {"aggressor_side": "unknown", "quantity": 20}]},
    },
    {
        "id": "mechanics_dark_pool", "name": "Dark Pool Trading",
        "endpoint": "/research/market-mechanics/dark-pool",
        "evidence_strength": "real_activity_ambiguous_direction",
        "mechanism": "Off-exchange executions reveal trading activity and concentration, while every print has a buyer and seller.",
        "limitations": ["Prices and volume do not identify institutional intent, aggressor side or future direction.",
                        "Use venue-identified dark pool prints and consolidated volume from the same window; off-exchange is not synonymous with dark pool."],
        "sample_input": {"dark_pool_prints": [{"price": 100, "quantity": 100}, {"price": 102, "quantity": 50}],
                         "consolidated_volume": 1000, "reference_price": 100},
    },
]


def _result(index: int, **metrics) -> dict:
    def finite_tree(value):
        if isinstance(value, float) and not isfinite(value):
            raise HTTPException(422, "calculation exceeds finite numerical range")
        if isinstance(value, dict):
            for item in value.values():
                finite_tree(item)
        elif isinstance(value, list):
            for item in value:
                finite_tree(item)

    finite_tree(metrics)
    entry = CATALOG[index]
    return {"research_only": True, "execution_enabled": False,
            "evidence_strength": entry["evidence_strength"],
            "limitations": entry["limitations"], **metrics}


@router.post("/dealer-gex")
def dealer_gex(payload: DealerGexInput):
    """Dollar change in dealer delta notional for a +1% underlying move."""
    by_strike: dict[float, float] = {}
    exposures = []
    for row in payload.options:
        exposure = (row.gamma_per_underlying_unit * row.dealer_contracts
                    * row.contract_multiplier * payload.spot ** 2 * 0.01)
        exposures.append(exposure)
        by_strike[row.strike] = by_strike.get(row.strike, 0.0) + exposure
    net = sum(exposures)
    return _result(0, underlying=payload.underlying, expiry=payload.expiry,
                   net_gex_per_1pct_move=net, gross_gex_per_1pct_move=sum(abs(x) for x in exposures),
                   hedge_notional_change_per_1pct_move=-net,
                   gamma_regime="long_gamma_dampening" if net > 0 else "short_gamma_amplifying" if net < 0 else "neutral",
                   by_strike=[{"strike": strike, "gex_per_1pct_move": value}
                              for strike, value in sorted(by_strike.items())],
                   pinning_forecast=None)


@router.post("/expiry-max-pain")
def expiry_max_pain(payload: ExpiryInput):
    payouts = [{"settlement_price": candidate.strike,
                "intrinsic_payout": sum((max(candidate.strike - row.strike, 0) * row.call_open_interest
                                         + max(row.strike - candidate.strike, 0) * row.put_open_interest)
                                        * payload.contract_multiplier for row in payload.chain)}
               for candidate in sorted(payload.chain, key=lambda row: row.strike)]
    total_oi = sum(row.call_open_interest + row.put_open_interest for row in payload.chain)
    minimum = min(item["intrinsic_payout"] for item in payouts)
    candidates = ([item["settlement_price"] for item in payouts if item["intrinsic_payout"] == minimum]
                  if total_oi else [])
    return _result(1, underlying=payload.underlying, expiry=payload.expiry,
                   hours_to_expiry=payload.hours_to_expiry, total_open_interest=total_oi,
                   max_pain_candidates=candidates,
                   minimum_intrinsic_payout=minimum if total_oi else None,
                   settlement_payouts=payouts, price_target=None,
                   information_available=total_oi > 0)


@router.post("/liquidity-vacuum")
def liquidity_vacuum(payload: LiquidityInput):
    buying = payload.side == "buy"
    levels = sorted(payload.asks if buying else payload.bids,
                    key=lambda row: row.price, reverse=not buying)
    best = levels[0].price
    remaining, notional = payload.order_quantity, 0.0
    fills = []
    for row in levels:
        quantity = min(remaining, row.quantity)
        if quantity <= 0:
            break
        fills.append({"price": row.price, "quantity": quantity})
        notional += row.price * quantity
        remaining -= quantity
    filled = sum(row["quantity"] for row in fills)
    vwap = notional / filled
    sign = 1 if buying else -1
    gaps_bps = [abs(right.price - left.price) / best * 10000
                for left, right in itertools.pairwise(levels)]
    return _result(2, side=payload.side, filled_quantity=filled, unfilled_quantity=remaining,
                   fill_fraction=filled / payload.order_quantity, filled_vwap=vwap,
                   slippage_from_best_bps=sign * (vwap / best - 1) * 10000,
                   worst_fill_price=fills[-1]["price"],
                   max_visible_gap_bps=max(gaps_bps, default=0),
                   depth_exhausted=remaining > 0, fills=fills)


@router.post("/order-flow-imbalance")
def executed_order_flow(payload: OrderFlowInput):
    volumes = {side: sum(row.quantity for row in payload.trades if row.aggressor_side == side)
               for side in ("buy", "sell", "unknown")}
    classified = volumes["buy"] + volumes["sell"]
    imbalance = (volumes["buy"] - volumes["sell"]) / classified if classified else None
    return _result(3, buy_volume=volumes["buy"], sell_volume=volumes["sell"],
                   unknown_volume=volumes["unknown"],
                   classified_fraction=classified / sum(volumes.values()), imbalance=imbalance,
                   pressure="unclassified" if imbalance is None else "buy" if imbalance > 0 else "sell" if imbalance < 0 else "balanced",
                   predicted_return=None)


@router.post("/dark-pool")
def dark_pool_activity(payload: DarkPoolInput):
    volume = sum(row.quantity for row in payload.dark_pool_prints)
    notional = sum(row.quantity * row.price for row in payload.dark_pool_prints)
    vwap = notional / volume if volume else None
    return _result(4, dark_pool_volume=volume, dark_pool_notional=notional,
                   consolidated_volume_share=volume / payload.consolidated_volume,
                   dark_pool_vwap=vwap,
                   vwap_vs_reference_bps=(vwap / payload.reference_price - 1) * 10000 if vwap is not None else None,
                   largest_print_volume_share=max((row.quantity for row in payload.dark_pool_prints), default=0) / volume if volume else None,
                   inferred_direction="unknown", institutional_intent="unknown")
