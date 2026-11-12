"""Source revocation, conservative availability and immutable research views."""

import base64
import json
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from osiris_api.control_plane.api import router
from osiris_api.control_plane.database import Database
from osiris_api.control_plane.identity import totp
from osiris_api.control_plane.market_views import MarketViews, regime_diagnostic

NOW = datetime(2026, 11, 12, 12, tzinfo=UTC)


def sources():
    return {
        "fixture": {
            "mode": "synthetic",
            "rights_reference": "offline-only",
            "expires_at": (NOW + timedelta(days=1)).isoformat(),
            "kinds": ["options", "news"],
            "entities": ["NIFTY", "GLOBAL"],
            "max_age_seconds": 300,
        }
    }


def record(kind="options", key="record-1"):
    payload = (
        {
            "underlying": "NIFTY",
            "expiry": "2026-11-19",
            "currency": "INR",
            "contracts": [
                {
                    "symbol": "NIFTY-24000-CALL",
                    "option_type": "CALL",
                    "strike": "24000",
                    "bid": "100",
                    "ask": "101",
                    "volume": 10,
                    "open_interest": 50,
                },
            ],
        }
        if kind == "options"
        else {
            "title": "Offline research fixture headline",
            "url": "https://example.invalid/news/1",
            "symbols": ["NIFTY"],
            "sentiment": None,
        }
    )
    return {
        "id": key,
        "source": "fixture",
        "kind": kind,
        "entity": "NIFTY" if kind == "options" else "GLOBAL",
        "event_time": (NOW - timedelta(seconds=10)).isoformat(),
        "available_at": (NOW - timedelta(seconds=5)).isoformat(),
        "payload": payload,
    }


@pytest.fixture
def service(tmp_path):
    return MarketViews(Database(str(tmp_path / "control.db")), sources(), clock=lambda: NOW)


@pytest.mark.parametrize("kind", ["options", "news"])
def test_old_source_availability_cannot_backdate_actual_intake(service, kind):
    request = record(kind)
    item = service.ingest(request)["record"]
    assert item["available_at"] == item["received_at"] == NOW.isoformat()
    assert item["source_available_at"] == request["available_at"]
    assert (
        service.read(kind, request["entity"], as_of=(NOW - timedelta(microseconds=1)).isoformat())[
            "records"
        ]
        == []
    )
    visible = service.read(kind, request["entity"])
    assert visible["status"] == "available"
    assert visible["records"][0]["source_mode"] == "synthetic"
    assert visible["rights_independently_verified"] is False
    assert item["live_execution"] is False


def test_identical_retry_and_content_dedup_are_immutable_and_concurrent(service):
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: service.ingest(record()), range(8)))
    assert sum(item["duplicate"] is False for item in results) == 1
    duplicate = service.ingest(record(key="second-id"))
    assert duplicate["duplicate"] is True
    assert duplicate["record"]["id"] == "record-1"
    changed = record()
    changed["payload"]["contracts"][0]["bid"] = "99"
    with pytest.raises(ValueError, match="immutable_market_view_conflict"):
        service.ingest(changed)


@pytest.mark.parametrize("change", ["revoked", "expired", "scope", "reference", "mode"])
def test_rights_revocation_and_changed_policy_hide_old_records(service, change):
    service.ingest(record())
    if change == "revoked":
        service.sources.clear()
    elif change == "expired":
        service.sources["fixture"]["expires_at"] = NOW.isoformat()
    elif change == "scope":
        service.sources["fixture"]["entities"] = ["OTHER"]
    elif change == "reference":
        service.sources["fixture"]["rights_reference"] = "new-contract"
    else:
        service.sources["fixture"]["mode"] = "operator_licensed"
    assert service.read("options", "NIFTY")["records"] == []


@pytest.mark.parametrize(
    "change",
    [
        "untrusted",
        "future",
        "inverted",
        "crossed",
        "negative",
        "nan",
        "duplicate",
        "type",
        "expired",
        "counter",
    ],
)
def test_invalid_option_chain_cannot_enter_the_store(service, change):
    request = record()
    row = request["payload"]["contracts"][0]
    if change == "untrusted":
        request["source"] = "not-allowed"
    elif change == "future":
        request["available_at"] = (NOW + timedelta(seconds=1)).isoformat()
    elif change == "inverted":
        request["available_at"] = (NOW - timedelta(days=1)).isoformat()
    elif change == "crossed":
        row["bid"] = "200"
    elif change == "negative":
        row["bid"] = "-1"
    elif change == "nan":
        row["ask"] = "NaN"
    elif change == "duplicate":
        request["payload"]["contracts"] *= 2
    elif change == "type":
        row["option_type"] = "BROKEN"
    elif change == "expired":
        request["payload"]["expiry"] = "2026-01-01"
    else:
        row["volume"] = True
    with pytest.raises(ValueError):
        service.ingest(request)
    assert service.db.list("market-views") == []


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "http://example.invalid/news",
        "https://user:pass@example.invalid/x",
        "https://example.invalid:444/x",
    ],
)
def test_unsafe_news_links_are_rejected(service, url):
    request = record("news")
    request["payload"]["url"] = url
    with pytest.raises(ValueError):
        service.ingest(request)


