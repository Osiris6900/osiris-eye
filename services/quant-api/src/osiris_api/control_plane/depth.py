"""Atomic Coinbase L2 captures and exact, read-only displayed-depth analysis."""

from __future__ import annotations

import json
import math
import time
from contextlib import contextmanager
from decimal import ROUND_HALF_EVEN, Context, Decimal, Inexact, localcontext
from fractions import Fraction
from urllib.parse import quote

from .broker import money
from .database import Database, canonical, digest, identifier
from .vendors import HttpJson


def _exact(value: Fraction) -> str:
    # Bounded input exponents are at most 1000. Sums/products of the retained
    # 1000 decimal levels terminate within this precision. Trap any lost money.
    with localcontext(Context(prec=4096)) as context:
        context.traps[Inexact] = True
        result = format(Decimal(value.numerator) / Decimal(value.denominator), "f")
    return result.rstrip("0").rstrip(".") if "." in result else result


def _average(value: Fraction) -> tuple[str, bool]:
    # A VWAP can repeat. Only its display is rounded, under an explicit context;
    # fills, quantities, notionals and cost decomposition remain exact.
    with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
        result = str(Decimal(value.numerator) / Decimal(value.denominator))
    return result, Fraction(Decimal(result)) != value


def _history_key(instrument: str, sequence: int) -> str:
    return "book:" + digest({"instrument": instrument, "sequence": sequence})


