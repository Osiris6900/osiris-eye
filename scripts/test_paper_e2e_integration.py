import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from scripts.paper_e2e_integration import execute_paper_proposal
from scripts.market_observation_contract import MarketObservation
from scripts.portfolio_proposal_contract import PortfolioProposal
from scripts.independent_paper_risk import PaperRiskPolicy
from scripts.paper_order_ledger import PaperLedger


class PaperIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.obs = MarketObservation("synthetic", "test", "BTC", self.now, self.now, 1, D("100"))
        self.proposal = PortfolioProposal("v1", "BTC", D("2"), D("5"), self.now, self.now + timedelta(minutes=1))
        self.policy = PaperRiskPolicy(D("500"), D("5"))
        self.ledger = PaperLedger(cash=D("1000"))

    def run_proposal(self):
        execute_paper_proposal(proposal=self.proposal, observation=self.obs,
                               policy=self.policy, ledger=self.ledger,
                               key="test-1", quantity=D("2"), side="buy", now=self.now)

    def test_successful_synthetic_flow(self):
        self.run_proposal()
        self.assertEqual(self.ledger.positions["BTC"], D("2"))
        self.assertEqual(self.ledger.cash, D("800"))

    def test_risk_rejects_without_mutating_ledger(self):
        self.policy = PaperRiskPolicy(D("100"), D("5"))
        with self.assertRaises(ValueError):
            self.run_proposal()
        self.assertEqual(self.ledger.orders, {})

    def test_halt_blocks_flow(self):
        self.ledger.halt()
        with self.assertRaises(ValueError):
            self.run_proposal()
        self.assertEqual(self.ledger.orders, {})

    def test_expired_proposal_rejected(self):
        self.proposal = PortfolioProposal("v1", "BTC", D("2"), D("5"),
                                          self.now - timedelta(minutes=2), self.now - timedelta(minutes=1))
        with self.assertRaises(ValueError):
            self.run_proposal()

    def test_wrong_instrument_rejected(self):
        self.obs = MarketObservation("synthetic", "test", "ETH", self.now, self.now, 1, D("100"))
        with self.assertRaises(ValueError):
            self.run_proposal()


if __name__ == "__main__":
    unittest.main()
