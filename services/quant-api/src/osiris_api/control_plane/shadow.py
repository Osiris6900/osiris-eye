"""Isolated, persistent long/flat shadow strategy comparison; no broker calls."""

from __future__ import annotations

from decimal import Decimal

from .broker import money
from .database import Database, digest, identifier


class ShadowService:
    def __init__(self, db: Database):
        self.db = db

    def run(
        self,
        run_id: str,
        ticks: list[dict],
        *,
        initial_cash: str = "10000",
        max_order_amount: str = "1000",
        daily_loss_limit: str = "500",
        fee_bps: str = "10",
    ) -> dict:
        run_id = identifier(run_id)
        cash, cap, stop = money(initial_cash), money(max_order_amount), money(daily_loss_limit)
        fee_rate = money(fee_bps, positive=False) / 10000
        if (
            not 0 <= fee_rate <= Decimal("0.1")
            or not isinstance(ticks, list)
            or not 2 <= len(ticks) <= 10000
        ):
            raise ValueError("invalid_shadow_configuration")
        fingerprint = digest(
            {
                "ticks": ticks,
                "cash": str(cash),
                "cap": str(cap),
                "stop": str(stop),
                "fee": str(fee_rate),
            }
        )
        old = self.db.get("shadow-runs", run_id)
        if old:
            if old["value"]["input_sha256"] != fingerprint:
                raise ValueError("shadow_run_id_conflict")
            return old["value"]
        initial, quantity, fees, previous_time = cash, Decimal(0), Decimal(0), -1
        pending_target, fills, equity, halted = 0, [], [], False
        for tick in ticks:
            if not isinstance(tick, dict) or set(tick) != {"event_ms", "price", "target"}:
                raise ValueError("invalid_shadow_tick")
            stamp, target = tick["event_ms"], tick["target"]
            if (
                type(stamp) is not int
                or stamp <= previous_time
                or stamp < 0
                or type(target) is not int
                or target not in (0, 1)
            ):
                raise ValueError("invalid_shadow_time_or_signal")
            price = money(tick["price"])
            current_equity = cash + quantity * price
            if current_equity - initial <= -stop:
                halted = True
            # Execution uses the PREVIOUS observation's target, avoiding same-tick lookahead.
            desired = (
                min(cap, current_equity) / price if pending_target and not halted else Decimal(0)
            )
            delta = desired - quantity
            if delta > 0:
                delta = min(delta, cash / (price * (1 + fee_rate)))
            if delta:
                notional, fee = delta * price, abs(delta * price) * fee_rate
                cash -= notional + fee
                quantity += delta
                fees += fee
                fills.append(
                    {
                        "event_ms": stamp,
                        "quantity": str(delta),
                        "price": str(price),
                        "fee": str(fee),
                    }
                )
            equity.append(str(cash + quantity * price))
            pending_target, previous_time = target, stamp
        result = {
            "run_id": run_id,
            "input_sha256": fingerprint,
            "cash": str(cash),
            "quantity": str(quantity),
            "fees": str(fees),
            "pnl": str(Decimal(equity[-1]) - initial),
            "equity": equity,
            "fills": fills,
            "risk_halted": halted,
            "signal_alignment": "previous_tick_target",
            "broker_orders": 0,
            "live_execution": False,
        }
        self.db.put("shadow-runs", run_id, result)
        return result

    def compare(self, run_id: str, paper_equity: list[str]) -> dict:
        run = self.db.get("shadow-runs", identifier(run_id))
        if (
            not run
            or not isinstance(paper_equity, list)
            or len(paper_equity) != len(run["value"]["equity"])
        ):
            raise ValueError("aligned_paper_equity_required")
        differences = [
            money(a, positive=False) - money(b, positive=False)
            for a, b in zip(run["value"]["equity"], paper_equity)
        ]
        return {
            "run_id": run_id,
            "equity_differences": [str(d) for d in differences],
            "max_absolute_difference": str(max(abs(d) for d in differences)),
            "execution_authorized": False,
        }
