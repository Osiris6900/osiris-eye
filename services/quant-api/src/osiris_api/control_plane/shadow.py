"""Isolated, persistent long/flat shadow strategy comparison; no broker calls."""

from __future__ import annotations

import json
import re
from decimal import Decimal
from itertools import pairwise

from .broker import money
from .database import Database, canonical, digest, identifier

RUNS = "shadow-runs"
COMPARISONS = "shadow-comparisons"
RUN_POLICY = "lagged-long-flat-independent-risk-v1"
COMPARISON_POLICY = "event-aligned-paper-equity-v1"
MAX_COMPARISON_ROWS = 2000


def _sealed(value: object, hash_field: str, error: str) -> dict:
    if not isinstance(value, dict) or hash_field not in value:
        raise ValueError(error)
    body = dict(value)
    expected = body.pop(hash_field)
    if expected != digest(body):
        raise ValueError(error)
    return body


def _sha256(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _decimal_text(value: Decimal) -> str:
    return "0" if value == 0 else format(value.normalize(), "f")


def _normalized_ticks(ticks: object) -> list[dict]:
    if not isinstance(ticks, list) or not 2 <= len(ticks) <= 10000:
        raise ValueError("invalid_shadow_configuration")
    normalized = []
    previous_time = -1
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
        normalized.append(
            {"event_ms": stamp, "price": _decimal_text(money(tick["price"])), "target": target}
        )
        previous_time = stamp
    return normalized


def _validated_run(value: object, run_id: str) -> dict:
    body = _sealed(value, "shadow_run_sha256", "shadow_run_digest_mismatch")
    try:
        configuration = body["configuration"]
        if set(configuration) != {
            "initial_cash",
            "max_order_amount",
            "daily_loss_limit",
            "fee_rate",
        }:
            raise ValueError("invalid_shadow_run")
        initial = money(configuration["initial_cash"])
        money(configuration["max_order_amount"])
        money(configuration["daily_loss_limit"])
        fee_rate = money(configuration["fee_rate"], positive=False)
        equity = body["equity"]
        event_times = body["event_times_ms"]
        if (
            body["run_id"] != run_id
            or body["policy"] != RUN_POLICY
            or not _sha256(body["input_sha256"])
            or not 0 <= fee_rate <= Decimal("0.1")
            or not isinstance(equity, list)
            or not isinstance(event_times, list)
            or not 2 <= len(equity) <= 10000
            or len(event_times) != len(equity)
            or any(type(stamp) is not int or stamp < 0 for stamp in event_times)
            or any(later <= earlier for earlier, later in pairwise(event_times))
            or any(money(item, positive=False) is None for item in equity)
            or money(body["initial_equity"]) != initial
            or money(body["final_equity"], positive=False) != money(equity[-1], positive=False)
            or money(body["pnl"], positive=False) != money(equity[-1], positive=False) - initial
            or money(body["cash"], positive=False) < 0
            or money(body["quantity"], positive=False) < 0
            or money(body["fees"], positive=False) < 0
            or not isinstance(body["fills"], list)
            or type(body["risk_halted"]) is not bool
            or body["signal_alignment"] != "previous_tick_target"
            or type(body["broker_orders"]) is not int
            or body["broker_orders"] != 0
            or body["live_execution"] is not False
        ):
            raise ValueError("invalid_shadow_run")
    except (KeyError, TypeError, ValueError, ArithmeticError):
        raise ValueError("invalid_shadow_run") from None
    return value


def _comparison_request(payload: object) -> dict:
    required = {"run_id", "comparison_as_of_ms", "paper_observations"}
    if not isinstance(payload, dict) or set(payload) != required:
        raise ValueError("invalid_shadow_comparison_payload")
    try:
        request = {
            "run_id": identifier(payload["run_id"]),
            "comparison_as_of_ms": payload["comparison_as_of_ms"],
            "paper_observations": payload["paper_observations"],
        }
    except (TypeError, ValueError):
        raise ValueError("invalid_shadow_comparison_payload") from None
    if type(request["comparison_as_of_ms"]) is not int or request["comparison_as_of_ms"] < 0:
        raise ValueError("invalid_comparison_time")
    observations = request["paper_observations"]
    if not isinstance(observations, list) or not 2 <= len(observations) <= MAX_COMPARISON_ROWS:
        raise ValueError("invalid_paper_observations")
    normalized = []
    for observation in observations:
        if (
            not isinstance(observation, dict)
            or set(observation) != {"event_ms", "available_ms", "equity", "source_sha256"}
            or type(observation["event_ms"]) is not int
            or observation["event_ms"] < 0
            or type(observation["available_ms"]) is not int
            or observation["available_ms"] < observation["event_ms"]
            or observation["available_ms"] > request["comparison_as_of_ms"]
            or not _sha256(observation["source_sha256"])
        ):
            raise ValueError("invalid_paper_observations")
        try:
            equity = money(observation["equity"], positive=False)
        except ValueError:
            raise ValueError("invalid_paper_observations") from None
        normalized.append(
            {
                "event_ms": observation["event_ms"],
                "available_ms": observation["available_ms"],
                "equity": _decimal_text(equity),
                "source_sha256": observation["source_sha256"],
            }
        )
    request["paper_observations"] = normalized
    canonical(request)
    return request


def _comparison_fingerprint(comparison_id: str, actor: str, request: dict) -> str:
    return digest({"comparison_id": comparison_id, "actor": actor, "request": request})


def _validated_comparison(value: object, comparison_id: str) -> dict:
    body = _sealed(value, "comparison_sha256", "shadow_comparison_digest_mismatch")
    try:
        request = _comparison_request(body["request"])
        rows = body["rows"]
        observations = request["paper_observations"]
        if (
            body["comparison_id"] != comparison_id
            or body["actor"] != identifier(body["actor"])
            or body["input_sha256"]
            != _comparison_fingerprint(comparison_id, body["actor"], request)
            or body["policy"] != COMPARISON_POLICY
            or body["run_id"] != request["run_id"]
            or not _sha256(body["shadow_run_sha256"])
            or not isinstance(rows, list)
            or len(rows) != len(observations)
            or body["paper_evidence"] != "operator_supplied"
            or body["independent_paper_verification"] is not False
            or body["execution_authorized"] is not False
            or body["live_execution"] is not False
        ):
            raise ValueError("invalid_shadow_comparison")
        differences = []
        for row, observation in zip(rows, observations):
            if set(row) != {"event_ms", "shadow_equity", "paper_equity", "difference"}:
                raise ValueError("invalid_shadow_comparison")
            shadow_equity = money(row["shadow_equity"], positive=False)
            paper_equity = money(row["paper_equity"], positive=False)
            difference = money(row["difference"], positive=False)
            if (
                row["event_ms"] != observation["event_ms"]
                or paper_equity != money(observation["equity"], positive=False)
                or difference != shadow_equity - paper_equity
            ):
                raise ValueError("invalid_shadow_comparison")
            differences.append(difference)
        initial = money(body["initial_equity"])
        final_shadow = money(body["final_shadow_equity"], positive=False)
        final_paper = money(body["final_paper_equity"], positive=False)
        if (
            rows[0]["event_ms"] != observations[0]["event_ms"]
            or money(rows[-1]["shadow_equity"], positive=False) != final_shadow
            or money(rows[-1]["paper_equity"], positive=False) != final_paper
            or money(body["shadow_pnl"], positive=False) != final_shadow - initial
            or money(body["paper_pnl"], positive=False) != final_paper - initial
            or money(body["final_difference"], positive=False) != final_shadow - final_paper
            or money(body["max_absolute_difference"], positive=False)
            != max(abs(difference) for difference in differences)
        ):
            raise ValueError("invalid_shadow_comparison")
    except (KeyError, TypeError, ValueError, ArithmeticError):
        raise ValueError("invalid_shadow_comparison") from None
    return value


class ShadowService:
    def __init__(self, db: Database):
        self.db = db

    def get_run(self, run_id: str) -> dict | None:
        run_id = identifier(run_id)
        item = self.db.get(RUNS, run_id)
        return None if item is None else _validated_run(item["value"], run_id)

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
        if not 0 <= fee_rate <= Decimal("0.1"):
            raise ValueError("invalid_shadow_configuration")
        normalized_ticks = _normalized_ticks(ticks)
        fingerprint = digest(
            {
                "ticks": normalized_ticks,
                "cash": _decimal_text(cash),
                "cap": _decimal_text(cap),
                "stop": _decimal_text(stop),
                "fee": _decimal_text(fee_rate),
            }
        )
        old = self.get_run(run_id)
        if old is not None:
            if old["input_sha256"] != fingerprint:
                raise ValueError("shadow_run_id_conflict")
            return old
        initial, quantity, fees = cash, Decimal(0), Decimal(0)
        pending_target, fills, equity, halted = 0, [], [], False
        for tick in normalized_ticks:
            stamp, target = tick["event_ms"], tick["target"]
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
            pending_target = target
        result = {
            "run_id": run_id,
            "input_sha256": fingerprint,
            "policy": RUN_POLICY,
            "configuration": {
                "initial_cash": _decimal_text(initial),
                "max_order_amount": _decimal_text(cap),
                "daily_loss_limit": _decimal_text(stop),
                "fee_rate": _decimal_text(fee_rate),
            },
            "event_times_ms": [tick["event_ms"] for tick in normalized_ticks],
            "initial_equity": _decimal_text(initial),
            "final_equity": equity[-1],
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
        result["shadow_run_sha256"] = digest(result)
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT body FROM cp_documents WHERE collection=? AND id=?", (RUNS, run_id)
            ).fetchone()
            if row is not None:
                stored = _validated_run(json.loads(row["body"]), run_id)
                if stored["input_sha256"] != fingerprint:
                    raise ValueError("shadow_run_id_conflict")
                return stored
            conn.execute(
                "INSERT INTO cp_documents VALUES (?,?,?,?)",
                (RUNS, run_id, 1, canonical(result).decode()),
            )
        return result

    def compare(self, run_id: str, paper_equity: list[str]) -> dict:
        run_id = identifier(run_id)
        run = self.get_run(run_id)
        if (
            run is None
            or not isinstance(paper_equity, list)
            or len(paper_equity) != len(run["equity"])
        ):
            raise ValueError("aligned_paper_equity_required")
        differences = [
            money(a, positive=False) - money(b, positive=False)
            for a, b in zip(run["equity"], paper_equity)
        ]
        return {
            "run_id": run_id,
            "equity_differences": [str(d) for d in differences],
            "max_absolute_difference": str(max(abs(d) for d in differences)),
            "execution_authorized": False,
        }

    def get_comparison(self, comparison_id: str) -> dict | None:
        comparison_id = identifier(comparison_id)
        item = self.db.get(COMPARISONS, comparison_id)
        return None if item is None else _validated_comparison(item["value"], comparison_id)

    def create_comparison(self, comparison_id: str, actor: str, payload: object) -> dict:
        comparison_id, actor = identifier(comparison_id), identifier(actor)
        request = _comparison_request(payload)
        input_sha256 = _comparison_fingerprint(comparison_id, actor, request)
        old = self.get_comparison(comparison_id)
        if old is not None:
            if old["input_sha256"] != input_sha256:
                raise ValueError("shadow_comparison_id_conflict")
            return {"comparison": old, "replayed": True, "live_execution": False}
        run = self.get_run(request["run_id"])
        if run is None:
            raise ValueError("shadow_run_not_found")
        observations = request["paper_observations"]
        if run["event_times_ms"] != [row["event_ms"] for row in observations]:
            raise ValueError("paper_event_alignment_mismatch")
        if run["event_times_ms"][-1] > request["comparison_as_of_ms"]:
            raise ValueError("shadow_run_not_available")
        initial = money(run["initial_equity"])
        if money(observations[0]["equity"], positive=False) != initial:
            raise ValueError("paper_initial_equity_mismatch")
        rows = []
        differences = []
        for shadow_equity, observation in zip(run["equity"], observations):
            shadow = money(shadow_equity, positive=False)
            paper = money(observation["equity"], positive=False)
            difference = shadow - paper
            differences.append(difference)
            rows.append(
                {
                    "event_ms": observation["event_ms"],
                    "shadow_equity": _decimal_text(shadow),
                    "paper_equity": _decimal_text(paper),
                    "difference": _decimal_text(difference),
                }
            )
        final_shadow = money(run["final_equity"], positive=False)
        final_paper = money(observations[-1]["equity"], positive=False)
        comparison = {
            "comparison_id": comparison_id,
            "actor": actor,
            "input_sha256": input_sha256,
            "policy": COMPARISON_POLICY,
            "request": request,
            "run_id": request["run_id"],
            "shadow_run_sha256": run["shadow_run_sha256"],
            "rows": rows,
            "initial_equity": _decimal_text(initial),
            "final_shadow_equity": _decimal_text(final_shadow),
            "final_paper_equity": _decimal_text(final_paper),
            "shadow_pnl": _decimal_text(final_shadow - initial),
            "paper_pnl": _decimal_text(final_paper - initial),
            "final_difference": _decimal_text(final_shadow - final_paper),
            "max_absolute_difference": _decimal_text(max(abs(value) for value in differences)),
            "paper_evidence": "operator_supplied",
            "independent_paper_verification": False,
            "execution_authorized": False,
            "live_execution": False,
        }
        comparison["comparison_sha256"] = digest(comparison)
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT body FROM cp_documents WHERE collection=? AND id=?",
                (COMPARISONS, comparison_id),
            ).fetchone()
            if row is not None:
                stored = _validated_comparison(json.loads(row["body"]), comparison_id)
                if stored["input_sha256"] != input_sha256:
                    raise ValueError("shadow_comparison_id_conflict")
                return {"comparison": stored, "replayed": True, "live_execution": False}
            conn.execute(
                "INSERT INTO cp_documents VALUES (?,?,?,?)",
                (COMPARISONS, comparison_id, 1, canonical(comparison).decode()),
            )
        return {"comparison": comparison, "replayed": False, "live_execution": False}
