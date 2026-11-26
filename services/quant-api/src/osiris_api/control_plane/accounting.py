"""Read-only attribution and TCA from the canonical paper execution database."""

from __future__ import annotations

import sqlite3
from decimal import Decimal
from pathlib import Path
from typing import Any

from .database import Database, identifier

_ZERO = Decimal(0)
_BPS = Decimal(10000)


def _read_canonical(canonical_ledger_path: str):
    """Return one verified snapshot; never create or repair the execution ledger."""
    from ..paper_execution import _reconcile_breaks, _verify_audit

    path = Path(canonical_ledger_path).resolve()
    if not path.is_file():
        raise ValueError("canonical_ledger_unavailable")
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("BEGIN")
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise ValueError("canonical_ledger_corrupt")
        _verify_audit(connection)
        if _reconcile_breaks(connection):
            raise ValueError("canonical_ledger_does_not_reconcile")
        # Read exactly the persisted fills; never synthesize fills from requested orders.
        fills = connection.execute("SELECT * FROM paper_exec_fills ORDER BY seq").fetchall()
        orders = {
            row["client_order_id"]: row
            for row in connection.execute("SELECT * FROM paper_exec_orders")
        }
        account = connection.execute("SELECT * FROM paper_exec_meta LIMIT 1").fetchone()
        return fills, orders, account
    finally:
        connection.close()


def _new_cost_bucket() -> dict[str, Any]:
    return {
        "order_ids": set(),
        "fill_count": 0,
        "filled_quantity": _ZERO,
        "fill_notional": _ZERO,
        "reference_notional": _ZERO,
        "fees": _ZERO,
        "slippage": _ZERO,
        "executed_shortfall": _ZERO,
        "spread_impact_fill_count": 0,
        "quote_slippage": _ZERO,
        "spread_cost": _ZERO,
        "impact_cost": _ZERO,
    }


def _accumulate_cost(
    bucket: dict[str, Any],
    fill: sqlite3.Row,
    reference: Decimal,
    spread: Decimal | None,
    impact: Decimal | None,
) -> None:
    quantity = Decimal(fill["quantity"])
    price = Decimal(fill["price"])
    fee = Decimal(fill["fee"])
    if fill["side"] == "BUY":
        slippage = (price - reference) * quantity
    elif fill["side"] == "SELL":
        slippage = (reference - price) * quantity
    else:
        raise ValueError("canonical_fill_side_invalid")
    bucket["order_ids"].add(fill["client_order_id"])
    bucket["fill_count"] += 1
    bucket["filled_quantity"] += quantity
    bucket["fill_notional"] += quantity * price
    bucket["reference_notional"] += quantity * reference
    bucket["fees"] += fee
    bucket["slippage"] += slippage
    bucket["executed_shortfall"] += slippage + fee
    if spread is not None and impact is not None:
        bucket["spread_impact_fill_count"] += 1
        bucket["quote_slippage"] += slippage
        bucket["spread_cost"] += spread
        bucket["impact_cost"] += impact


def _public_cost_bucket(bucket: dict[str, Any]) -> dict[str, Any]:
    reference_notional = bucket["reference_notional"]
    has_quote_decomposition = bucket["spread_impact_fill_count"] > 0
    return {
        "order_count": len(bucket["order_ids"]),
        "fill_count": bucket["fill_count"],
        "filled_quantity": str(bucket["filled_quantity"]),
        "average_fill_price": str(bucket["fill_notional"] / bucket["filled_quantity"]),
        "reference_notional": str(reference_notional),
        "fees": str(bucket["fees"]),
        "slippage": str(bucket["slippage"]),
        "executed_shortfall": str(bucket["executed_shortfall"]),
        "executed_shortfall_bps": str(
            bucket["executed_shortfall"] / reference_notional * _BPS
        ),
        "spread_impact_fill_count": bucket["spread_impact_fill_count"],
        "quote_slippage": str(bucket["quote_slippage"]) if has_quote_decomposition else None,
        "spread_cost": str(bucket["spread_cost"]) if has_quote_decomposition else None,
        "impact_cost": str(bucket["impact_cost"]) if has_quote_decomposition else None,
    }


