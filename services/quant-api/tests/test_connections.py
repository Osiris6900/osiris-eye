"""Connection preferences must not implicitly connect, identify or trade."""
import base64
import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from osiris_api.control_plane.api import router
from osiris_api.control_plane.broker import AlpacaPaperBroker
from osiris_api.control_plane.connections import Connections, DEFAULT
from osiris_api.control_plane.database import Database
from osiris_api.control_plane.identity import totp


def test_every_offered_keyless_provider_is_registered_by_the_actual_gateway():
    # A fresh process avoids any other test's intentionally replaced gateway.
    # Constructors register adapters; no market request or account call occurs.
    check = subprocess.run([sys.executable, "-c", (
        "from osiris_api.market_routes import market_data_gateway; "
        "from osiris_api.control_plane.connections import PROVIDERS; "
        "registered={p['provider']:p for p in market_data_gateway.providers()['providers']}; "
        "assert all(p['id'] in registered and registered[p['id']]['mode']=='public' for p in PROVIDERS)"
    )], env={**os.environ, **{name: "true" for name in (
        "OSIRIS_PUBLIC_COINBASE_ENABLED", "OSIRIS_PUBLIC_BINANCE_ENABLED", "OSIRIS_PUBLIC_STOOQ_ENABLED",
    )}}, capture_output=True, text=True, timeout=30)
    assert check.returncode == 0, check.stderr

BROKER = {"mode": "broker", "data_provider": "binance-public", "broker": "alpaca-paper"}
ENV = {"OSIRIS_ALPACA_PAPER_ENABLED": "1", "OSIRIS_ALPACA_PAPER_API_KEY": "fixture-paper-key",
       "OSIRIS_ALPACA_PAPER_API_SECRET": "fixture-paper-secret"}


class Transport:
    def __init__(self, response=None):
        self.calls = []
        self.response = response if response is not None else {
            "status": "ACTIVE", "account_blocked": False, "trading_blocked": False,
            "id": "private-account-id", "account_number": "private-account-number",
            "cash": "123456.78", "secret": "never-return-this",
        }

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.response


@pytest.fixture
def service(tmp_path):
    return Connections(Database(str(tmp_path / "control.sqlite3")), environ=dict(ENV), transport=Transport())


def test_default_ghost_needs_no_vendor_secrets_and_never_contacts_a_broker(service):
    service.environ = {}
    result = service.status()
    assert result["preferences"] == DEFAULT
    assert result["version"] == 0
    assert result["broker_readiness"] == "not_selected"
    assert result["connection_verified"] is result["live_execution"] is result["broker_order_submitted"] is False
    assert service.db.get("connection-options", "current") is None
    with pytest.raises(ValueError, match="broker_not_selected"):
        service.test_broker(expected_version=0)
    assert service.transport.calls == []


@pytest.mark.parametrize("provider", ["coinbase-public", "binance-public", "stooq-daily"])
def test_keyless_selection_survives_restart_without_connecting(service, provider):
    selected = {**DEFAULT, "data_provider": provider}
    result = service.save(selected, expected_version=0)
    assert result["version"] == 1
    restarted = Connections(Database(service.db.path), environ={}, transport=service.transport)
    assert restarted.preferences() == {"version": 1, "preferences": selected}
    assert service.transport.calls == []


@pytest.mark.parametrize("changes", [
    {"mode": "live"}, {"mode": "broker"}, {"broker": "alpaca-paper"},
    {"broker": "alpaca-live"}, {"broker": "https://example.invalid"},
    {"data_provider": "alpaca"}, {"data_provider": []}, {"mode": True},
    {"api_key": "never-store-me"}, {"live_execution": True}, {"node_authentication": False},
])
def test_invalid_live_secret_and_mismatched_options_fail_without_a_write(service, changes):
    with pytest.raises(ValueError):
        service.save({**DEFAULT, **changes}, expected_version=0)
    assert service.preferences()["version"] == 0
    assert service.transport.calls == []


