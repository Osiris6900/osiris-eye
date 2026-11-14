"""Frozen, forward-recorded public quotes and reproducible research diagnostics.

The original response bytes and client receive times are retained. File hashes
check consistency; they do not independently authenticate a source, licence,
host, model approval or profitable strategy. No broker transport is used.
"""
from __future__ import annotations

import hashlib
import json
import math
import platform
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from statistics import median

import numpy as np

from .authorized_feeds import FEED_TICK_SCHEMA, ticks_digest, validate_feed_tick
from .control_plane.broker import money
from .control_plane.database import Database, digest
from .control_plane.models import ModelService
from .public_market_data import _get
from .walk_forward import MomentumReferenceModel, governance_record, run_walk_forward

ORIGIN = "https://api.exchange.coinbase.com"
SCHEMA = "osiris.keyless-forward-research.v1"
MAX_RESPONSE_BYTES = 262_144
MAX_ARCHIVE_BYTES = 4_194_304
SOURCE_FILES = (
    "authorized_feeds.py", "keyless_research.py", "public_market_data.py",
    "walk_forward.py", "control_plane/database.py",
    "control_plane/broker.py",
    "control_plane/models.py",
)
PLAN_KEYS = {
    "schema", "candidate_commit", "symbol", "samples", "interval_seconds",
    "created_at", "model", "walk_forward", "cost_grid_bps", "max_quote_age_seconds",
    "source_origin", "purpose", "redistribution_authorized", "live_trading_authorized",
    "fitted_baseline",
}


def source_manifest() -> dict:
    root = Path(__file__).resolve().parent
    files = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in SOURCE_FILES}
    script = root.parents[3] / "scripts/record_keyless_research.py"
    files["scripts/record_keyless_research.py"] = hashlib.sha256(script.read_bytes()).hexdigest()
    return files


def _time(value: object) -> datetime:
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError("invalid_capture_time")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("invalid_capture_time") from None
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("invalid_capture_time")
    return stamp.astimezone(UTC)


def _symbol(symbol: object) -> str:
    if not isinstance(symbol, str) or not re.fullmatch(r"[A-Z0-9]{2,15}-USD", symbol):
        raise ValueError("keyless_research_requires_usd_product")
    return symbol


def research_plan(candidate_commit: str, *, symbol="BTC-USD", samples=32,
                  interval_seconds=3, created_at: datetime) -> dict:
    plan = {
        "schema": "osiris.keyless-forward-plan.v1", "candidate_commit": candidate_commit,
        "symbol": _symbol(symbol), "samples": samples, "interval_seconds": interval_seconds,
        "created_at": created_at.isoformat(),
        "model": {"id": "momentum-reference-keyless-forward", "version": "0.1.0",
                  "window": 4, "scale": 0.5},
        "fitted_baseline": {"kind": "ridge-v1", "ridge": 0.001, "holdout": 4, "feature_lags": 3},
        "walk_forward": {"train_min": 8, "test_size": 4, "step": 4},
        "cost_grid_bps": [0, 10, 25], "max_quote_age_seconds": 30,
        "source_origin": ORIGIN, "purpose": "internal_research_paper_only",
        "redistribution_authorized": False, "live_trading_authorized": False,
    }
    validate_plan(plan)
    return plan


