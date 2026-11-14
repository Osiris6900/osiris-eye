"""Explicit keyless BLS monthly macro captures with conservative availability.

The period endpoint is a statistical reference date, never a claimed release
timestamp. Original HTTP bytes are retained before parsing. No default polling,
model approval, source-rights certification or order routing is supplied here.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .data_store import ResearchStore
from .feed_admission import admit_feed

PROVIDER = "bls-public-monthly"
DATASET = "macro_bls_monthly"
ENDPOINT = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
SOURCE_TERMS_URL = "https://www.bls.gov/bls/linksite.htm"
SERIES = {
    "CUUR0000SA0": {
        "name": "US all-items CPI",
        "unit": "index_1982_1984_100",
        "seasonal_adjustment": "not_seasonally_adjusted",
    },
    "LNS14000000": {
        "name": "US civilian unemployment rate",
        "unit": "percent",
        "seasonal_adjustment": "seasonally_adjusted",
    },
    "CES0000000001": {
        "name": "US total nonfarm payroll employment",
        "unit": "thousand_persons",
        "seasonal_adjustment": "seasonally_adjusted",
    },
}
MAX_RESPONSE_BYTES = 2_097_152
ROOT = Path(__file__).resolve().parents[4]
Transport = Callable[[bytes, float], tuple[int, bytes]]


def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("bls_time_requires_timezone")
    return value.astimezone(UTC)


def _manifest() -> dict[str, str]:
    names = (
        "services/quant-api/src/osiris_api/macro_economic_watch.py",
        "scripts/record_bls_macro.py",
    )
    return {name: _digest((ROOT / name).read_bytes()) for name in names}


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _http_post(body: bytes, timeout: float) -> tuple[int, bytes]:
    request = Request(
        ENDPOINT,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Osiris-Eye/0.3 macro-research",
        },
    )
    try:
        with build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
            return response.status, response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as error:
        # Preserve bounded refused/redirect response bytes without following it.
        return error.code, error.read(MAX_RESPONSE_BYTES + 1)
    except (URLError, TimeoutError, OSError):
        raise ValueError("bls_request_unavailable") from None


def _write(path: Path, value, *, raw=False) -> None:
    with path.open("xb") as file:
        os.chmod(path, 0o600)
        file.write(value if raw else _canonical(value) + b"\n")


def _read(path: Path, limit=MAX_RESPONSE_BYTES) -> bytes:
    with path.open("rb") as file:
        raw = file.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("bls_evidence_file_too_large")
    return raw


def _json(raw: bytes):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("bls_duplicate_json_key")
            result[key] = value
        return result

    return json.loads(raw, object_pairs_hook=unique)


def _plan(candidate_commit: str, series, started: datetime) -> dict:
    if not isinstance(candidate_commit, str) or not re.fullmatch("[0-9a-f]{40}", candidate_commit):
        raise ValueError("bls_candidate_commit_required")
    if (
        not isinstance(series, (list, tuple))
        or not 1 <= len(series) <= len(SERIES)
        or any(not isinstance(s, str) or s not in SERIES for s in series)
        or len(set(series)) != len(series)
    ):
        raise ValueError("bls_explicit_supported_series_required")
    return {
        "schema": "osiris.bls-capture-plan.v1",
        "candidate_commit": candidate_commit,
        "series": sorted(series),
        "start_year": started.year - 1,
        "end_year": started.year,
        "requested_at": started.isoformat(),
        "source_url": ENDPOINT,
        "source_terms_url": SOURCE_TERMS_URL,
        "max_latest_period_lag_months": 3,
        "code_files_sha256": _manifest(),
    }


def _request_body(plan) -> bytes:
    return _canonical(
        {
            "seriesid": plan["series"],
            "startyear": str(plan["start_year"]),
            "endyear": str(plan["end_year"]),
        }
    )


def _normalize(raw: bytes, plan: dict, received: datetime) -> list[dict]:
    try:
        data = _json(raw)
    except (ValueError, UnicodeError):
        raise ValueError("bls_response_not_json") from None
    if not isinstance(data, dict) or data.get("status") != "REQUEST_SUCCEEDED":
        raise ValueError("bls_request_not_succeeded")
    result = data.get("Results")
    series = result.get("series") if isinstance(result, dict) else None
    if (
        not isinstance(series, list)
        or len(series) != len(plan["series"])
        or any(not isinstance(s, dict) or not isinstance(s.get("seriesID"), str) for s in series)
        or sorted(s["seriesID"] for s in series) != plan["series"]
    ):
        raise ValueError("bls_series_mismatch")
    observations = []
    for group in series:
        series_id = group["seriesID"]
        rows = group.get("data")
        if not isinstance(rows, list) or not 1 <= len(rows) <= 26:
            raise ValueError("bls_month_count_invalid")
        periods, monthly, latest = set(), set(), None
        for row in rows:
            if not isinstance(row, dict):
                raise TypeError("bls_month_invalid")
            year, period = row.get("year"), row.get("period")
            if (
                not isinstance(year, str)
                or not re.fullmatch("[0-9]{4}", year)
                or not plan["start_year"] <= int(year) <= plan["end_year"]
                or not isinstance(period, str)
                or not re.fullmatch(r"M(0[1-9]|1[0-3])", period)
            ):
                raise ValueError("bls_month_invalid")
            if (year, period) in periods:
                raise ValueError("bls_duplicate_period")
            periods.add((year, period))
            if period == "M13":
                continue  # Annual averages are not monthly releases.
            month = int(period[1:])
            monthly.add(int(year) * 12 + month - 1)
            reference = datetime(int(year) + (month == 12), month % 12 + 1, 1, tzinfo=UTC)
            if reference > received:
                raise ValueError("bls_future_reference_period")
            latest = max(latest or reference, reference)
            value = row.get("value")
            if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,12}(\.[0-9]{1,6})?", value):
                raise ValueError("bls_value_invalid")
            amount = Decimal(value)
            if (series_id == "LNS14000000" and amount > 100) or (
                series_id != "LNS14000000" and amount <= 0
            ):
                raise ValueError("bls_value_out_of_range")
            value = format(amount, "f").rstrip("0").rstrip(".") if "." in value else value
            notes = row.get("footnotes")
            if not isinstance(notes, list) or len(notes) > 5:
                raise ValueError("bls_footnotes_invalid")
            footnotes = []
            for note in notes:
                if not isinstance(note, dict):
                    raise TypeError("bls_footnotes_invalid")
                normalized = {}
                for field, bound in (("code", 16), ("text", 512)):
                    item = note.get(field)
                    if item is not None:
                        if (
                            not isinstance(item, str)
                            or len(item) > bound
                            or any(ord(c) < 32 for c in item)
                        ):
                            raise ValueError("bls_footnotes_invalid")
                        if item:
                            normalized[field] = item
                if normalized:
                    footnotes.append(normalized)
            semantic = {
                "series_id": series_id,
                "year": year,
                "period": period,
                "value": value,
                "footnotes": footnotes,
                **SERIES[series_id],
            }
            observations.append(
                {
                    "record_key": series_id
                    + ":"
                    + year
                    + ":"
                    + period
                    + ":"
                    + _digest(_canonical(semantic)),
                    "instrument": "MACRO:BLS:" + series_id,
                    "source": PROVIDER,
                    "event_time": reference.isoformat(),
                    "available_at": received.isoformat(),
                    "payload": {
                        **semantic,
                        "reference_time_basis": "period_end_exclusive_not_release_time",
                        "source_url": ENDPOINT,
                        "response_sha256": _digest(raw),
                    },
                }
            )
        if (
            latest is None
            or (received.year * 12 + received.month) - (latest.year * 12 + latest.month) > 3
        ):
            raise ValueError("bls_latest_period_stale")
        if monthly != set(range(plan["start_year"] * 12, max(monthly) + 1)):
            raise ValueError("bls_monthly_history_gap")
    return sorted(observations, key=lambda item: item["record_key"])


def capture_bls(
    directory: Path,
    *,
    candidate_commit: str,
    series,
    transport: Transport = _http_post,
    clock=lambda: datetime.now(UTC),
    timeout_seconds=10,
    source_guard: Callable[[], bool] | None = None,
) -> dict:
    if (
        type(timeout_seconds) not in (int, float)
        or not math.isfinite(timeout_seconds)
        or not 1 <= timeout_seconds <= 30
    ):
        raise ValueError("bls_timeout_invalid")
    started = _utc(clock())
    plan = _plan(candidate_commit, series, started)
    directory = Path(directory)
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    body = _request_body(plan)
    _write(directory / "plan.json", plan)
    _write(directory / "request.json", body, raw=True)
    result = {
        "schema": "osiris.bls-capture.v1",
        "passed": False,
        "candidate_commit": candidate_commit,
        "plan_sha256": _digest(_canonical(plan)),
        "request_sha256": _digest(body),
        "source_url": ENDPOINT,
        "vendor_rights_verified": False,
        "release_gate_accepted": False,
        "live_trading_authorized": False,
    }
    try:
        status, raw = transport(body, timeout_seconds)
        received = _utc(clock())
        if not isinstance(raw, bytes) or len(raw) > MAX_RESPONSE_BYTES:
            raise ValueError("bls_response_too_large")
        _write(directory / "response.bin", raw, raw=True)
        result.update(
            http_status=status, response_sha256=_digest(raw), received_at=received.isoformat()
        )
        if type(status) is not int or status != 200:
            raise ValueError("bls_http_refused")
        if received < started:
            raise ValueError("bls_clock_rollback")
        observations = _normalize(raw, plan, received)
        if (
            plan["code_files_sha256"] != _manifest()
            or source_guard is not None
            and source_guard() is not True
        ):
            raise ValueError("bls_source_changed_during_capture")
        _write(directory / "observations.json", observations)
        result.update(passed=True, observations=len(observations), series=len(plan["series"]))
    except (OSError, ValueError, TypeError) as error:
        result["error"] = {"type": type(error).__name__, "message": str(error)}
    _write(directory / "capture.json", result)
    return result


def verify_bls_capture(
    directory: Path, *, expected_commit: str, expected_response_sha256: str
) -> dict:
    directory = Path(directory)
    if not isinstance(expected_response_sha256, str) or not re.fullmatch(
        "[0-9a-f]{64}", expected_response_sha256
    ):
        raise ValueError("bls_trusted_response_digest_required")
    plan = _json(_read(directory / "plan.json"))
    result = _json(_read(directory / "capture.json"))
    started = _utc(datetime.fromisoformat(plan["requested_at"]))
    if plan != _plan(expected_commit, plan["series"], started):
        raise ValueError("bls_plan_or_source_mismatch")
    raw = _read(directory / "response.bin")
    if len(raw) > MAX_RESPONSE_BYTES or _digest(raw) != expected_response_sha256:
        raise ValueError("bls_response_digest_mismatch")
    received = _utc(datetime.fromisoformat(result["received_at"]))
    if (
        result.get("passed") is not True
        or result.get("http_status") != 200
        or received < started
        or result.get("candidate_commit") != expected_commit
        or result.get("plan_sha256") != _digest(_canonical(plan))
        or result.get("response_sha256") != expected_response_sha256
        or result.get("request_sha256") != _digest(_request_body(plan))
        or _read(directory / "request.json") != _request_body(plan)
        or any(
            result.get(k) is not False
            for k in ("vendor_rights_verified", "release_gate_accepted", "live_trading_authorized")
        )
    ):
        raise ValueError("bls_capture_binding_mismatch")
    observations = _normalize(raw, plan, received)
    if (
        observations != _json(_read(directory / "observations.json"))
        or result.get("observations") != len(observations)
        or result.get("series") != len(plan["series"])
    ):
        raise ValueError("bls_normalization_mismatch")
    return {
        "passed": True,
        "candidate_commit": expected_commit,
        "observations": len(observations),
        "response_sha256": expected_response_sha256,
        "vendor_rights_verified": False,
        "release_gate_accepted": False,
        "live_trading_authorized": False,
    }


def preflight_bls_ingestion(
    directory: Path,
    *,
    expected_commit: str,
    expected_response_sha256: str,
    policy: dict,
    now: datetime,
    max_age_seconds: float = 300,
) -> tuple[dict, list[dict]]:
    """Validate all evidence and policies before an ingestion database is opened."""
    if os.getenv("OSIRIS_P2P_INGEST_ENABLED") != "1":
        raise ValueError("ingestion_disabled_kill_switch")
    if (
        type(max_age_seconds) not in (int, float)
        or not math.isfinite(max_age_seconds)
        or not 0 < max_age_seconds <= 86_400
    ):
        raise ValueError("bls_freshness_bound_invalid")
    verified = verify_bls_capture(
        directory,
        expected_commit=expected_commit,
        expected_response_sha256=expected_response_sha256,
    )
    rows = _json(_read(Path(directory) / "observations.json"))
    current = _utc(now)
    for row in rows:
        age = (current - datetime.fromisoformat(row["available_at"])).total_seconds()
        if not 0 <= age <= max_age_seconds:
            raise ValueError("bls_capture_stale_or_future")
        admit_feed(provider=PROVIDER, instrument=row["instrument"], policy=policy, now=current)
    return verified, rows


def ingest_bls_capture(
    store: ResearchStore,
    directory: Path,
    *,
    expected_commit: str,
    expected_response_sha256: str,
    policy: dict,
    now: datetime,
    max_age_seconds: float = 300,
) -> dict:
    """Admit a vintage; unchanged data retains its first actual availability.

    Revised values/footnotes append immutable keys rather than rewriting history.
    """
    verified, rows = preflight_bls_ingestion(
        directory,
        expected_commit=expected_commit,
        expected_response_sha256=expected_response_sha256,
        policy=policy,
        now=now,
        max_age_seconds=max_age_seconds,
    )
    added = duplicates = revisions = 0
    with store.transaction() as connection:
        for row in rows:
            prefix = row["record_key"].rsplit(":", 1)[0] + ":%"
            semantic = row["record_key"].rsplit(":", 1)[1]
            prior = connection.execute(
                "SELECT record_key,available_at,payload_json FROM observations WHERE dataset=? AND source=? AND record_key LIKE ? ORDER BY id DESC LIMIT 1",
                (DATASET, PROVIDER, prefix),
            ).fetchone()
            if prior and row["available_at"] < prior["available_at"]:
                raise ValueError("bls_vintage_time_rollback")
            if prior and json.loads(prior["payload_json"])["semantic_sha256"] == semantic:
                duplicates += 1
                continue
            if prior and row["available_at"] == prior["available_at"]:
                raise ValueError("bls_same_vintage_conflict")
            payload = {
                **row["payload"],
                "instrument": row["instrument"],
                "semantic_sha256": semantic,
                "source_policy": "operator_attestation_not_independent_rights_verification",
            }
            record_key = row["record_key"]
            if prior:
                payload["supersedes_record_key"] = prior["record_key"]
                # A later revision may return to a previously published value.
                # Retain that new vintage instead of deduplicating it against
                # an older, non-current occurrence of the same statistic.
                record_key += (
                    ":"
                    + _digest(
                        _canonical(
                            {
                                "available_at": row["available_at"],
                                "response_sha256": row["payload"]["response_sha256"],
                            }
                        )
                    )[:16]
                )
                revisions += 1
            store._put_in_transaction(
                connection,
                dataset=DATASET,
                record_key=record_key,
                source=PROVIDER,
                event_time=row["event_time"],
                available_at=row["available_at"],
                payload=payload,
            )
            added += 1
    return {**verified, "ingested": added, "duplicates_skipped": duplicates, "revisions": revisions}
