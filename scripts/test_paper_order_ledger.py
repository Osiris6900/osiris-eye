"""Paper-only order ledger regression tests."""
import unittest
from decimal import Decimal as D
from scripts.paper_order_ledger import PaperLedger, OrderState


class PaperLedgerTests(unittest.TestCase):
    def test_idempotent_submit_and_key_conflict(self):
        ledger = PaperLedger()
        first = ledger.submit("a", "BTC", "buy", D("2"))
        self.assertIs(first, ledger.submit("a", "BTC", "buy", D("2")))
        with self.assertRaises(ValueError):
            ledger.submit("a", "BTC", "sell", D("2"))

    def test_partial_fill_fee_and_final_fill(self):
        ledger = PaperLedger()
        ledger.submit("a", "BTC", "buy", D("2"))
        self.assertEqual(ledger.fill("a", D("1"), D("100"), D("1")).state, OrderState.PARTIALLY_FILLED)
        self.assertEqual(ledger.fill("a", D("1"), D("110"), D("1")).state, OrderState.FILLED)
        self.assertEqual(ledger.cash, D("-212"))
        self.assertEqual(ledger.positions["BTC"], D("2"))
        self.assertEqual(ledger.fees, D("2"))
        with self.assertRaises(ValueError):
            ledger.fill("a", D("1"), D("100"))

    def test_halt_blocks_new_orders_and_fills(self):
        ledger = PaperLedger()
        ledger.submit("a", "BTC", "buy", D("1"))
        ledger.halt()
        with self.assertRaises(ValueError):
            ledger.submit("b", "BTC", "buy", D("1"))
        with self.assertRaises(ValueError):
            ledger.fill("a", D("1"), D("100"))

    def test_invalid_nonfinite_and_overfill(self):
        ledger = PaperLedger()
        with self.assertRaises(ValueError):
            ledger.submit("bad", "BTC", "buy", D("NaN"))
        ledger.submit("a", "BTC", "buy", D("1"))
        with self.assertRaises(ValueError):
            ledger.fill("a", D("2"), D("100"))
        with self.assertRaises(ValueError):
            ledger.fill("a", D("1"), D("Infinity"))


if __name__ == "__main__":
    unittest.main()
