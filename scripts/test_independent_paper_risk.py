import unittest
from decimal import Decimal as D
from scripts.independent_paper_risk import PaperRiskPolicy


class PaperRiskTests(unittest.TestCase):
    def test_limits_and_halt(self):
        policy = PaperRiskPolicy(D("1000"), D("5"))
        policy.check(side="buy", quantity=D("2"), price=D("100"), current_position=D("1"), halted=False, stale=False)
        for args in [
            dict(side="buy", quantity=D("6"), price=D("100"), current_position=D("0"), halted=False, stale=False),
            dict(side="buy", quantity=D("2"), price=D("100"), current_position=D("1"), halted=True, stale=False),
            dict(side="buy", quantity=D("2"), price=D("100"), current_position=D("1"), halted=False, stale=True),
        ]:
            with self.assertRaises(ValueError):
                policy.check(**args)


if __name__ == "__main__":
    unittest.main()
