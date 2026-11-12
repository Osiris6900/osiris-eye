"""Immutable, source-governed research views. No feed licence or model approval is inferred."""

from __future__ import annotations

import json
import math
import os
from datetime import UTC, datetime
from itertools import pairwise
from statistics import pstdev
from urllib.parse import urlsplit

from ..market_gateway import normalize_bar, parse_market_time
from .broker import money
from .database import Database, canonical, digest, identifier


def source_policies() -> dict:
    raw = os.getenv("OSIRIS_MARKET_VIEW_SOURCES", "{}")
    if len(raw) > 32_768:
        raise ValueError("invalid_market_view_sources")
    try:
        sources = json.loads(raw)
    except ValueError:
        raise ValueError("invalid_market_view_sources") from None
    if not isinstance(sources, dict) or len(sources) > 32:
        raise ValueError("invalid_market_view_sources")
    return sources


def _bounded_text(value, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > maximum
        or any(ord(c) < 32 for c in value)
    ):
        raise ValueError("invalid_market_view_text")
    return value.strip()


class MarketViews:
    def __init__(self, db: Database, sources: dict, *, clock=None):
        self.db, self.sources = db, sources
        self.clock = clock or (lambda: datetime.now(UTC))

    def policy(self, source: str, kind: str, entity: str) -> dict:
        identifier(source)
        policy = self.sources.get(source)
        if not isinstance(policy, dict) or set(policy) != {
            "mode",
            "rights_reference",
            "expires_at",
            "kinds",
            "entities",
            "max_age_seconds",
        }:
            raise ValueError("market_view_source_not_configured")
        if policy["mode"] not in {"synthetic", "operator_licensed"}:
            raise ValueError("unsupported_market_view_source_mode")
        _bounded_text(policy["rights_reference"], 200)
        expiry = parse_market_time(policy["expires_at"])
        for field, maximum in (("kinds", 2), ("entities", 200)):
            values = policy[field]
            if (
                not isinstance(values, list)
                or not 1 <= len(values) <= maximum
                or any(not isinstance(value, str) for value in values)
                or len(set(values)) != len(values)
            ):
                raise ValueError("invalid_market_view_source_policy")
        if (
            set(policy["kinds"]) - {"options", "news"}
            or kind not in policy["kinds"]
            or entity not in policy["entities"]
        ):
            raise ValueError("market_view_source_scope_denied")
        if (
            expiry <= self.clock()
            or type(policy["max_age_seconds"]) is not int
            or not 1 <= policy["max_age_seconds"] <= 604800
        ):
            raise ValueError("expired_or_invalid_market_view_source")
        return policy

    @staticmethod
    def normalize(kind: str, entity: str, payload: dict, event: datetime) -> dict:
        if not isinstance(payload, dict):
            raise TypeError("market_view_payload_required")
        if kind == "news":
            if set(payload) != {"title", "url", "symbols", "sentiment"}:
                raise ValueError("invalid_news_fields")
            title, url = _bounded_text(payload["title"], 500), _bounded_text(payload["url"], 2048)
            parsed = urlsplit(url)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.fragment
                or parsed.port not in {None, 443}
            ):
                raise ValueError("https_news_link_required")
            symbols = payload["symbols"]
            if (
                not isinstance(symbols, list)
                or len(symbols) > 32
                or any(not isinstance(s, str) or identifier(s) != s.upper() for s in symbols)
                or len(set(symbols)) != len(symbols)
            ):
                raise ValueError("invalid_news_symbols")
            sentiment = payload["sentiment"]
            if sentiment is not None and (
                type(sentiment) not in {int, float}
                or not math.isfinite(sentiment)
                or not -1 <= sentiment <= 1
            ):
                raise ValueError("invalid_research_sentiment")
            return {"title": title, "url": url, "symbols": sorted(symbols), "sentiment": sentiment}
        if kind != "options" or set(payload) != {"underlying", "expiry", "currency", "contracts"}:
            raise ValueError("invalid_option_chain_fields")
        if payload["underlying"] != entity:
            raise ValueError("option_underlying_mismatch")
        expiry = datetime.strptime(_bounded_text(payload["expiry"], 10), "%Y-%m-%d").date()
        if expiry.isoformat() != payload["expiry"] or expiry < event.date():
            raise ValueError("invalid_option_expiry")
        currency = payload["currency"]
        if (
            not isinstance(currency, str)
            or len(currency) != 3
            or not currency.isascii()
            or not currency.isupper()
            or not currency.isalpha()
        ):
            raise ValueError("invalid_option_currency")
        contracts = payload["contracts"]
        if not isinstance(contracts, list) or not 1 <= len(contracts) <= 500:
            raise ValueError("invalid_option_contracts")
        seen_symbols, seen_strikes, normalized = set(), set(), []
        for row in contracts:
            if not isinstance(row, dict) or set(row) != {
                "symbol",
                "option_type",
                "strike",
                "bid",
                "ask",
                "volume",
                "open_interest",
            }:
                raise ValueError("invalid_option_contract")
            symbol = identifier(row["symbol"])
            if row["option_type"] not in {"CALL", "PUT"}:
                raise ValueError("invalid_option_type")
            strike, bid, ask = (
                money(row["strike"]),
                money(row["bid"], positive=False),
                money(row["ask"], positive=False),
            )
            if (
                bid < 0
                or ask < bid
                or (row["option_type"], strike) in seen_strikes
                or symbol in seen_symbols
            ):
                raise ValueError("crossed_or_duplicate_option_quote")
            if any(
                type(row[k]) is not int or not 0 <= row[k] <= 2**53 - 1
                for k in ("volume", "open_interest")
            ):
                raise ValueError("invalid_option_counter")
            seen_symbols.add(symbol)
            seen_strikes.add((row["option_type"], strike))
            normalized.append({**row, "strike": str(strike), "bid": str(bid), "ask": str(ask)})
        normalized.sort(key=lambda row: (money(row["strike"]), row["option_type"]))
        return {
            "underlying": entity,
            "expiry": expiry.isoformat(),
            "currency": currency,
            "contracts": normalized,
        }

    def ingest(self, record: dict) -> dict:
        canonical(record)
        if not isinstance(record, dict) or set(record) != {
            "id",
            "source",
            "kind",
            "entity",
            "event_time",
            "available_at",
            "payload",
        }:
            raise ValueError("invalid_market_view_record")
        key, entity = identifier(record["id"]), identifier(record["entity"])
        if entity != entity.upper() or record["kind"] not in {"options", "news"}:
            raise ValueError("invalid_market_view_kind_or_entity")
        policy = self.policy(record["source"], record["kind"], entity)
        event, declared = (
            parse_market_time(record["event_time"]),
            parse_market_time(record["available_at"]),
        )
        now = self.clock()
        if event > declared or declared > now:
            raise ValueError("future_or_inverted_market_view_time")
        payload = self.normalize(record["kind"], entity, record["payload"], event)
        fingerprint = digest(
            {
                **record,
                "id": None,
                "event_time": event.isoformat(),
                "available_at": declared.isoformat(),
                "payload": payload,
            }
        )
        value = {
            "id": key,
            "kind": record["kind"],
            "entity": entity,
            "source": record["source"],
            "source_mode": policy["mode"],
            "rights_reference": policy["rights_reference"],
            "rights_independently_verified": False,
            "event_time": event.isoformat(),
            "source_available_at": declared.isoformat(),
            "available_at": now.isoformat(),
            "received_at": now.isoformat(),
            "event_ms": int(event.timestamp() * 1000),
            "available_ms": int(now.timestamp() * 1000),
            "payload": payload,
            "input_sha256": fingerprint,
            "payload_sha256": digest(payload),
            "live_execution": False,
        }
        value["record_sha256"] = digest(value)
        with self.db.transaction() as conn:
            old = conn.execute(
                "SELECT id,body FROM cp_documents WHERE collection='market-views' AND (id=? OR json_extract(body,'$.input_sha256')=?)",
                (key, fingerprint),
            ).fetchall()
            if old:
                if any(json.loads(row["body"])["input_sha256"] != fingerprint for row in old):
                    raise ValueError("immutable_market_view_conflict")
                return {"record": self._verified(json.loads(old[0]["body"])), "duplicate": True}
            conn.execute(
                "INSERT INTO cp_documents VALUES ('market-views',?,1,?)",
                (key, canonical(value).decode()),
            )
        return {"record": value, "duplicate": False}

    @staticmethod
    def _verified(value: dict) -> dict:
        if (
            not isinstance(value, dict)
            or digest({k: v for k, v in value.items() if k != "record_sha256"})
            != value.get("record_sha256")
            or digest(value.get("payload")) != value.get("payload_sha256")
        ):
            raise ValueError("market_view_digest_mismatch")
        return value

    def read(self, kind: str, entity: str, *, as_of: str | None = None, limit: int = 30) -> dict:
        entity = identifier(entity)
        if (
            kind not in {"news", "options"}
            or entity != entity.upper()
            or type(limit) is not int
            or not 1 <= limit <= 100
        ):
            raise ValueError("invalid_market_view_query")
        now = self.clock()
        cutoff = parse_market_time(as_of) if as_of else now
        if cutoff > now:
            raise ValueError("future_market_view_query")
        allowed = {}
        for source in self.sources:
            try:
                allowed[source] = self.policy(source, kind, entity)
            except (ValueError, TypeError):
                continue
        result = {
            "kind": kind,
            "entity": entity,
            "as_of": cutoff.isoformat(),
            "records": [],
            "status": "unavailable",
            "reason": "source_not_configured" if not allowed else "no_visible_record",
            "live_execution": False,
            "rights_independently_verified": False,
        }
        if not allowed:
            return result
        placeholders = ",".join("?" for _ in allowed)
        with self.db.transaction() as conn:
            records = conn.execute(
                "SELECT body FROM cp_documents WHERE collection='market-views' AND json_extract(body,'$.kind')=? AND json_extract(body,'$.entity')=? "
                f"AND json_extract(body,'$.source') IN ({placeholders}) "
                "AND json_extract(body,'$.event_ms')<=? AND json_extract(body,'$.available_ms')<=? ORDER BY json_extract(body,'$.event_ms') DESC, json_extract(body,'$.available_ms') DESC, id LIMIT ?",
                (
                    kind,
                    entity,
                    *allowed,
                    int(cutoff.timestamp() * 1000),
                    int(cutoff.timestamp() * 1000),
                    limit,
                ),
            ).fetchall()
        for row in records:
            item = self._verified(json.loads(row["body"]))
            policy = allowed[item["source"]]
            if (
                parse_market_time(item["available_at"]) > cutoff
                or parse_market_time(item["event_time"]) > cutoff
            ):
                continue
            if kind == "options" and item["payload"]["expiry"] < cutoff.date().isoformat():
                continue
            if (
                item["source_mode"] != policy["mode"]
                or item["rights_reference"] != policy["rights_reference"]
            ):
                continue
            age = (cutoff - parse_market_time(item["event_time"])).total_seconds()
            if 0 <= age <= policy["max_age_seconds"]:
                result["records"].append(item)
        if result["records"]:
            result.update(status="available", reason=None)
        elif records:
            result["reason"] = "stale_or_changed_source_policy"
        return result


