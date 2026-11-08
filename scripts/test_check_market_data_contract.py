import unittest
from check_market_data_contract import validate

class MarketDataTests(unittest.TestCase):
    def row(self):
        return {"symbol":"BTCUSD","source":"authorized_vendor","entitlement":"authorized","event_time":"2026-11-08T10:00:00Z","available_time":"2026-11-08T10:00:01Z","received_time":"2026-11-08T10:00:02Z","price":"1.25"}
    def test_valid(self): self.assertEqual(validate([self.row()]), [])
    def test_reject_unlicensed(self):
        r=self.row(); r["entitlement"]="unknown"; self.assertTrue(validate([r]))
    def test_reject_future_availability(self):
        r=self.row(); r["available_time"]="2026-11-08T11:00:00Z"; self.assertTrue(validate([r]))
    def test_reject_nonfinite_price(self):
        r=self.row(); r["price"]="NaN"; self.assertTrue(validate([r]))
    def test_reject_stale(self):
        r=self.row(); r["received_time"]="2026-11-08T10:10:00Z"; self.assertTrue(validate([r]))
    def test_invalid_freshness_limit_denied(self):
        self.assertTrue(validate([self.row()], -1))
        self.assertTrue(validate([self.row()], True))
if __name__=="__main__": unittest.main()
