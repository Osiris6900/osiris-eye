"""In-memory paper order intake; no broker/exchange routing."""
from decimal import Decimal, InvalidOperation

class PaperOrderBook:
    def __init__(self):
        self.orders = {}

    def accept(self, order):
        if not isinstance(order, dict):
            raise ValueError("invalid order")
        key = order.get("client_order_id")
        if not isinstance(key, str) or not key:
            raise ValueError("missing idempotency key")
        if order.get("side") not in ("buy", "sell") or not isinstance(order.get("symbol"), str) or not order["symbol"]:
            raise ValueError("invalid order metadata")
        try:
            qty = Decimal(str(order["quantity"]))
        except (KeyError, TypeError, InvalidOperation):
            raise ValueError("invalid quantity") from None
        if not qty.is_finite() or qty <= 0:
            raise ValueError("invalid quantity")
        canonical = (order["symbol"], order["side"], str(qty))
        if key in self.orders:
            if self.orders[key] != canonical:
                raise ValueError("idempotency key collision")
            return False
        self.orders[key] = canonical
        return True
