import unittest
from decimal import Decimal
from backtest_cost_gate import net_returns

class CostTests(unittest.TestCase):
    def test_costs(self):
        self.assertEqual(net_returns([{"gross_return":"0.02","turnover":"1"}],10,5), [Decimal("0.0185")])
    def test_negative_cost(self):
        with self.assertRaises(ValueError):
            net_returns([], -1, 0)
    def test_nan(self):
        with self.assertRaises(ValueError):
            net_returns([{"gross_return":"NaN","turnover":"1"}],0,0)
if __name__ == "__main__": unittest.main()
