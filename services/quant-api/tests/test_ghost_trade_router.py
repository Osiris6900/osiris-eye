"""Dual-mode Ghost routing must never reach a live broker."""
from __future__ import annotations

from dataclasses import replace
import unittest

from osiris_api.ghost_trade_router import GhostTradeRequest, route_ghost_trade
from osiris_api.paper_execution_gate import PaperOrderContext


def request(mode="paper"):
    return GhostTradeRequest(
        mode=mode,
        source_consistent=True,
        peer_authenticated=True,
        provenance_verified=True,
        context=PaperOrderContext(
            quantity=1.0, price=10.0, current_position_notional=0.0,
            daily_pnl=0.0, observation_time_ms=1000, decision_time_ms=1200,
            model_approved=True, feed_authenticated=True, feed_entitled=True,
            kill_switch_active=False,
        ),
    )


class GhostDualModeTests(unittest.TestCase):
    def test_paper_authorization_is_not_fill(self):
        result = route_ghost_trade(request())
        self.assertEqual(result["status"], "paper_authorized")
        self.assertFalse(result["broker_called"])
        self.assertFalse(result["live_execution_authorized"])

    def test_live_always_blocked_even_with_all_green_checks(self):
        result = route_ghost_trade(request("live"))
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(all(result["checks"].values()))
        self.assertFalse(result["broker_called"])
        self.assertFalse(result["live_execution_authorized"])

    def test_paper_rejects_unverified_provenance(self):
        req = replace(request(), provenance_verified=False)
        self.assertEqual(route_ghost_trade(req)["reason"], "provenance_verified_required")

    def test_paper_rejects_untrusted_peer(self):
        req = replace(request(), peer_authenticated=False)
        self.assertEqual(route_ghost_trade(req)["status"], "rejected")

    def test_paper_rejects_kill_switch(self):
        req = replace(request(), context=replace(request().context, kill_switch_active=True))
        self.assertEqual(route_ghost_trade(req)["status"], "rejected")

    def test_unknown_mode_rejected(self):
        self.assertEqual(route_ghost_trade(request("broker"))["status"], "rejected")


if __name__ == "__main__":
    unittest.main()
