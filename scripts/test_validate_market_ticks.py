import unittest
from validate_market_ticks import validate_ticks

class TickTests(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(len(validate_ticks([{"symbol":"BTC","timestamp_ms":100,"price":"12.5"}], 10, 105)), 1)
    def test_stale(self):
        with self.assertRaises(ValueError):
            validate_ticks([{"symbol":"BTC","timestamp_ms":90,"price":"12.5"}], 10, 105)
    def test_duplicate(self):
        tick={"symbol":"BTC","timestamp_ms":100,"price":"12.5"}
        with self.assertRaises(ValueError):
            validate_ticks([tick,tick], 10, 105)
    def test_nan(self):
        with self.assertRaises(ValueError):
            validate_ticks([{"symbol":"BTC","timestamp_ms":100,"price":"NaN"}], 10, 105)
if __name__ == "__main__": unittest.main()
