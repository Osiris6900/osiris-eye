import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from scripts.market_observation_contract import MarketObservation


class MarketObservationTests(unittest.TestCase):
    def test_valid_and_stale(self):
        now = datetime.now(timezone.utc)
        obs = MarketObservation("licensed-feed", "venue", "BTC", now, now, 1, D("100"))
        obs.validate()
        with self.assertRaises(ValueError):
            MarketObservation("feed", "venue", "BTC", now - timedelta(seconds=6), now, 1, D("100")).validate()

    def test_rejects_invalid(self):
        now = datetime.now(timezone.utc)
        with self.assertRaises(ValueError):
            MarketObservation("feed", "venue", "BTC", now, now, 1, D("NaN")).validate()


if __name__ == "__main__":
    unittest.main()