def test_stale_and_expired_contracts_do_not_appear_as_current(service):
    old = record()
    old["event_time"] = old["available_at"] = (NOW - timedelta(hours=1)).isoformat()
    service.ingest(old)
    assert service.read("options", "NIFTY")["reason"] == "stale_or_changed_source_policy"


def test_stored_payload_tampering_fails_closed(service):
    service.ingest(record())
    with service.db.transaction() as db:
        db.execute(
            "UPDATE cp_documents SET body=json_set(body,'$.payload.contracts[0].bid','999') WHERE collection='market-views'"
        )
    with pytest.raises(ValueError, match="digest_mismatch"):
        service.read("options", "NIFTY")


def history(closes):
    start = NOW - timedelta(days=1)
    return {
        "provider": "fixture",
        "mode": "synthetic",
        "symbol": "BTC-USD",
        "bars": [
            {
                "symbol": "BTC-USD",
                "event_time": (start + timedelta(minutes=i)).isoformat(),
                "available_at": (start + timedelta(minutes=i)).isoformat(),
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": 10,
            }
            for i, price in enumerate(closes)
        ],
    }


@pytest.mark.parametrize(
    "prices,label",
    [
        ([100] * 25, "ranging"),
        ([100 + i for i in range(25)], "trending_up"),
        ([100 - i / 10 for i in range(25)], "trending_down"),
        ([100] * 24 + [60], "crisis"),
    ],
)
def test_regime_is_reproducible_descriptive_output_with_no_model_approval(prices, label):
    result = regime_diagnostic(history(prices))
    assert result == regime_diagnostic(history(prices))
    assert result["label"] == label
    assert result["independent_model_approval"] is result["live_execution"] is False
    assert result["liquidity_state"] == "unavailable_without_order_book"


def test_regime_rejects_short_future_unordered_and_live_histories():
    valid = history([100 + i for i in range(25)])
    invalid = [
        history([100] * 20),
        {**valid, "mode": "live"},
        {**valid, "bars": list(reversed(valid["bars"]))},
    ]
    future = deepcopy(valid)
    future["bars"][-1]["available_at"] = "2100-01-01T00:00:00Z"
    invalid.append(future)
    for item in invalid:
        with pytest.raises(ValueError):
            regime_diagnostic(item)


def test_api_intake_requires_mfa_operations_and_read_rejects_viewer(tmp_path, monkeypatch):
    monkeypatch.setenv("OSIRIS_CONTROL_DB", str(tmp_path / "api.db"))
    monkeypatch.setenv("OSIRIS_CONTROL_MASTER_KEY", base64.b64encode(b"k" * 32).decode())
    monkeypatch.setenv(
        "OSIRIS_ADMIN_TOKENS",
        json.dumps({"operator": "fixture-operator-token", "viewer": "fixture-viewer-token"}),
    )
    policy = sources()
    policy["fixture"]["expires_at"] = "2100-01-01T00:00:00Z"
    monkeypatch.setenv("OSIRIS_MARKET_VIEW_SOURCES", json.dumps(policy))
    app = FastAPI()
    app.include_router(router)
    api = TestClient(app)
    header = {"Authorization": "Bearer fixture-operator-token"}
    assert api.post("/control-plane/market/views", json=record(), headers=header).status_code == 403
    enrollment = api.post("/control-plane/auth/enroll", headers=header).json()
    session = api.post(
        "/control-plane/auth/login",
        headers=header,
        json={"code": totp(enrollment["secret"], int(time.time() // 30))},
    ).json()
    auth = {"Authorization": "Session " + session["session_token"]}
    request = record()
    now = datetime.now(UTC)
    request["event_time"] = request["available_at"] = now.isoformat()
    # Keep fixture contract expiry after the actual test clock.
    request["payload"]["expiry"] = (now + timedelta(days=7)).date().isoformat()
    assert api.post("/control-plane/market/views", json=request, headers=auth).status_code == 200
    response = api.get(
        "/control-plane/market/views/options", params={"entity": "NIFTY"}, headers=auth
    )
    assert response.status_code == 200, response.text
    assert response.json()["records"][0]["payload"]["contracts"][0]["bid"] == "100"
    assert response.headers["cache-control"] == "no-store"
    assert (
        api.get(
            "/control-plane/market/views/options",
            params={"entity": "NIFTY"},
            headers={"Authorization": "Bearer fixture-viewer-token"},
        ).status_code
        == 403
    )