def test_broker_selection_and_readiness_are_not_connectivity(service):
    result = service.save(BROKER, expected_version=0)
    assert result["broker_readiness"] == "configured_unverified"
    assert result["connection_verified"] is False
    assert service.transport.calls == []


def test_probe_is_get_only_paper_only_and_does_not_return_account_identity(service):
    service.save(BROKER, expected_version=0)
    result = service.test_broker(expected_version=1)
    assert result["connected"] is result["read_only"] is True
    assert result["broker_order_submitted"] is result["live_execution"] is False
    assert service.transport.calls == [("GET", "https://paper-api.alpaca.markets/v2/account", {
        "headers": {"APCA-API-KEY-ID": ENV["OSIRIS_ALPACA_PAPER_API_KEY"],
                    "APCA-API-SECRET-KEY": ENV["OSIRIS_ALPACA_PAPER_API_SECRET"]}, "body": None})]
    serialized = json.dumps(result)
    for value in ("private-account-id", "private-account-number", "123456.78", "never-return-this", *ENV.values()):
        if len(value) > 1:
            assert value not in serialized
    assert service.status()["connection_verified"] is False
    assert service.db.list("broker-orders") == []


@pytest.mark.parametrize("name,value,readiness", [
    ("OSIRIS_ALPACA_PAPER_ENABLED", "0", "disabled"),
    ("OSIRIS_ALPACA_PAPER_API_KEY", "", "credentials_required"),
    ("OSIRIS_ALPACA_PAPER_API_SECRET", "\r\nInjected: secret", "credentials_required"),
    ("OSIRIS_ALPACA_PAPER_API_SECRET", "\x00", "credentials_required"),
    ("OSIRIS_ALPACA_PAPER_API_KEY", " ", "credentials_required"),
])
def test_disabled_or_invalid_server_configuration_blocks_network(service, name, value, readiness):
    service.environ[name] = value
    assert service.save(BROKER, expected_version=0)["broker_readiness"] == readiness
    with pytest.raises(ValueError):
        service.test_broker(expected_version=1)
    assert not service.transport.calls


@pytest.mark.parametrize("account", [None, [], {}, {"status": "CLOSED"},
    {"status": "ACTIVE", "account_blocked": True, "trading_blocked": False},
    {"status": "ACTIVE", "account_blocked": False, "trading_blocked": 0}])
def test_unavailable_malformed_and_blocked_account_never_verifies(service, account):
    service.save(BROKER, expected_version=0)
    service.transport.response = account
    with pytest.raises(ValueError, match="^paper_broker_connection_unavailable$"):
        service.test_broker(expected_version=1)


def test_upstream_secret_exception_is_redacted(service):
    service.save(BROKER, expected_version=0)

    def broken(*args, **kwargs):
        raise RuntimeError("private-account-id fixture-paper-secret")
    service.transport.request = broken
    with pytest.raises(ValueError, match="^paper_broker_connection_unavailable$"):
        service.test_broker(expected_version=1)


def test_version_conflicts_and_switching_to_ghost_block_old_broker_tests(service):
    service.save(BROKER, expected_version=0)
    with pytest.raises(ValueError, match="version_conflict"):
        service.save(DEFAULT, expected_version=0)
    service.save(DEFAULT, expected_version=1)
    with pytest.raises(ValueError, match="version_conflict"):
        service.test_broker(expected_version=1)
    with pytest.raises(ValueError, match="broker_not_selected"):
        service.test_broker(expected_version=2)
    assert not service.transport.calls


@pytest.mark.parametrize("version", [True, -1, 10**50, "0", 0.0])
def test_bad_versions_fail_before_network_or_sqlite_overflow(service, version):
    with pytest.raises(ValueError, match="invalid_connection_version"):
        service.save(DEFAULT, expected_version=version)
    with pytest.raises(ValueError, match="invalid_connection_version"):
        service.test_broker(expected_version=version)
    assert not service.transport.calls


