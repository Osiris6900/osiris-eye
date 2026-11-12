"""Paper-ledger accounting invariants: offline only, no live execution."""
import unittest
from decimal import Decimal as D

from scripts.paper_order_ledger import PaperLedger, OrderState


class PaperLedgerAccountingGates(unittest.TestCase):
    def test_round_trip_cash_positions_and_fees(self):
        ledger = PaperLedger(cash=D("1000"))
        ledger.submit("buy-1", "BTC", "buy", D("2"))
        ledger.fill("buy-1", D("2"), D("100"), D("1"))
        ledger.submit("sell-1", "BTC", "sell", D("2"))
        ledger.fill("sell-1", D("1"), D("120"), D("0.5"))
        ledger.fill("sell-1", D("1"), D("130"), D("0.5"))
        self.assertEqual(ledger.cash, D("1048"))
        self.assertEqual(ledger.positions["BTC"], D("0"))
        self.assertEqual(ledger.fees, D("2"))
        self.assertEqual(ledger.orders["sell-1"].state, OrderState.FILLED)

    def test_cancelled_partial_order_preserves_fills(self):
        ledger = PaperLedger(cash=D("500"))
        ledger.submit("a", "ETH", "buy", D("3"))
        ledger.fill("a", D("1"), D("20"), D("0.25"))
        ledger.cancel("a")
        self.assertEqual(ledger.orders["a"].state, OrderState.CANCELED)
        self.assertEqual(ledger.orders["a"].filled, D("1"))
        self.assertEqual(ledger.positions["ETH"], D("1"))
        self.assertEqual(ledger.cash, D("479.75"))
        with self.assertRaises(ValueError):
            ledger.fill("a", D("1"), D("20"))

    def test_invalid_fee_and_overfill_do_not_mutate_balances(self):
        ledger = PaperLedger(cash=D("100"))
        ledger.submit("a", "BTC", "buy", D("1"))
        for quantity, price, fee in [
            (D("1"), D("10"), D("-1")),
            (D("2"), D("10"), D("0")),
            (D("1"), D("NaN"), D("0")),
        ]:
            with self.assertRaises(ValueError):
                ledger.fill("a", quantity, price, fee)
            self.assertEqual(ledger.cash, D("100"))
            self.assertEqual(ledger.fees, D("0"))
            self.assertEqual(ledger.positions, {})
            self.assertEqual(ledger.orders["a"].filled, D("0"))


if __name__ == "__main__":
    unittest.main()
