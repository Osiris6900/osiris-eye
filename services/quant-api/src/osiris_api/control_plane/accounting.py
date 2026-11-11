"""Read-only attribution from the existing canonical paper execution database."""

from __future__ import annotations

import sqlite3
from decimal import Decimal
from pathlib import Path

from .database import Database, identifier


class AttributionService:
    def __init__(self, db: Database):
        self.db = db

    def tag_order(self, client_order_id: str, strategy: str, reference_price: str):
        from .broker import money

        value = {"strategy": identifier(strategy), "reference_price": str(money(reference_price))}
        key = identifier(client_order_id)
        old = self.db.get("order-strategies", key)
        if old:
            if old["value"] != value:
                raise ValueError("order_strategy_is_immutable")
            return
        self.db.put("order-strategies", key, value)

    def report(self, canonical_ledger_path: str) -> dict:
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
            account = connection.execute("SELECT * FROM paper_exec_meta LIMIT 1").fetchone()
        finally:
            connection.close()
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