def regime_diagnostic(history: dict) -> dict:
    """Descriptive per-observation thresholds, not fitted predictions or OOS approval."""
    bars = history.get("bars")
    if (
        history.get("mode") not in {"public", "synthetic", "sandbox"}
        or not isinstance(bars, list)
        or not 21 <= len(bars) <= 50000
    ):
        raise ValueError("at_least_21_bars_required_for_regime")
    normalized = [normalize_bar(row, history["symbol"]) for row in bars]
    closes = [row["close"] for row in normalized]
    now = datetime.now(UTC)
    if any(parse_market_time(row["available_at"]) > now for row in normalized) or any(
        parse_market_time(b["event_time"]) <= parse_market_time(a["event_time"])
            for a, b in pairwise(normalized)
    ):
        raise ValueError("unavailable_or_unordered_regime_bars")
    returns = [math.log(b) - math.log(a) for a, b in pairwise(closes)]
    volatility, movement = pstdev(returns), sum(abs(r) for r in returns)
    efficiency = sum(returns) / movement if movement else 0.0
    high, drawdown = closes[0], 0.0
    for close in closes:
        high = max(high, close)
        drawdown = max(drawdown, 1 - close / high)
    label = (
        "crisis"
        if drawdown >= 0.2
        else "volatile"
        if volatility >= 0.03
        else "trending_up"
        if efficiency >= 0.5
        else "trending_down"
        if efficiency <= -0.5
        else "ranging"
    )
    return {
        "schema": "osiris.regime-diagnostic.v1",
        "method": "descriptive-log-return-thresholds-v1",
        "label": label,
        "provider": history["provider"],
        "symbol": history["symbol"],
        "source_mode": history["mode"],
        "bars_sha256": digest(normalized),
        "observations": len(normalized),
        "first_event_time": normalized[0]["event_time"],
        "last_event_time": normalized[-1]["event_time"],
        "last_available_at": max(row["available_at"] for row in normalized),
        "per_observation_volatility": volatility,
        "directional_efficiency": efficiency,
        "max_drawdown": drawdown,
        "thresholds": {
            "crisis_drawdown": 0.2,
            "volatile_per_observation_log_return_std": 0.03,
            "trend_efficiency": 0.5,
        },
        "independent_model_approval": False,
        "liquidity_state": "unavailable_without_order_book",
        "live_execution": False,
    }
