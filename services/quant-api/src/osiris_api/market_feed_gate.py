"""Point-in-time market observation validation; no vendor or exchange connection.

Paper marks reuse this gate together with ``validate_feed_tick`` and
``StreamingFeedGate``. Those checks are composed by ``admit_market_observation``.
Nothing here enables a live order.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import math

from .authorized_feeds import (
    ADAPTER_ENTITLEMENTS,
    FEED_TICK_SCHEMA,
    FeedAdapterRejection,
    is_adapter_suspended,
    validate_feed_tick,
)
from .streaming_feed_gate import FeedRejected, StreamingFeedGate


# Paper-book sources that are not vendor adapters. Adapter names are authorized
# only through the reviewed registry, and a suspension still wins.
PAPER_MARK_SOURCES = frozenset({
    "paper-ledger",
    "coinbase",
    "kraken",
    "coinbase-public",
    "coinbase-exchange",
    "coinbase-public-forward-capture",
})
# A new mark may be at most this many times the last good mark, in either direction.
DEFAULT_MAX_MARK_JUMP_MULTIPLE = Decimal("10")
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


@dataclass(frozen=True)
class MarketObservation:
    symbol: str
    price: float
    event_time_ms: int
    received_time_ms: int
    authenticated: bool
    entitled: bool
    source: str


@dataclass(frozen=True)
class MarkCursor:
    """Last accepted mark for one symbol. A gap latch blocks sequenced ticks."""

    price: Decimal
    event_time: datetime
    sequence: int | None = None
    gap_open: bool = False
    source: str = "paper-ledger"


def validate_market_observation(
    observation: MarketObservation,
    *,
    decision_time_ms: int,
    max_age_ms: int = 5000,
    previous_event_time_ms: int | None = None,
    max_future_skew_ms: int = 0,
) -> tuple[bool, str]:
    """Fail closed on invalid provenance, time, value and entitlement."""
    if not observation.authenticated:
        return False, "source_unauthenticated"
    if not observation.entitled:
        return False, "source_unentitled"
    if not observation.symbol.strip() or not observation.source.strip():
        return False, "missing_instrument_or_source"
    if isinstance(observation.price, bool) or not isinstance(observation.price, (int, float)):
        return False, "invalid_price"
    try:
        if not math.isfinite(float(observation.price)) or observation.price <= 0:
            return False, "invalid_price"
    except (OverflowError, ValueError):
        return False, "invalid_price"
    timestamps = (
        observation.event_time_ms,
        observation.received_time_ms,
        decision_time_ms,
        max_age_ms,
        max_future_skew_ms,
    )
    if any(type(value) is not int or value < 0 for value in timestamps):
        return False, "invalid_timestamp"
    if previous_event_time_ms is not None and (
        type(previous_event_time_ms) is not int or previous_event_time_ms < 0
    ):
        return False, "invalid_previous_timestamp"
    if observation.event_time_ms > observation.received_time_ms:
        return False, "event_after_receipt"
    if observation.event_time_ms > decision_time_ms + max_future_skew_ms:
        return False, "future_receipt"
    if observation.received_time_ms > decision_time_ms + max_future_skew_ms:
        return False, "future_receipt"
    if previous_event_time_ms is not None and observation.event_time_ms <= previous_event_time_ms:
        return False, "out_of_order_observation"
    if decision_time_ms - observation.event_time_ms > max_age_ms:
        return False, "stale_observation"
    return True, "accepted_for_research_only"


def _aware(value: datetime) -> datetime | None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        return None
    return value.astimezone(timezone.utc)


def _epoch_ms(value: datetime) -> int | None:
    current = _aware(value)
    if current is None:
        return None
    millis = int((current - _EPOCH).total_seconds() * 1000)
    if millis < 0:
        return None
    return millis


def _seconds_to_ms(seconds: float) -> int | None:
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds):
        return None
    if seconds < 0 or seconds > 86_400 * 10:
        return None
    return int(Decimal(str(seconds)) * 1000)


def _price_text(price: Decimal) -> str:
    if not isinstance(price, Decimal) or not price.is_finite():
        return "NaN"
    return format(price, "f")


def _price_float(price: Decimal) -> float:
    if not isinstance(price, Decimal) or not price.is_finite():
        return float("nan")
    try:
        value = float(price)
    except (OverflowError, ValueError):
        return float("inf")
    return value


def _quote_reason(bid: Decimal | None, ask: Decimal | None) -> str | None:
    if bid is None and ask is None:
        return None
    if bid is None or ask is None:
        return None
    if (
        not isinstance(bid, Decimal) or not isinstance(ask, Decimal)
        or not bid.is_finite() or not ask.is_finite()
        or bid <= 0 or ask <= 0
    ):
        return "invalid_price"
    if bid > ask:
        return "crossed_quote"
    if bid == ask:
        return "locked_quote"
    return None


def _outlier_reason(price: Decimal, previous: MarkCursor | None, multiple: Decimal) -> str | None:
    if previous is None:
        return None
    if (
        not isinstance(multiple, Decimal) or not multiple.is_finite()
        or multiple < 1
    ):
        return "invalid_jump_threshold"
    last = previous.price
    if not isinstance(last, Decimal) or not last.is_finite() or last <= 0:
        return "outlier_price"
    if not isinstance(price, Decimal) or not price.is_finite() or price <= 0:
        return "outlier_price"
    if max(price, last) / min(price, last) > multiple:
        return "outlier_price"
    return None


def _feed_reason(exc: FeedAdapterRejection) -> str:
    code = str(exc)
    if code in {"tick_price_invalid", "tick_price_not_positive_finite"}:
        return "invalid_price"
    if code in {"tick_stale_at_decision", "tick_stale_beyond_delay"}:
        return "stale_observation"
    if code in {"tick_available_in_future", "tick_received_in_future"}:
        return "future_receipt"
    if code == "tick_time_order_violated":
        return "event_after_receipt"
    if code in {"tick_symbol_required", "tick_source_required"}:
        return "missing_instrument_or_source"
    if code.endswith("_invalid") or code.endswith("_missing_timezone"):
        return "invalid_timestamp"
    return "feed_tick_rejected"


def _sequence_reason(exc: FeedRejected, *, previous: MarkCursor | None, sequence: int, event: datetime) -> str:
    text = str(exc)
    if text == "duplicate or out-of-order observation":
        if previous is not None and previous.sequence is not None and event < previous.event_time:
            return "out_of_order_observation"
        if previous is not None and previous.sequence is not None and sequence <= previous.sequence:
            return "duplicate_tick"
        return "duplicate_or_out_of_order"
    if "sequence gap" in text or "does not advance" in text:
        return "sequence_gap"
    if "behind cursor" in text:
        return "out_of_order_observation"
    if text in {"invalid sequence", "invalid price", "price must be finite and positive"}:
        return "invalid_sequence" if text == "invalid sequence" else "invalid_price"
    if "stale" in text:
        return "stale_observation"
    if "future" in text or "before event" in text:
        return "future_receipt"
    if text == "feed not entitled":
        return "source_unentitled"
    return "feed_tick_rejected"


def admit_market_observation(
    *,
    symbol: str,
    price: Decimal,
    source: str,
    event_time: datetime,
    received_time: datetime,
    now: datetime,
    max_age_seconds: float,
    max_future_skew_seconds: float,
    previous: MarkCursor | None = None,
    sequence: int | None = None,
    bid: Decimal | None = None,
    ask: Decimal | None = None,
    resync: bool = False,
    max_jump_multiple: Decimal = DEFAULT_MAX_MARK_JUMP_MULTIPLE,
    available_time: datetime | None = None,
    enforce_sequence: bool = True,
    enforce_time_order: bool = True,
) -> tuple[bool, str]:
    """Run the existing feed gates and the paper-mark checks they do not cover.

    Freshness uses ``event_time`` and ``received_time`` against ``now``. A caller
    freshness flag is not an input. An accepted tick is the only one that may
    move a mark. ``sequence_gap`` is the signal to latch the cursor.
    """
    name = source.strip() if isinstance(source, str) else ""
    known = bool(name) and (name in PAPER_MARK_SOURCES or name in ADAPTER_ENTITLEMENTS)
    suspended = bool(name) and name in ADAPTER_ENTITLEMENTS and is_adapter_suspended(name)
    event = _aware(event_time)
    received = _aware(received_time)
    clock = _aware(now)
    available = _aware(available_time) if available_time is not None else event
    decision_ms = None if clock is None else _epoch_ms(clock)
    event_ms = None if event is None else _epoch_ms(event)
    received_ms = None if received is None else _epoch_ms(received)
    max_age_ms = _seconds_to_ms(max_age_seconds)
    skew_ms = _seconds_to_ms(max_future_skew_seconds)
    if (
        event is None or received is None or clock is None or available is None
        or decision_ms is None or event_ms is None or received_ms is None
        or max_age_ms is None or skew_ms is None
        or not isinstance(symbol, str) or not symbol.strip()
    ):
        return False, "invalid_timestamp"
    previous_ms = None
    if (
        enforce_time_order
        and sequence is None
        and previous is not None
        and event < previous.event_time
    ):
        previous_ms = _epoch_ms(previous.event_time)
        if previous_ms is None:
            return False, "invalid_timestamp"
    observation = MarketObservation(
        symbol=symbol.strip(),
        price=_price_float(price),
        event_time_ms=event_ms,
        received_time_ms=received_ms,
        authenticated=known,
        entitled=known and not suspended,
        source=name,
    )
    feed_problem: str | None = None
    try:
        feed_age = float(max_age_seconds)
        if isinstance(max_age_seconds, bool) or not math.isfinite(feed_age) or feed_age <= 0:
            raise FeedAdapterRejection("tick_decision_age_bound_invalid")
        if feed_age > 86_400:
            feed_age = 86_400.0
        validate_feed_tick(
            {
                "schema": FEED_TICK_SCHEMA,
                "symbol": symbol.strip(),
                "source": name or " ",
                "event_time": event.isoformat(),
                "available_time": available.isoformat(),
                "received_time": received.isoformat(),
                "price": _price_text(price),
            },
            now=clock,
            max_age_seconds_at_decision=feed_age,
        )
    except FeedAdapterRejection as exc:
        feed_problem = _feed_reason(exc)
    accepted, observation_reason = validate_market_observation(
        observation,
        decision_time_ms=decision_ms,
        max_age_ms=max_age_ms,
        previous_event_time_ms=previous_ms,
        max_future_skew_ms=skew_ms,
    )
    if feed_problem is not None:
        return False, feed_problem
    if not accepted:
        return False, observation_reason
    quote = _quote_reason(bid, ask)
    if quote is not None:
        return False, quote
    jump = _outlier_reason(price, previous, max_jump_multiple)
    if jump is not None:
        return False, jump
    if not enforce_sequence or sequence is None:
        return True, "accepted"
    if type(resync) is not bool:
        return False, "invalid_sequence"
    stream = StreamingFeedGate(
        {name: {symbol.strip()}},
        max_age=timedelta(seconds=float(max_age_seconds)),
        max_future_skew=timedelta(seconds=float(max_future_skew_seconds)),
    )
    same_source = previous is not None and previous.source == name and previous.sequence is not None
    if same_source:
        try:
            stream.seed_cursor(
                provider=name,
                instrument_id=symbol.strip(),
                sequence=previous.sequence,
                event_time=previous.event_time,
                price=format(previous.price, "f"),
                gap_open=previous.gap_open,
            )
        except FeedRejected as exc:
            return False, _sequence_reason(exc, previous=previous, sequence=sequence, event=event)
    try:
        action = stream.resync if resync else stream.ingest
        action(
            provider=name,
            instrument_id=symbol.strip(),
            price=_price_text(price),
            event_time=event,
            available_at=available,
            sequence=sequence,
            now=clock,
        )
    except FeedRejected as exc:
        return False, _sequence_reason(exc, previous=previous if same_source else None, sequence=sequence, event=event)
    return True, "accepted"