def validate_plan(plan: object) -> None:
    if not isinstance(plan, dict) or set(plan) != PLAN_KEYS:
        raise ValueError("invalid_keyless_plan")
    if (plan["schema"] != "osiris.keyless-forward-plan.v1"
            or not isinstance(plan["candidate_commit"], str)
            or not re.fullmatch(r"[0-9a-f]{40}", plan["candidate_commit"])
            or type(plan["samples"]) is not int or not 20 <= plan["samples"] <= 200
            or type(plan["interval_seconds"]) not in (int, float)
            or not 1 <= plan["interval_seconds"] <= 30
            or not math.isfinite(plan["interval_seconds"])
            or plan["model"] != {"id": "momentum-reference-keyless-forward", "version": "0.1.0",
                                 "window": 4, "scale": 0.5}
            or plan["fitted_baseline"] != {"kind": "ridge-v1", "ridge": 0.001, "holdout": 4, "feature_lags": 3}
            or plan["walk_forward"] != {"train_min": 8, "test_size": 4, "step": 4}
            or plan["cost_grid_bps"] != [0, 10, 25]
            or plan["max_quote_age_seconds"] != 30
            or plan["source_origin"] != ORIGIN
            or plan["purpose"] != "internal_research_paper_only"
            or plan["redistribution_authorized"] is not False
            or plan["live_trading_authorized"] is not False):
        raise ValueError("invalid_keyless_plan")
    _symbol(plan["symbol"])
    _time(plan["created_at"])


def capture_response(symbol: str, kind: str, *, clock=lambda: datetime.now(UTC)) -> dict:
    """Explicit fixed-origin GET. There are no URL, credential or broker inputs."""
    symbol = _symbol(symbol)
    if kind not in {"product", "ticker"}:
        raise ValueError("unsupported_keyless_capture")
    url = ORIGIN + "/products/" + symbol + ("/ticker" if kind == "ticker" else "")
    started = clock()
    _time(started.isoformat())
    raw = _get(url, timeout=8)
    received = clock()
    if received < started:
        raise ValueError("keyless_capture_clock_rollback")
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= MAX_RESPONSE_BYTES:
        raise ValueError("keyless_response_size_limit")
    try:
        body = raw.decode("utf-8")
    except UnicodeError:
        raise ValueError("invalid_keyless_response_encoding") from None
    return {"kind": kind, "url": url, "request_started_at": started.isoformat(),
            "received_at": received.isoformat(), "body_utf8": body,
            "body_sha256": hashlib.sha256(raw).hexdigest()}


def response_payload(record: object, plan: dict, kind: str) -> dict:
    if not isinstance(record, dict) or set(record) != {
        "kind", "url", "request_started_at", "received_at", "body_utf8", "body_sha256",
    }:
        raise ValueError("invalid_capture_record")
    expected = ORIGIN + "/products/" + plan["symbol"] + ("/ticker" if kind == "ticker" else "")
    if record["kind"] != kind or record["url"] != expected:
        raise ValueError("keyless_capture_source_mismatch")
    started, received = _time(record["request_started_at"]), _time(record["received_at"])
    if started < _time(plan["created_at"]) or received < started:
        raise ValueError("keyless_capture_clock_rollback")
    body = record["body_utf8"]
    if not isinstance(body, str) or not 1 <= len(body.encode()) <= MAX_RESPONSE_BYTES:
        raise ValueError("keyless_response_size_limit")
    if hashlib.sha256(body.encode()).hexdigest() != record["body_sha256"]:
        raise ValueError("keyless_response_hash_mismatch")
    try:
        payload = json.loads(body)
    except (ValueError, RecursionError):
        raise ValueError("invalid_keyless_response") from None
    if not isinstance(payload, dict):
        raise ValueError("invalid_keyless_response")
    return payload


def _positive(value: object):
    if not isinstance(value, str) or not 1 <= len(value) <= 64:
        raise ValueError("invalid_keyless_price")
    try:
        number = money(value)
    except ValueError:
        raise ValueError("invalid_keyless_price")
    return number


def validate_product(record: dict, plan: dict) -> dict:
    payload = response_payload(record, plan, "product")
    base, quote = plan["symbol"].split("-")
    if (payload.get("id") != plan["symbol"] or payload.get("base_currency") != base
            or payload.get("quote_currency") != quote or payload.get("status") != "online"
            or payload.get("trading_disabled") is not False):
        raise ValueError("keyless_product_identity_or_status_mismatch")
    for field in ("base_increment", "quote_increment"):
        _positive(payload.get(field))
    return {field: payload[field] for field in (
        "id", "base_currency", "quote_currency", "status", "base_increment", "quote_increment",
    )}


