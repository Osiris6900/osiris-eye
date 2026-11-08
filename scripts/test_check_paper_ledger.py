import unittest
from check_paper_ledger import reconcile

class LedgerTests(unittest.TestCase):
    def data(self):
        return {"orders":[{"id":"o1","symbol":"BTC","side":"buy","quantity":"2"}],"fills":[{"id":"f1","order_id":"o1","quantity":"1","price":"100"}]}
    def test_valid(self): self.assertTrue(reconcile(self.data())["passed"])
    def test_overfill(self):
        d=self.data(); d["fills"][0]["quantity"]="3"; self.assertFalse(reconcile(d)["passed"])
    def test_duplicate_fill(self):
        d=self.data(); d["fills"].append(dict(d["fills"][0])); self.assertFalse(reconcile(d)["passed"])
    def test_orphan(self):
        d=self.data(); d["fills"][0]["order_id"]="missing"; self.assertFalse(reconcile(d)["passed"])
    def test_invalid_price(self):
        d=self.data(); d["fills"][0]["price"]="NaN"; self.assertFalse(reconcile(d)["passed"])
    def test_cash_and_positions(self):
        result = reconcile(self.data())
        self.assertEqual(result["cash"], "99900")
        self.assertEqual(result["positions"]["BTC"], "1")
    def test_insufficient_cash(self):
        d = self.data()
        d["starting_cash"] = "50"
        self.assertFalse(reconcile(d)["passed"])
        self.assertIn("insufficient paper cash", " ".join(reconcile(d)["errors"]))
    def test_sell_without_position(self):
        d = self.data()
        d["orders"][0]["side"] = "sell"
        self.assertFalse(reconcile(d)["passed"])
    def test_zero_starting_cash(self):
        d = {"starting_cash": "0", "orders": [], "fills": []}
        self.assertTrue(reconcile(d)["passed"])
        self.assertEqual(reconcile(d)["cash"], "0")

    def test_restart_replay_is_deterministic(self):
        import json
        d = self.data()
        d["orders"].append({"id": "o2", "symbol": "BTC", "side": "sell", "quantity": "1"})
        d["fills"].append({"id": "f2", "order_id": "o2", "quantity": "0.4", "price": "120"})
        first = reconcile(d)
        restarted = reconcile(json.loads(json.dumps(d)))
        self.assertEqual(first, restarted)
        self.assertTrue(first["passed"])
        self.assertEqual(first["cash"], "99948.0")
        self.assertEqual(first["positions"]["BTC"], "0.6")

    def test_duplicate_order_id_fails_closed(self):
        d = self.data()
        d["orders"].append(dict(d["orders"][0]))
        self.assertFalse(reconcile(d)["passed"])

    def test_invalid_starting_cash_fails_closed(self):
        d = self.data()
        d["starting_cash"] = "NaN"
        self.assertFalse(reconcile(d)["passed"])

    def test_negative_starting_cash_fails_closed(self):
        d = self.data()
        d["starting_cash"] = "-1"
        self.assertFalse(reconcile(d)["passed"])

if __name__=="__main__": unittest.main()
