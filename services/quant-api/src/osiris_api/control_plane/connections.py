"""Ghost/keyless defaults and optional, explicitly tested PAPER broker access.

Saving preferences never contacts a provider or submits an order. The optional
broker probe is GET-only, uses server credentials and a fixed paper origin, and
returns neither account identifiers nor balances. It does not activate trading.
"""
from __future__ import annotations

import json
import os
from datetime import UTC, datetime

from .broker import AlpacaPaperBroker
from .database import Database

DEFAULT = {"mode": "ghost", "data_provider": "coinbase-public", "broker": "none"}
PROVIDERS = (
    {"id": "coinbase-public", "label": "Coinbase public · crypto", "access": "keyless"},
    {"id": "binance-public", "label": "Binance public · crypto", "access": "keyless"},
    {"id": "stooq-daily", "label": "Stooq public · daily equities", "access": "keyless"},
)
BROKERS = (
    {"id": "none", "label": "No broker · internal paper account"},
    {"id": "alpaca-paper", "label": "Alpaca PAPER"},
)


def validate_preferences(value):
    if not isinstance(value, dict) or set(value) != set(DEFAULT):
        raise ValueError("invalid_connection_preferences")
    if (any(not isinstance(item, str) for item in value.values())
            or value["mode"] not in ("ghost", "broker")
            or value["data_provider"] not in {p["id"] for p in PROVIDERS}
            or value["broker"] not in {p["id"] for p in BROKERS}):
        raise ValueError("unsupported_connection_option")
    if ((value["mode"] == "ghost" and value["broker"] != "none")
            or (value["mode"] == "broker" and value["broker"] != "alpaca-paper")):
        raise ValueError("connection_mode_broker_mismatch")
    return dict(value)


class Connections:
    def __init__(self, db: Database, *, environ=None, transport=None):
        self.db, self.environ, self.transport = db, os.environ if environ is None else environ, transport

    def _read(self, conn):
        row = conn.execute("SELECT version,body FROM cp_documents WHERE collection='connection-options' AND id='current'").fetchone()
        if row is None:
            return {"version": 0, "preferences": dict(DEFAULT)}
        if type(row["version"]) is not int or not 1 <= row["version"] <= 1_000_000_000:
            raise ValueError("invalid_connection_version")
        return {"version": row["version"], "preferences": validate_preferences(json.loads(row["body"]))}

    def preferences(self):
        with self.db.transaction() as conn:
            return self._read(conn)

    def status(self):
        return self._status(self.preferences())

    def _status(self, current):
        readiness = "not_selected"
        if current["preferences"]["broker"] == "alpaca-paper":
            readiness = ("disabled" if self.environ.get("OSIRIS_ALPACA_PAPER_ENABLED") != "1" else
                         "configured_unverified" if self._configured() else "credentials_required")
        return {"schema": "osiris.connection-options.v1", **current,
                "data_providers": list(PROVIDERS), "brokers": list(BROKERS),
                "broker_readiness": readiness, "live_execution": False,
                "broker_order_submitted": False, "connection_verified": False}

    def save(self, preferences, *, expected_version):
        value = validate_preferences(preferences)
        if type(expected_version) is not int or not 0 <= expected_version < 1_000_000_000:
            raise ValueError("invalid_connection_version")
        version = self.db.put("connection-options", "current", value, expected_version=expected_version)
        return self._status({"version": version, "preferences": value})

    def _configured(self):
        return all(isinstance(self.environ.get(name), str) and 1 <= len(self.environ[name]) <= 16384
                   and all(33 <= ord(c) <= 126 for c in self.environ[name])
                   for name in ("OSIRIS_ALPACA_PAPER_API_KEY", "OSIRIS_ALPACA_PAPER_API_SECRET"))

    def test_broker(self, *, expected_version):
        if type(expected_version) is not int or not 0 <= expected_version <= 1_000_000_000:
            raise ValueError("invalid_connection_version")
        # Serialize the bounded probe with preference changes. A switch to Ghost
        # cannot complete while a previous profile's external probe is active.
        with self.db.transaction() as conn:
            current = self._read(conn)
            if current["version"] != expected_version:
                raise ValueError("version_conflict")
            if current["preferences"]["mode"] != "broker":
                raise ValueError("broker_not_selected")
            if self.environ.get("OSIRIS_ALPACA_PAPER_ENABLED") != "1":
                raise ValueError("paper_broker_not_enabled")
            if not self._configured():
                raise ValueError("paper_broker_credentials_required")
            broker = AlpacaPaperBroker(self.db, {
                "key": self.environ["OSIRIS_ALPACA_PAPER_API_KEY"],
                "secret": self.environ["OSIRIS_ALPACA_PAPER_API_SECRET"],
            }, enabled=True, transport=self.transport)
            try:
                account = broker._request("GET", "/v2/account")
                if (not isinstance(account, dict) or account.get("status") != "ACTIVE"
                        or account.get("account_blocked") is not False
                        or account.get("trading_blocked") is not False):
                    raise ValueError("unavailable")
            except Exception:
                raise ValueError("paper_broker_connection_unavailable") from None
            return {"schema": "osiris.paper-broker-probe.v1", "broker": "alpaca-paper",
                    "version": current["version"], "connected": True, "read_only": True,
                    "checked_at": datetime.now(UTC).isoformat(), "live_execution": False,
                    "broker_order_submitted": False}
