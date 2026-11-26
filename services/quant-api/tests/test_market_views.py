"""Source revocation, conservative availability and immutable research views."""

import base64
import json
import math
import os
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
from osiris_api.control_plane.market_views import MarketViews, regime_diagnostic, regime_oos

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


@pytest.mark.parametrize("excluded", ["changed_rights", "stale"])
def test_result_limit_applies_after_source_policy_and_freshness(service, excluded):
    eligible = record("news", key="eligible")
    eligible["event_time"] = eligible["available_at"] = (NOW - timedelta(seconds=20)).isoformat()
    service.ingest(eligible)
    service.sources["other"] = deepcopy(service.sources["fixture"])
    for index in range(3):
        newer = record("news", key=f"excluded-{index}")
        newer["source"] = "other"
        newer["event_time"] = newer["available_at"] = (NOW - timedelta(seconds=index + 1)).isoformat()
        service.ingest(newer)
    if excluded == "changed_rights":
        service.sources["other"]["rights_reference"] = "replaced-contract"
    else:
        service.sources["other"]["max_age_seconds"] = 1
        service.clock = lambda: NOW + timedelta(seconds=1)
    result = service.read("news", "GLOBAL", limit=1)
    assert result["status"] == "available"
    assert [row["id"] for row in result["records"]] == ["eligible"]


def test_fresh_record_sharing_a_millisecond_with_expired_record_is_not_hidden(service):
    service.sources["fixture"]["max_age_seconds"] = 1
    for key, microseconds in [("aaa-expired", 100), ("zzz-eligible", 700)]:
        item = record("news", key=key)
        item["event_time"] = item["available_at"] = (
            NOW - timedelta(seconds=1) + timedelta(microseconds=microseconds)
        ).isoformat()
        service.ingest(item)
    service.clock = lambda: NOW + timedelta(microseconds=500)
    result = service.read("news", "GLOBAL", limit=1)
    assert [row["id"] for row in result["records"]] == ["zzz-eligible"]


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


@pytest.mark.parametrize(
    "prices,label,regime",
    [
        ([100 + i for i in range(30)], "trending_up", "trending"),
        ([100] * 30, "ranging", "ranging"),
        ([100] * 20 + [60] * 10, "crisis", "stressed"),
    ],
)
def test_regime_oos_is_reproducible_across_trending_ranging_and_stressed(
    prices, label, regime
):
    result = regime_oos(history(prices))
    assert result == regime_oos(history(prices))
    assert result["schema"] == "osiris.regime-oos.v1"
    assert result["history_observations"] == 30
    assert result["feature_window_bars"] == 21
    assert result["evaluated_observations"] == 9
    assert result["by_regime"][regime]["observations"] == 9
    assert result["by_label"][label]["observations"] == 9
    assert {item["label"] for item in result["evaluations"]} == {label}
    assert result["overall"] == result["by_regime"][regime]
    assert result["thresholds_fitted_on_history"] is False
    assert result["causal_feature_availability_enforced"] is True
    assert result["independent_observations"] is False
    assert result["statistical_significance_tested"] is False
    assert result["predictive_model_approved"] is False
    assert result["independent_model_approval"] is result["live_execution"] is False


def test_regime_oos_future_outcome_cannot_change_earlier_labels():
    original = history([100 + i for i in range(30)])
    changed = deepcopy(original)
    changed["bars"][-1].update(open=500, high=500, low=500, close=500)
    before = regime_oos(original)
    after = regime_oos(changed)
    assert before["evaluations"][:-1] == after["evaluations"][:-1]
    assert before["evaluations"][-1]["label"] == after["evaluations"][-1]["label"]
    assert before["evaluations"][-1]["next_log_return"] != pytest.approx(
        after["evaluations"][-1]["next_log_return"]
    )
    assert after["evaluations"][-1]["next_log_return"] == pytest.approx(
        math.log(500) - math.log(128)
    )


def test_regime_oos_rejects_short_histories_and_late_feature_availability():
    with pytest.raises(ValueError, match="at_least_22_bars_required_for_regime"):
        regime_oos(history([100] * 21))
    late = history([100 + i for i in range(30)])
    label_time = datetime.fromisoformat(
        late["bars"][20]["event_time"]
    )
    late["bars"][20]["available_at"] = (label_time + timedelta(seconds=1)).isoformat()
    with pytest.raises(ValueError, match="regime_feature_not_available_before_outcome"):
        regime_oos(late)


def test_regime_oos_api_requires_research_access(tmp_path, monkeypatch):
    monkeypatch.setenv("OSIRIS_CONTROL_DB", str(tmp_path / "api.db"))
    monkeypatch.setenv("OSIRIS_CONTROL_MASTER_KEY", base64.b64encode(b"k" * 32).decode())
    monkeypatch.setenv(
        "OSIRIS_ADMIN_TOKENS",
        json.dumps(
            {"analyst": "fixture-analyst-token", "viewer": "fixture-viewer-token"}
        ),
    )
    from osiris_api.market_routes import market_data_gateway

    monkeypatch.setattr(
        market_data_gateway,
        "fetch_bars",
        lambda *args, **kwargs: history([100 + i for i in range(30)]),
    )
    app = FastAPI()
    app.include_router(router)
    api = TestClient(app)
    params = {
        "provider": "fixture",
        "symbol": "BTC-USD",
        "start": "2026-11-11T00:00:00Z",
        "end": "2026-11-12T00:00:00Z",
        "asset_class": "crypto",
    }
    path = "/control-plane/market/regime/oos"
    assert api.get(path, params=params).status_code == 401
    viewer = {"Authorization": "Bearer fixture-viewer-token"}
    assert api.get(path, params=params, headers=viewer).status_code == 403
    analyst = {"Authorization": "Bearer fixture-analyst-token"}
    response = api.get(path, params=params, headers=analyst)
    assert response.status_code == 200, response.text
    assert response.json()["schema"] == "osiris.regime-oos.v1"
    assert response.json()["evaluated_observations"] == 9
    assert response.headers["cache-control"] == "no-store"


def test_api_intake_requires_mfa_operations_and_read_rejects_viewer(tmp_path, monkeypatch):
    monkeypatch.setenv("OSIRIS_CONTROL_DB", str(tmp_path / "api.db"))
    monkeypatch.setenv("OSIRIS_CONTROL_MASTER_KEY", base64.b64encode(os.urandom(32)).decode())
    monkeypatch.setenv("OSIRIS_CONTROL_MASTER_KEY_ID", "identity-master-1")
    monkeypatch.setenv("OSIRIS_CONTROL_MASTER_KEY_VERSION", "1")
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