def quote_tick(record: dict, plan: dict) -> tuple[dict, int]:
    payload = response_payload(record, plan, "ticker")
    bid, ask = _positive(payload.get("bid")), _positive(payload.get("ask"))
    _positive(payload.get("price"))
    trade = payload.get("trade_id")
    if type(trade) is not int or not 0 <= trade < 2**63 or bid > ask:
        raise ValueError("invalid_keyless_trade_or_spread")
    received = _time(record["received_at"])
    event = _time(payload.get("time"))
    if not 0 <= (received - event).total_seconds() <= plan["max_quote_age_seconds"]:
        raise ValueError("stale_or_future_keyless_quote")
    tick = validate_feed_tick({
        "schema": FEED_TICK_SCHEMA, "symbol": plan["symbol"],
        "source": "coinbase-public-forward-capture", "event_time": event.isoformat(),
        # First client receipt is a conservative availability bound. Never
        # backdate availability to the vendor's historical event timestamp.
        "available_time": received.isoformat(), "received_time": received.isoformat(),
        "price": payload["price"],
    }, now=received, max_age_seconds_at_decision=plan["max_quote_age_seconds"])
    return tick, trade


def _derive(plan: dict, records: list[dict]) -> tuple[dict, list[dict], dict]:
    validate_plan(plan)
    if not isinstance(records, list) or len(records) != plan["samples"] + 1:
        raise ValueError("incomplete_keyless_capture")
    product = validate_product(records[0], plan)
    ticks, previous_trade, previous_event = [], -1, None
    previous_received = _time(records[0]["received_at"])
    for record in records[1:]:
        tick, trade = quote_tick(record, plan)
        started, received = _time(record["request_started_at"]), _time(record["received_at"])
        event = _time(tick["event_time"])
        if (started < previous_received or received <= previous_received
                or trade <= previous_trade or (previous_event and event <= previous_event)):
            raise ValueError("keyless_quote_replay_or_clock_rollback")
        ticks.append(tick)
        previous_received, previous_trade, previous_event = received, trade, event
    reports = {}
    for cost in plan["cost_grid_bps"]:
        reports[str(cost)] = run_walk_forward(
            ticks, symbol=plan["symbol"], now=previous_received,
            model_factory=lambda: MomentumReferenceModel(window=4, scale=0.5),
            cost_bps_per_unit_turnover=cost, **plan["walk_forward"],
        )
    return product, ticks, reports


def _fit_baseline(ticks: list[dict], plan: dict) -> dict:
    prices = [float(tick["price"]) for tick in ticks]
    rows = []
    for i in range(3, len(ticks) - 1):
        known = int(_time(ticks[i]["available_time"]).timestamp() * 1000)
        rows.append({
            "event_ms": known, "available_ms": known,
            "label_available_ms": int(_time(ticks[i + 1]["available_time"]).timestamp() * 1000),
            "features": [prices[i - lag] / prices[i - lag - 1] - 1 for lag in range(3)],
            "target": prices[i + 1] / prices[i] - 1,
        })
    # The last training label arrives at the first holdout decision. Purge that
    # row: every fitted label must have arrived strictly before holdout starts.
    split = len(rows) - plan["fitted_baseline"]["holdout"]
    rows = rows[:split - 1] + rows[split:]
    with tempfile.TemporaryDirectory(prefix="osiris-keyless-model-") as folder:
        model = ModelService(Database(str(Path(folder) / "research.sqlite3"))).train(
            "keyless-ridge-v1", rows, holdout=4, ridge=0.001,
            feature_names=["last_return", "previous_return", "third_return"],
        )
    return {"rows": rows, "rows_sha256": digest(rows), "model": model,
            "purged_boundary_rows": 1, "target": "next_observed_trade_return",
            "independent_model_approval": False}


