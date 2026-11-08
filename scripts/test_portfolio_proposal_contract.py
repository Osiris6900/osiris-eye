import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from scripts.portfolio_proposal_contract import PortfolioProposal


class ProposalTests(unittest.TestCase):
    def test_risk_bound(self):
        now = datetime.now(timezone.utc)
        p = PortfolioProposal("v1", "BTC", D("2"), D("3"), now, now + timedelta(minutes=1))
        p.validate()
        with self.assertRaises(ValueError):
            PortfolioProposal("v1", "BTC", D("4"), D("3"), now, now + timedelta(minutes=1)).validate()


if __name__ == "__main__":
    unittest.main()