class DepthService:
    def __init__(
        self, db: Database, *, entitlement_reference: str, transport=None, clock=time.time
    ):
        if (
            not isinstance(entitlement_reference, str)
            or not entitlement_reference.strip()
            or len(entitlement_reference) > 512
            or any(ord(char) < 32 for char in entitlement_reference)
        ):
            raise ValueError("depth_entitlement_reference_required")
        self.db, self.transport, self.clock = db, transport or HttpJson(), clock
        self.entitlement_reference = entitlement_reference

    def _clock_ms(self) -> int:
        now = self.clock()
        if type(now) not in (int, float) or not 0 <= now <= 253402300799 or not math.isfinite(now):
            raise ValueError("invalid_depth_clock")
        return int(now * 1000)

    @contextmanager
    def _timed_transaction(self, instrument: str):
        # Keep the observed clock even when an expired/replayed read is refused.
        # Otherwise rolling time back after a refusal would rejuvenate a book.
        failure = None
        with self.db.transaction() as conn:
            now = self._clock_ms()
            prior = conn.execute(
                "SELECT body FROM cp_documents WHERE collection='depth-clock' AND id=?",
                (instrument,),
            ).fetchone()
            if prior and now < json.loads(prior[0])["observed_ms"]:
                raise ValueError("depth_clock_rollback")
            conn.execute(
                "INSERT INTO cp_documents VALUES ('depth-clock',?,1,?) "
                "ON CONFLICT(collection,id) DO UPDATE SET version=version+1,body=excluded.body",
                (instrument, canonical({"observed_ms": now}).decode()),
            )
            conn.execute("SAVEPOINT depth_operation")
            try:
                yield conn, now
            except BaseException as exc:  # noqa: BLE001 - preserve clock, undo all business writes
                conn.execute("ROLLBACK TO depth_operation")
                failure = exc
            finally:
                conn.execute("RELEASE depth_operation")
        if failure is not None:
            raise failure

    def capture(self, instrument: str) -> dict:
        instrument = identifier(instrument)
        source_url = (
            "https://api.exchange.coinbase.com/products/"
            + quote(instrument, safe="")
            + "/book?level=2"
        )
        started_ms = self._clock_ms()
        raw = self.transport.request("GET", source_url)
        received_ms = self._clock_ms()
        if received_ms < started_ms:
            raise ValueError("depth_clock_rollback")
        if (
            not isinstance(raw, dict)
            or type(raw.get("sequence")) is not int
            or not 0 <= raw["sequence"] < 2**63
        ):
            raise ValueError("invalid_depth_sequence")
        book = {
            "schema_version": 2,
            "sequence": raw["sequence"],
            "snapshot_id": _history_key(instrument, raw["sequence"]),
            "instrument": instrument,
            "venue": "coinbase-exchange",
            "source_url": source_url,
            "entitlement_reference": self.entitlement_reference,
            "captured_ms": received_ms,
            "age_basis": "local-response-receipt",
            "raw_sha256": digest(raw),
        }
        for side in ("bids", "asks"):
            rows = raw.get(side)
            if not isinstance(rows, list) or not 1 <= len(rows) <= 20000:
                raise ValueError("invalid_depth_levels")
            levels = []
            for row in rows:
                if (
                    not isinstance(row, list)
                    or len(row) != 3
                    or type(row[2]) is not int
                    or not 0 < row[2] < 2**63
                ):
                    raise ValueError("invalid_depth_level")
                price, size = money(row[0]), money(row[1])
                levels.append((price, size))
            if len({price for price, _ in levels}) != len(levels):
                raise ValueError("duplicate_depth_price")
            book[side] = [
                [str(price), str(size)]
                for price, size in sorted(levels, reverse=side == "bids")[:1000]
            ]
            book[side + "_truncated"] = len(levels) > 1000
        if money(book["bids"][0][0]) >= money(book["asks"][0][0]):
            raise ValueError("crossed_depth_book")
        with self._timed_transaction(instrument) as (conn, now):
            if now < received_ms:
                raise ValueError("depth_clock_rollback")
            prior = conn.execute(
                "SELECT body FROM cp_documents WHERE collection='depth' AND id=?", (instrument,)
            ).fetchone()
            if prior:
                previous = json.loads(prior[0])
                if previous["sequence"] >= book["sequence"]:
                    raise ValueError("stale_or_replayed_depth")
                if previous["captured_ms"] > received_ms:
                    raise ValueError("depth_clock_rollback")
            if conn.execute(
                "SELECT 1 FROM cp_documents WHERE collection='depth-history' AND id=?",
                (book["snapshot_id"],),
            ).fetchone():
                raise ValueError("depth_history_conflict")
            body = canonical(book).decode()
            conn.execute(
                "INSERT INTO cp_documents VALUES ('depth',?,1,?) "
                "ON CONFLICT(collection,id) DO UPDATE SET version=version+1,body=excluded.body",
                (instrument, body),
            )
            conn.execute(
                "INSERT INTO cp_documents VALUES ('depth-history',?,1,?)",
                (book["snapshot_id"], body),
            )
        return book

    def impact(
        self,
        instrument: str,
        side: str,
        quantity: str,
        *,
        max_age_ms: int = 5000,
        limit_price: str | None = None,
    ) -> dict:
        instrument = identifier(instrument)
        if type(max_age_ms) is not int or max_age_ms <= 0:
            raise ValueError("depth_unavailable")
        if not isinstance(side, str) or side not in {"buy", "sell"}:
            raise ValueError("stale_depth_or_invalid_side")
        requested = Fraction(money(quantity))
        limit = None if limit_price is None else Fraction(money(limit_price))
        with self._timed_transaction(instrument) as (conn, now):
            prior = conn.execute(
                "SELECT body FROM cp_documents WHERE collection='depth' AND id=?", (instrument,)
            ).fetchone()
            if not prior:
                raise ValueError("depth_unavailable")
            book = json.loads(prior[0])
            if book.get("schema_version") != 2:
                raise ValueError("depth_recapture_required")
            if book["entitlement_reference"] != self.entitlement_reference:
                raise ValueError("depth_entitlement_mismatch")
            history = conn.execute(
                "SELECT body FROM cp_documents WHERE collection='depth-history' AND id=?",
                (book["snapshot_id"],),
            ).fetchone()
            if not history or history[0] != prior[0]:
                raise ValueError("depth_history_mismatch")
            age = now - book["captured_ms"]
            if age < 0 or age > max_age_ms:
                raise ValueError("stale_depth_or_invalid_side")
        remaining, cost, fills = requested, Fraction(0), []
        levels = book["asks"] if side == "buy" else book["bids"]
        for price, size in levels:
            price, size = Fraction(money(price)), Fraction(money(size))
            if limit is not None and (
                (side == "buy" and price > limit) or (side == "sell" and price < limit)
            ):
                break
            filled = min(remaining, size)
            cost += filled * price
            remaining -= filled
            fills.append({"price": _exact(price), "quantity": _exact(filled)})
            if remaining == 0:
                break
        filled = requested - remaining
        average, rounded, ratio = None, False, None
        if filled:
            vwap = cost / filled
            average, rounded = _average(vwap)
            ratio = {"numerator": str(vwap.numerator), "denominator": str(vwap.denominator)}
        bid, ask = Fraction(money(book["bids"][0][0])), Fraction(money(book["asks"][0][0]))
        midpoint, direction = (bid + ask) / 2, 1 if side == "buy" else -1
        best = ask if side == "buy" else bid
        spread_cost = direction * (best - midpoint) * filled
        depth_cost = direction * (cost - best * filled)
        return {
            "instrument": instrument,
            "side": side,
            "requested_quantity": _exact(requested),
            "filled_quantity": _exact(filled),
            "unfilled_quantity": _exact(remaining),
            "average_price": average,
            "average_price_is_rounded": rounded,
            "average_price_exact_ratio": ratio,
            "filled_notional": _exact(cost),
            "fills": fills,
            "limit_price": None if limit is None else _exact(limit),
            "reference_midpoint": _exact(midpoint),
            "spread_cost": _exact(spread_cost),
            "visible_depth_cost": _exact(depth_cost),
            "shortfall_from_midpoint": _exact(spread_cost + depth_cost),
            "sequence": book["sequence"],
            "snapshot_id": book["snapshot_id"],
            "snapshot_sha256": digest(book),
            "raw_sha256": book["raw_sha256"],
            "source_url": book["source_url"],
            "entitlement_reference": book["entitlement_reference"],
            "captured_ms": book["captured_ms"],
            "age_ms": age,
            "age_basis": book["age_basis"],
            "retained_depth_truncated": book[
                "asks_truncated" if side == "buy" else "bids_truncated"
            ],
            "model": "static-displayed-book-sweep",
            "limitations": [
                "Local receipt age does not verify exchange timestamp or vendor cache freshness.",
                "No queue, latency, replenishment, hidden liquidity, fees, borrow or margin model.",
                "Read-only estimates do not consume depth or calibrate future market impact.",
                "The entitlement reference is an operator declaration, not rights-owner approval.",
            ],
            "execution_authorized": False,
        }
