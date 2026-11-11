"""Read-only Coinbase L2 snapshot capture and bounded depth/impact analysis."""

from __future__ import annotations

import time
from urllib.parse import quote

from .broker import money
from .database import Database, digest, identifier
from .vendors import HttpJson


class DepthService:
    def __init__(
        self, db: Database, *, entitlement_reference: str, transport=None, clock=time.time
    ):
        if not entitlement_reference:
            raise ValueError("depth_entitlement_reference_required")
        self.db, self.transport, self.clock = db, transport or HttpJson(), clock

    def capture(self, instrument: str) -> dict:
        instrument = identifier(instrument)
        raw = self.transport.request(
            "GET",
            "https://api.exchange.coinbase.com/products/"
            + quote(instrument, safe="")
            + "/book?level=2",
        )
        if not isinstance(raw, dict) or type(raw.get("sequence")) is not int or raw["sequence"] < 0:
            raise ValueError("invalid_depth_sequence")
        book = {
            "sequence": raw["sequence"],
            "instrument": instrument,
            "captured_ms": int(self.clock() * 1000),
            "raw_sha256": digest(raw),
        }
        for side in ("bids", "asks"):
            rows = raw.get(side)
            if not isinstance(rows, list) or not 1 <= len(rows) <= 20000:
                raise ValueError("invalid_depth_levels")
            levels = []
            for row in rows:
                if not isinstance(row, list) or len(row) != 3:
                    raise ValueError("invalid_depth_level")
                price, size = money(row[0]), money(row[1])
                levels.append((price, size))
            if len(set(price for price, _ in levels)) != len(levels):
                raise ValueError("duplicate_depth_price")
            book[side] = [
                [str(price), str(size)]
                for price, size in sorted(levels, reverse=side == "bids")[:1000]
            ]
            book[side + "_truncated"] = len(levels) > 1000
        if money(book["bids"][0][0]) >= money(book["asks"][0][0]):
            raise ValueError("crossed_depth_book")
        prior = self.db.get("depth", instrument)
        if prior and prior["value"]["sequence"] >= book["sequence"]:
            raise ValueError("stale_or_replayed_depth")
        self.db.put("depth", instrument, book, expected_version=prior["version"] if prior else 0)
        self.db.put("depth-history", f"{instrument}:{book['sequence']}", book)
        return book

    def impact(self, instrument: str, side: str, quantity: str, *, max_age_ms: int = 5000) -> dict:
        prior = self.db.get("depth", identifier(instrument))
        if not prior or type(max_age_ms) is not int or max_age_ms <= 0:
            raise ValueError("depth_unavailable")
        book = prior["value"]
        age = int(self.clock() * 1000) - book["captured_ms"]
        if age < 0 or age > max_age_ms or side not in {"buy", "sell"}:
            raise ValueError("stale_depth_or_invalid_side")
        requested = money(quantity)
        remaining, cost = requested, money("0", positive=False)
        levels = book["asks"] if side == "buy" else book["bids"]
        for price, size in levels:
            filled = min(remaining, money(size))
            cost += filled * money(price)
            remaining -= filled
            if remaining == 0:
                break
        filled = requested - remaining
        return {
            "filled_quantity": str(filled),
            "unfilled_quantity": str(remaining),
            "average_price": str(cost / filled) if filled else None,
            "sequence": book["sequence"],
            "execution_authorized": False,
        }
