"""Ghost API-keyless trade consistency negative regression tests."""
from __future__ import annotations

import unittest
from check_ghost_keyless_consistency import check


def fixture():
    return {
        "schema": "osiris.ghost-public-trade-consistency.v1",
        "symbol": "BTCUSDT",
        "observations": [
            {"source": "public-feed-A", "symbol": "BTCUSDT", "price": "100.00",
             "exchange_ms": 1000, "received_ms": 1200, "transport_authenticated": True},
            {"source": "public-feed-B", "symbol": "BTCUSDT", "price": "100.10",
             "exchange_ms": 1001, "received_ms": 1201, "transport_authenticated": True},
        ],
    }


class GhostConsistencyTests(unittest.TestCase):
    def test_consistency_is_not_market_truth_or_keyless_identity(self):
        result = check(fixture(), now_ms=1300)
        self.assertEqual(result["status"], "consistent")
        self.assertFalse(result["provider_truth_verified"])
        self.assertFalse(result["source_independence_verified"])
        self.assertFalse(result["node_identity_keyless"])
        self.assertFalse(result["live_trading_authorized"])

    def test_divergent_prices_rejected(self):
        data = fixture()
        data["observations"][1]["price"] = "110"
        self.assertEqual(check(data, now_ms=1300)["status"], "rejected")

    def test_duplicate_source_rejected(self):
        data = fixture()
        data["observations"][1]["source"] = "public-feed-A"
        self.assertIn("duplicate_or_invalid_source", check(data, now_ms=1300)["errors"])

    def test_stale_or_untrusted_rejected(self):
        data = fixture()
        data["observations"][0]["received_ms"] = 100
        self.assertEqual(check(data, now_ms=10000)["status"], "rejected")
        data = fixture()
        data["observations"][0]["transport_authenticated"] = False
        self.assertIn("unauthenticated_transport", check(data, now_ms=1300)["errors"])

    def test_invalid_price_or_symbol_rejected(self):
        for price in ("NaN", "-1", "0", "Infinity"):
            with self.subTest(price=price):
                data = fixture()
                data["observations"][0]["price"] = price
                self.assertEqual(check(data, now_ms=1300)["status"], "rejected")
        data = fixture()
        data["observations"][0]["symbol"] = "ETHUSDT"
        self.assertIn("symbol_mismatch", check(data, now_ms=1300)["errors"])


if __name__ == "__main__":
    unittest.main()
