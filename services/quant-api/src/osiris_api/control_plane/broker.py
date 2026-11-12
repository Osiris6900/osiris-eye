"""Explicitly configured Alpaca PAPER transport and FIX 4.4 wire integrity.

No live hostname, automatic order activation or general-purpose broker proxy.
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from urllib.parse import quote, urlencode

from ..risk import RiskLimits, check_execution
from .database import Database, digest, identifier
from .vendors import HttpJson

BROKER_STATES = frozenset({
    "new", "pending_new", "accepted", "accepted_for_bidding", "partially_filled",
    "filled", "done_for_day", "canceled", "expired", "pending_cancel", "stopped",
    "rejected", "suspended", "calculated",
})
TERMINAL_BROKER_STATES = frozenset({"filled", "canceled", "expired", "rejected"})


def validate_broker_snapshot(order: dict, response: object, previous: dict | None = None) -> None:
    """Bind a paper-broker snapshot to the persisted intent and prior fill progress.

    A client ID alone is not proof that an acknowledgement describes the order
    we authorized. Unknown states and corrections require operator investigation.
    """
    try:
        if not isinstance(response, dict):
            raise TypeError()
        identifier(response.get("id"))
        requested = money(order["quantity"])
        filled = money(response.get("filled_qty"), positive=False)
        status = response.get("status")
        if (
            response.get("client_order_id") != order["client_order_id"]
            or response.get("symbol") != order["symbol"]
            or response.get("side") != order["side"].lower()
            or money(response.get("qty")) != requested
            or money(response.get("limit_price")) != money(order["limit_price"])
            or response.get("type") != "limit"
            or response.get("time_in_force") != "day"
            or not isinstance(status, str)
            or status not in BROKER_STATES
            or not 0 <= filled <= requested
            or (status == "filled" and filled != requested)
            or (status != "filled" and filled == requested)
            or (status == "partially_filled" and filled == 0)
            or (status in {"new", "pending_new", "accepted", "accepted_for_bidding", "rejected"}
                and filled != 0)
        ):
            raise ValueError()
        if filled:
            average = money(response.get("filled_avg_price"))
            limit = money(order["limit_price"])
            if (order["side"].upper() == "BUY" and average > limit) or (
                order["side"].upper() == "SELL" and average < limit
            ):
                raise ValueError()
        elif response.get("filled_avg_price") is not None and (
            money(response["filled_avg_price"], positive=False) != 0
        ):
            raise ValueError()
        if previous is not None:
            prior_filled = money(previous["filled_qty"], positive=False)
            if (
                response["id"] != previous["id"]
                or filled < prior_filled
                or (previous["status"] in TERMINAL_BROKER_STATES and (
                    status != previous["status"] or filled != prior_filled
                ))
                or (filled == prior_filled and filled > 0 and
                    money(response["filled_avg_price"]) != money(previous["filled_avg_price"]))
            ):
                raise ValueError()
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ValueError("broker_snapshot_mismatch") from None


def money(value: object, *, positive: bool = True) -> Decimal:
    if not isinstance(value, (str, int)) or isinstance(value, bool) or len(str(value)) > 64:
        raise ValueError("decimal_string_required")
    try:
        amount = Decimal(value)
    except InvalidOperation:
        raise ValueError("invalid_decimal") from None
    if not amount.is_finite() or amount.copy_abs() > Decimal("1e15") or (positive and amount <= 0):
        raise ValueError("decimal_out_of_range")
    # Input length alone does not bound numeric work: a short exponent such as
    # 1e-999999999 would allocate an enormous denominator in Fraction below.
    if abs(amount.as_tuple().exponent) > 1000:
        raise ValueError("decimal_precision_out_of_range")
    return amount


class AlpacaPaperBroker:
    ORIGIN = "https://paper-api.alpaca.markets"

    def __init__(self, db: Database, credentials: dict, *, enabled: bool = False, transport=None):
        self.db, self.enabled, self.transport = db, enabled is True, transport or HttpJson()
        if not credentials.get("key") or not credentials.get("secret"):
            raise ValueError("paper_credentials_required")
        self.headers = {
            "APCA-API-KEY-ID": credentials["key"],
            "APCA-API-SECRET-KEY": credentials["secret"],
        }

    def _request(self, method: str, path: str, body=None):
        if not self.enabled:
            raise ValueError("paper_broker_not_enabled")
        return self.transport.request(method, self.ORIGIN + path, headers=self.headers, body=body)

    def submit(
        self,
        order: dict,
        *,
        limits: RiskLimits,
        position: float,
        gross: float,
        daily_pnl: float,
        kill_switch: bool = True,
        fresh: bool = False,
    ):
        if not isinstance(order, dict) or set(order) != {
            "client_order_id", "symbol", "side", "quantity", "limit_price",
        }:
            raise ValueError("invalid_order_shape")
        # Use the same detached intent for risk, transport and reconciliation.
        order = dict(order)
        client_id, symbol = identifier(order["client_order_id"]), identifier(order["symbol"])
        quantity, price = money(order["quantity"]), money(order["limit_price"])
        allowed, reason, _ = check_execution(
            side=order["side"],
            quantity=float(quantity),
            price=float(price),
            current_position_notional=position,
            current_gross_exposure=gross,
            daily_pnl=daily_pnl,
            limits=limits,
            kill_switch_active=kill_switch,
            market_data_fresh=fresh,
            risk_service_available=True,
        )
        if not allowed:
            raise ValueError(reason)
        # A float approval can round away a tiny excess at an exact budget.
        # Compare the original decimal economics without Decimal-context rounding.
        notional = Fraction(quantity) * Fraction(price)
        if notional > Fraction(str(limits.max_order_notional)):
            raise ValueError("max_order_notional_exceeded")
        if order["side"].upper() == "BUY":
            if notional > Fraction(str(limits.max_position_notional)) - Fraction(str(position)):
                raise ValueError("max_position_notional_exceeded")
            if notional > Fraction(str(limits.max_gross_exposure)) - Fraction(str(gross)):
                raise ValueError("exposure_budget_exceeded")
        elif notional > Fraction(str(position)) or notional > Fraction(str(gross)):
            raise ValueError("invalid_order_or_position")
        if not self.enabled:
            raise ValueError("paper_broker_not_enabled")
        fingerprint = digest(order)
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT body FROM cp_documents WHERE collection='broker-orders' AND id=?",
                (client_id,),
            ).fetchone()
            if row:
                state = json.loads(row[0])
                if state["fingerprint"] != fingerprint:
                    raise ValueError("order_id_conflict")
                if state.get("order") != order:
                    raise ValueError("broker_order_contract_required")
                if state["status"] != "acknowledged":
                    raise ValueError("order_outcome_unknown_reconcile_first")
                validate_broker_snapshot(order, state.get("response"))
                return state["response"]
            conn.execute(
                "INSERT INTO cp_documents VALUES ('broker-orders',?,1,?)",
                (client_id, json.dumps({
                    "fingerprint": fingerprint, "status": "submitting", "order": order,
                })),
            )
        body = {
            "client_order_id": client_id,
            "symbol": symbol,
            "side": order["side"].lower(),
            "qty": str(quantity),
            "limit_price": str(price),
            "type": "limit",
            "time_in_force": "day",
        }
        try:
            response = self._request("POST", "/v2/orders", body)
            validate_broker_snapshot(order, response)
        except Exception:  # noqa: BLE001 - any send failure leaves an uncertain broker outcome
            raise ValueError("order_outcome_unknown_reconcile_first") from None
        self.db.put(
            "broker-orders",
            client_id,
            {"fingerprint": fingerprint, "status": "acknowledged",
             "order": order, "response": response},
            expected_version=1,
        )
        return response

    def reconcile(self, client_id: str):
        client_id = identifier(client_id)
        prior = self.db.get("broker-orders", client_id)
        if not prior:
            raise ValueError("unknown_client_order")
        order = prior["value"].get("order")
        if not isinstance(order, dict) or digest(order) != prior["value"].get("fingerprint"):
            raise ValueError("broker_order_contract_required")
        response = self._request(
            "GET", "/v2/orders:by_client_order_id?" + urlencode({"client_order_id": client_id})
        )
        validate_broker_snapshot(order, response, previous=prior["value"].get("response"))
        self.db.put(
            "broker-orders",
            client_id,
            {**prior["value"], "status": "acknowledged", "response": response},
            expected_version=prior["version"],
        )
        return response

    def cancel(self, broker_order_id: str):
        return self._request("DELETE", "/v2/orders/" + quote(identifier(broker_order_id), safe=""))


def encode_fix(fields: list[tuple[int, str]]) -> bytes:
    if not fields or fields[0][0] != 35:
        raise ValueError("fix_message_type_first")
    body = bytearray()
    seen = set()
    for tag, value in fields:
        if (
            type(tag) is not int
            or tag <= 0
            or tag in {8, 9, 10}
            or tag in seen
            or not isinstance(value, str)
            or "\x01" in value
            or "=" in value
        ):
            raise ValueError("invalid_fix_field")
        seen.add(tag)
        body.extend(f"{tag}={value}\x01".encode("ascii"))
    if len(body) > 65536:
        raise ValueError("fix_message_too_large")
    prefix = f"8=FIX.4.4\x019={len(body)}\x01".encode() + body
    return prefix + f"10={sum(prefix) % 256:03d}\x01".encode()


def decode_fix(raw: bytes) -> dict[int, str]:
    if not isinstance(raw, bytes) or len(raw) > 65580:
        raise ValueError("invalid_fix_message")
    try:
        pieces = raw.decode("ascii").split("\x01")
        pairs = [part.split("=", 1) for part in pieces[:-1]]
        fields = [(int(tag), value) for tag, value in pairs]
        body_start = raw.index(b"\x01", raw.index(b"\x01") + 1) + 1
        checksum_start = raw.rfind(b"10=")
        if (
            pieces[-1] != ""
            or fields[0] != (8, "FIX.4.4")
            or fields[1][0] != 9
            or fields[-1][0] != 10
            or fields[2][0] != 35
            or len({tag for tag, _ in fields}) != len(fields)
            or int(fields[1][1]) != checksum_start - body_start
            or fields[-1][1] != f"{sum(raw[:checksum_start]) % 256:03d}"
        ):
            raise ValueError()
        return dict(fields)
    except (ValueError, IndexError, UnicodeError):
        raise ValueError("invalid_fix_integrity") from None
