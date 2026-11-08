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
if __name__=="__main__": unittest.main()