def test_switch_to_ghost_serializes_with_inflight_broker_probe(service):
    service.save(BROKER, expected_version=0)
    entered, release, switched = threading.Event(), threading.Event(), threading.Event()
    original = service.transport.request

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return original(*args, **kwargs)

    def switch():
        outcome = service.save(DEFAULT, expected_version=1)
        switched.set()
        return outcome

    service.transport.request = blocked
    with ThreadPoolExecutor(max_workers=2) as pool:
        test = pool.submit(service.test_broker, expected_version=1)
        assert entered.wait(3)
        write = pool.submit(switch)
        try:
            assert not switched.wait(0.1)
        finally:
            release.set()
        assert test.result(timeout=3)["version"] == 1
        assert write.result(timeout=3)["preferences"] == DEFAULT
    assert service.status()["broker_readiness"] == "not_selected"


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("OSIRIS_CONTROL_DB", str(tmp_path / "api.sqlite3"))
    monkeypatch.setenv("OSIRIS_CONTROL_MASTER_KEY", base64.b64encode(b"k" * 32).decode())
    monkeypatch.setenv("OSIRIS_ADMIN_TOKENS", json.dumps({role: "fixture-" + role for role in ("operator", "viewer")}))
    for name in ENV:
        monkeypatch.delenv(name, raising=False)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def session(api, role):
    headers = {"Authorization": "Bearer fixture-" + role}
    seed = api.post("/control-plane/auth/enroll", headers=headers).json()["secret"]
    login = api.post("/control-plane/auth/login", headers=headers, json={"code": totp(seed, int(time.time() // 30))})
    return {"Authorization": "Session " + login.json()["session_token"]}


def test_api_read_and_write_authorization_and_no_store(api):
    assert api.get("/control-plane/connections").status_code == 401
    viewer = {"Authorization": "Bearer fixture-viewer"}
    response = api.get("/control-plane/connections", headers=viewer)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    payload = {"preferences": BROKER, "expected_version": 0}
    assert api.put("/control-plane/connections", headers=viewer, json=payload).status_code == 403
    assert api.put("/control-plane/connections", headers={"Authorization": "Bearer fixture-operator"}, json=payload).status_code == 403
    viewer_session = session(api, "viewer")
    assert api.put("/control-plane/connections", headers=viewer_session, json=payload).status_code == 403
    operator = session(api, "operator")
    saved = api.put("/control-plane/connections", headers=operator, json=payload)
    assert saved.status_code == 200, saved.text
    assert saved.json()["broker_readiness"] == "disabled"
    assert api.put("/control-plane/connections", headers=operator, json=payload).status_code == 409
    assert api.post("/control-plane/connections/test", headers=operator, json={"expected_version": 1}).status_code == 503
    assert api.post("/control-plane/auth/logout", headers=operator).status_code == 200
    assert api.get("/control-plane/connections", headers=operator).status_code == 401


def test_api_refuses_extra_credentials_urls_and_order_fields(api):
    operator = session(api, "operator")
    for extra in ({"api_key": "fixture-key"}, {"url": "https://example.invalid"}, {"order": {"quantity": 1}}, {"live_execution": True}):
        assert api.put("/control-plane/connections", headers=operator, json={"preferences": DEFAULT, "expected_version": 0, **extra}).status_code == 422
        assert api.post("/control-plane/connections/test", headers=operator, json={"expected_version": 0, **extra}).status_code == 422


def test_api_paper_probe_returns_only_sanitized_output(api, monkeypatch):
    operator = session(api, "operator")
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    calls = []

    def probe(self, method, path, body=None):
        calls.append((method, path, body))
        return {"status": "ACTIVE", "account_blocked": False, "trading_blocked": False,
                "id": "private-account-id", "key": "private-account-key"}

    monkeypatch.setattr(AlpacaPaperBroker, "_request", probe)
    assert api.put("/control-plane/connections", headers=operator, json={"preferences": BROKER, "expected_version": 0}).status_code == 200
    result = api.post("/control-plane/connections/test", headers=operator, json={"expected_version": 1})
    assert result.status_code == 200, result.text
    assert result.json()["connected"] is True
    assert "private-account" not in result.text
    assert result.headers["cache-control"] == "no-store"
    assert calls == [("GET", "/v2/account", None)]