def build_archive(plan: dict, records: list[dict], *, frozen_plan_sha256: str) -> dict:
    if digest(plan) != frozen_plan_sha256:
        raise ValueError("keyless_plan_changed_after_capture_started")
    product, ticks, reports = _derive(plan, records)
    spreads = []
    for record in records[1:]:
        payload = response_payload(record, plan, "ticker")
        bid, ask = _positive(payload["bid"]), _positive(payload["ask"])
        spreads.append(float((ask - bid) / ((ask + bid) / 2) * 10_000))
    report = reports["10"]
    governance = governance_record(
        model_id=plan["model"]["id"], model_version=plan["model"]["version"],
        params={"window": 4, "scale": 0.5}, report=report,
    )
    artifact = {
        "schema": SCHEMA, "plan": plan, "plan_sha256": frozen_plan_sha256,
        "captures": records, "product": product, "ticks": ticks,
        "data_sha256": ticks_digest(ticks), "cost_sensitivity": reports,
        "fitted_baseline": _fit_baseline(ticks, plan),
        "observed_quote_spread": {
            "samples": len(spreads), "min_bps": min(spreads), "median_bps": median(spreads),
            "max_bps": max(spreads), "realized_execution_cost": False,
        },
        "governance": governance, "code_files_sha256": source_manifest(),
        "runtime": {"python": platform.python_version(), "implementation": platform.python_implementation(),
                    "numpy": np.__version__},
        "source_mode": "public_https_forward_capture", "keyless": True,
        "broker_order_submitted": False, "live_trading_authorized": False,
        "independent_model_reviewed": False, "source_origin_independently_attested": False,
        "vendor_rights_verified": False, "release_gate_accepted": False,
        "limitations": [
            "Short forward quote sample; no calibrated profitable model or independent blinded holdout.",
            "Client receipt is conservative availability, not vendor publication-time attestation.",
            "Polling is not an incremental depth stream or venue fill/queue/margin simulation.",
            "Costs are fixed sensitivity assumptions, not measured execution calibration.",
            "Original public response bytes are retained for internal research, not redistribution permission.",
            "Hashes check local consistency; no independent host, source or review authenticity is established.",
        ],
    }
    artifact["archive_sha256"] = digest(artifact)
    return artifact


def verify_archive(artifact: object, *, expected_commit: str, expected_sha256: str) -> dict:
    if not isinstance(artifact, dict):
        raise ValueError("invalid_keyless_archive")
    sealed = {key: value for key, value in artifact.items() if key != "archive_sha256"}
    if (artifact.get("archive_sha256") != digest(sealed)
            or artifact.get("archive_sha256") != expected_sha256):
        raise ValueError("keyless_archive_hash_mismatch")
    plan = artifact.get("plan")
    validate_plan(plan)
    if plan["candidate_commit"] != expected_commit:
        raise ValueError("keyless_archive_commit_mismatch")
    rebuilt = build_archive(plan, artifact.get("captures"), frozen_plan_sha256=artifact.get("plan_sha256"))
    if artifact != rebuilt:
        raise ValueError("keyless_archive_reproduction_mismatch")
    return {"consistent": True, "reproduced": True, "samples": len(artifact["ticks"]),
            "folds": artifact["cost_sensitivity"]["10"]["aggregate"]["folds"],
            "archive_sha256": expected_sha256, "source_origin_independently_attested": False,
            "independent_model_reviewed": False, "vendor_rights_verified": False,
            "release_gate_accepted": False, "live_trading_authorized": False}


def read_archive(path: Path) -> dict:
    if path.is_symlink() or not path.is_file() or not 1 <= path.stat().st_size <= MAX_ARCHIVE_BYTES:
        raise ValueError("invalid_keyless_archive_file")
    try:
        value = json.loads(path.read_bytes())
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError("invalid_keyless_archive_json") from None
    return value
