"""Capital usage of the canonical paper book against its stored limits.

Gross exposure includes marked positions and resting buy orders. Inception
drawdown is the loss of equity from initial cash. The approved budgets are
the account limits. Caller strategy weights are ignored: paper orders have
no strategy tag, so a per-strategy budget is not invented.
"""
from __future__ import annotations

from decimal import Decimal
from fractions import Fraction
from typing import Any

from . import paper_execution as ledger


def paper_capital_usage(as_of: object) -> dict[str, Any]:
    when = ledger._parse_time(as_of)
    if when is None or not isinstance(as_of, str):
        raise ValueError("invalid_capital_time")
    report: dict[str, Any] = _blank("not_configured")
    try:
        ledger._database()
    except ValueError:
        return report
    with ledger._lock:
        conn = ledger._connect()
        try:
            conn.execute("BEGIN")
            account = conn.execute("SELECT * FROM paper_exec_meta WHERE id=1").fetchone()
            if account is None:
                report["reason"] = "account_not_initialized"
                return report
            try:
                ledger._verify_audit(conn)
            except ValueError:
                report["reason"] = "audit_chain_corrupt"
                return report
            if ledger._reconcile_breaks(conn):
                report["reason"] = "reconciliation_break"
                return report
            exact = ledger._closing_settlement_enabled(conn)
            positions = {
                row["symbol"]: (Decimal(row["quantity"]), Decimal(row["average_cost"]))
                for row in conn.execute(
                    "SELECT symbol, quantity, average_cost FROM paper_exec_positions"
                )
                if Decimal(row["quantity"]) != 0
            }
            marks = {
                row["symbol"]: row
                for row in conn.execute(
                    "SELECT symbol, price, observed_at FROM paper_exec_marks"
                )
            }
            open_buys: dict[str, Fraction] = {}
            for row in conn.execute(
                """SELECT symbol, leaves_quantity, price FROM paper_exec_orders
                   WHERE state IN ('accepted', 'partially_filled') AND side='BUY'"""
            ):
                notion = Fraction(Decimal(row["leaves_quantity"])) * Fraction(Decimal(row["price"]))
                open_buys[row["symbol"]] = open_buys.get(row["symbol"], Fraction(0)) + notion
            cash = Decimal(account["cash"])
            initial = Decimal(account["initial_cash"])
            realized = Decimal(account["realized_pnl"])
            recorded_unrealized = Decimal(account["unrealized_pnl"])
            exposure_limit = Decimal(account["max_gross_exposure"])
            position_limit = Decimal(account["max_position_notional"])
            loss_limit = Decimal(account["max_daily_loss"])
        finally:
            conn.close()
    if initial <= 0:
        report["reason"] = "invalid_capital_base"
        return report
    unrealized = Fraction(0)
    market_value = Fraction(0)
    marked_gross = Fraction(0)
    rows = []
    symbols = sorted(set(positions) | set(open_buys))
    for symbol in symbols:
        quantity, average = positions.get(symbol, (Decimal("0"), Decimal("0")))
        reserved = open_buys.get(symbol, Fraction(0))
        if quantity != 0:
            mark = marks.get(symbol)
            if mark is None or not ledger._fresh(account, mark["observed_at"], as_of):
                report.update(state="incomplete", reason="mark_required")
                return report
            price = Decimal(mark["price"])
            if exact:
                unrealized += Fraction(quantity) * (Fraction(price) - Fraction(average))
            else:
                unrealized += Fraction(quantity * (price - average))
            market_value += Fraction(quantity) * Fraction(price)
            marked_gross += abs(Fraction(quantity) * Fraction(price))
            inventory = abs(Fraction(quantity) * Fraction(price))
            mark_text = mark["price"]
        else:
            inventory = Fraction(0)
            mark_text = None
        used = inventory + reserved
        rows.append({
            "symbol": symbol,
            "quantity": ledger._canonical(quantity),
            "mark": mark_text,
            "inventory_notional": _money(inventory),
            "open_buy_notional": _money(reserved),
            "position_notional": _money(used),
            "position_limit_breached": used > Fraction(position_limit),
        })
    if not _matches(unrealized, account["unrealized_pnl"]):
        report["reason"] = "reconciliation_break"
        return report
    equity = Fraction(cash) + market_value
    expected_equity = Fraction(initial) + Fraction(realized) + Fraction(recorded_unrealized)
    if ledger._finite_decimal(equity) != ledger._finite_decimal(expected_equity):
        report["reason"] = "reconciliation_break"
        return report
    gross = marked_gross + sum(open_buys.values(), Fraction(0))
    adverse = Fraction(realized) + min(unrealized, Fraction(0))
    loss = max(Fraction(0), Fraction(initial) - equity)
    reasons = []
    if adverse <= -Fraction(loss_limit):
        reasons.append("daily_loss_limit_reached")
    if gross > Fraction(exposure_limit):
        reasons.append("exposure_budget_exceeded")
    if any(row["position_limit_breached"] for row in rows):
        reasons.append("max_position_notional_exceeded")
    report.update({
        "state": "ready",
        "reason": None,
        "initial_cash": ledger._canonical(initial),
        "cash": ledger._canonical(cash),
        "equity": _money(equity),
        "realized_pnl": ledger._canonical(realized),
        "unrealized_pnl": ledger._canonical(recorded_unrealized),
        "gross_exposure": _money(gross),
        "exposure_limit": ledger._canonical(exposure_limit),
        "exposure_headroom": _money(Fraction(exposure_limit) - gross),
        "loss_limit": ledger._canonical(loss_limit),
        "adverse_pnl": _money(adverse),
        "loss_headroom": _money(Fraction(loss_limit) + adverse),
        "drawdown_from_inception": _money(loss / Fraction(initial)),
        "positions": rows,
        "stop_breached": bool(reasons),
        "stop_reasons": reasons,
    })
    return report


def _blank(reason: str) -> dict[str, Any]:
    return {
        "state": "unavailable",
        "reason": reason,
        "initial_cash": None,
        "cash": None,
        "equity": None,
        "realized_pnl": None,
        "unrealized_pnl": None,
        "gross_exposure": None,
        "exposure_limit": None,
        "exposure_headroom": None,
        "loss_limit": None,
        "adverse_pnl": None,
        "loss_headroom": None,
        "drawdown_from_inception": None,
        "positions": [],
        "stop_breached": None,
        "stop_reasons": [],
        "strategy_budgets_enforced": False,
        "caller_allocation_used": False,
        "daily_loss_basis": ledger.DAILY_LOSS_BASIS,
        "book_mutated": False,
        "live_execution": False,
        "source": "paper_book",
    }


def _money(value: Fraction) -> str:
    return ledger._canonical(ledger._finite_decimal(value))


def _matches(value: Fraction, recorded: str) -> bool:
    return ledger._finite_decimal(value) == Decimal(recorded)