class AttributionService:
    def __init__(self, db: Database):
        self.db = db

    def tag_order(
        self,
        client_order_id: str,
        strategy: str,
        reference_price: str,
        *,
        arrival_bid: str | None = None,
        arrival_ask: str | None = None,
    ):
        """Bind an immutable strategy and arrival benchmark to a paper order.

        When an arrival quote is supplied, the reference must be its midpoint. This
        makes spread plus impact reconcile exactly to slippage for both sides.
        """
        from .broker import money

        reference = money(reference_price)
        value = {"strategy": identifier(strategy), "reference_price": str(reference)}
        if (arrival_bid is None) != (arrival_ask is None):
            raise ValueError("arrival_quote_pair_required")
        if arrival_bid is not None and arrival_ask is not None:
            bid, ask = money(arrival_bid), money(arrival_ask)
            if bid >= ask:
                raise ValueError("arrival_quote_crossed_or_locked")
            if reference != (bid + ask) / 2:
                raise ValueError("reference_price_must_equal_arrival_midpoint")
            value.update({"arrival_bid": str(bid), "arrival_ask": str(ask)})
        key = identifier(client_order_id)
        old = self.db.get("order-strategies", key)
        if old:
            if old["value"] != value:
                raise ValueError("order_strategy_is_immutable")
            return
        self.db.put("order-strategies", key, value)

    def report(self, canonical_ledger_path: str) -> dict:
        fills, _, account = _read_canonical(canonical_ledger_path)
        groups, total = {}, Decimal(0)
        for fill in fills:
            tag = self.db.get("order-strategies", fill["client_order_id"])
            strategy = tag["value"]["strategy"] if tag else "unattributed"
            row = groups.setdefault(
                strategy,
                {
                    "realized_pnl": Decimal(0),
                    "fees": Decimal(0),
                    "slippage": Decimal(0),
                    "fills": 0,
                },
            )
            pnl, fee = Decimal(fill["realized_pnl"]), Decimal(fill["fee"])
            row["realized_pnl"] += pnl
            row["fees"] += fee
            row["fills"] += 1
            total += pnl
            if tag:
                direction = Decimal(1) if fill["side"] == "BUY" else Decimal(-1)
                row["slippage"] += (
                    direction
                    * (Decimal(fill["price"]) - Decimal(tag["value"]["reference_price"]))
                    * Decimal(fill["quantity"])
                )
        if account is None or total != Decimal(account["realized_pnl"]):
            raise ValueError("canonical_pnl_does_not_reconcile")
        return {
            "source": "canonical-paper-execution-ledger",
            "fill_count": len(fills),
            "realized_pnl": str(total),
            "strategies": {
                key: {
                    field: str(value) if isinstance(value, Decimal) else value
                    for field, value in row.items()
                }
                for key, row in sorted(groups.items())
            },
            "unrealized_pnl_attributed": False,
            "slippage_is_diagnostic_not_additional_pnl": True,
            "fees_already_included_in_ledger_pnl": True,
            "live_execution": False,
        }

    def transaction_costs(self, canonical_ledger_path: str) -> dict:
        """Executed-quantity TCA; missing benchmarks are excluded, never zero-filled."""
        fills, orders, account = _read_canonical(canonical_ledger_path)
        if account is None:
            raise ValueError("canonical_pnl_does_not_reconcile")
        by_order: dict[str, dict[str, Any]] = {}
        by_instrument: dict[str, dict[str, Any]] = {}
        by_strategy: dict[str, dict[str, Any]] = {}
        order_context: dict[str, dict[str, Any]] = {}
        missing_reference_fill_count = 0

        for fill in fills:
            tag = self.db.get("order-strategies", fill["client_order_id"])
            if tag is None:
                missing_reference_fill_count += 1
                continue
            value = tag["value"]
            reference = Decimal(value["reference_price"])
            quantity = Decimal(fill["quantity"])
            spread = impact = None
            if "arrival_bid" in value or "arrival_ask" in value:
                if "arrival_bid" not in value or "arrival_ask" not in value:
                    raise ValueError("stored_arrival_quote_incomplete")
                bid, ask = Decimal(value["arrival_bid"]), Decimal(value["arrival_ask"])
                midpoint = (bid + ask) / 2
                if bid >= ask or reference != midpoint:
                    raise ValueError("stored_arrival_quote_invalid")
                if fill["side"] == "BUY":
                    spread = (ask - midpoint) * quantity
                elif fill["side"] == "SELL":
                    spread = (midpoint - bid) * quantity
                else:
                    raise ValueError("canonical_fill_side_invalid")
                direction = Decimal(1) if fill["side"] == "BUY" else Decimal(-1)
                slippage = direction * (Decimal(fill["price"]) - reference) * quantity
                impact = slippage - spread

            order_id = fill["client_order_id"]
            order = orders.get(order_id)
            if order is None:
                raise ValueError("canonical_fill_order_missing")
            order_bucket = by_order.setdefault(order_id, _new_cost_bucket())
            instrument_bucket = by_instrument.setdefault(fill["symbol"], _new_cost_bucket())
            strategy_bucket = by_strategy.setdefault(value["strategy"], _new_cost_bucket())
            for bucket in (order_bucket, instrument_bucket, strategy_bucket):
                _accumulate_cost(bucket, fill, reference, spread, impact)
            order_context[order_id] = {
                "order": order,
                "strategy": value["strategy"],
                "reference_price": str(reference),
                "arrival_bid": value.get("arrival_bid"),
                "arrival_ask": value.get("arrival_ask"),
            }

        order_rows = []
        for order_id in sorted(by_order):
            context = order_context[order_id]
            order = context["order"]
            order_rows.append({
                "client_order_id": order_id,
                "symbol": order["symbol"],
                "side": order["side"],
                "strategy": context["strategy"],
                "status": order["status"],
                "state": order["state"],
                "requested_quantity": order["quantity"],
                "unfilled_quantity": order["leaves_quantity"],
                "partial_fill": Decimal(order["leaves_quantity"]) > 0,
                "reference_price": context["reference_price"],
                "arrival_bid": context["arrival_bid"],
                "arrival_ask": context["arrival_ask"],
                **_public_cost_bucket(by_order[order_id]),
            })

        analyzed_fill_count = len(fills) - missing_reference_fill_count
        return {
            "source": "canonical-paper-execution-ledger",
            "fill_count": len(fills),
            "analyzed_fill_count": analyzed_fill_count,
            "missing_reference_fill_count": missing_reference_fill_count,
            "reference_coverage_complete": missing_reference_fill_count == 0,
            "orders": order_rows,
            "instruments": {
                key: _public_cost_bucket(by_instrument[key]) for key in sorted(by_instrument)
            },
            "strategies": {
                key: _public_cost_bucket(by_strategy[key]) for key in sorted(by_strategy)
            },
            "cost_convention": "positive cost is adverse; negative slippage is price improvement",
            "spread_impact_convention": (
                "arrival midpoint benchmark; spread is arrival-touch cost and impact is residual"
            ),
            "executed_quantity_only": True,
            "unfilled_opportunity_cost_included": False,
            "slippage_is_diagnostic_not_additional_pnl": True,
            "fees_already_included_in_ledger_pnl": True,
            "benchmark_market_data_independently_verified": False,
            "production_calibrated": False,
            "live_execution": False,
        }
