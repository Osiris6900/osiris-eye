import json
import unittest
from paper_order_journal import apply_command, recover


class PaperOrderJournalTests(unittest.TestCase):
    def initial(self):
        return {"starting_cash": "1000", "orders": [], "fills": [], "commands": {},
                "kill_switch": False, "market_data_fresh": True}

    def order(self):
        return {"command_id": "c1", "type": "order",
                "order": {"id": "o1", "symbol": "BTC", "side": "buy", "quantity": "2"}}

    def fill(self):
        return {"command_id": "c2", "type": "fill",
                "fill": {"id": "f1", "order_id": "o1", "quantity": "1", "price": "100"}}

    def test_restart_replay_and_idempotency(self):
        first, _ = apply_command(self.initial(), self.order())
        second, result = apply_command(first, self.fill())
        self.assertEqual(result["ledger"]["cash"], "900")
        restored = json.loads(json.dumps(second))
        self.assertEqual(recover(restored)["positions"]["BTC"], "1")
        unchanged, duplicate = apply_command(restored, self.fill())
        self.assertEqual(unchanged, restored)
        self.assertEqual(duplicate["status"], "duplicate")

    def test_reused_key_with_different_payload_fails(self):
        state, _ = apply_command(self.initial(), self.order())
        altered = self.order()
        altered["order"]["quantity"] = "3"
        with self.assertRaises(ValueError):
            apply_command(state, altered)

    def test_stale_feed_and_kill_switch_fail_closed(self):
        for key, value in [("market_data_fresh", False), ("kill_switch", True)]:
            state = self.initial()
            state[key] = value
            with self.assertRaises(ValueError):
                apply_command(state, self.order())
            self.assertEqual(state["orders"], [])

    def test_overfill_rejected_without_mutation(self):
        state, _ = apply_command(self.initial(), self.order())
        fill = self.fill()
        fill["fill"]["quantity"] = "3"
        with self.assertRaises(ValueError):
            apply_command(state, fill)
        self.assertEqual(state["fills"], [])

    def test_kill_switch_after_fill_blocks_new_commands(self):
        state, _ = apply_command(self.initial(), self.order())
        state, _ = apply_command(state, self.fill())
        state["kill_switch"] = True
        with self.assertRaises(ValueError):
            apply_command(state, {"command_id": "c3", "type": "order",
                                  "order": {"id": "o2", "symbol": "BTC", "side": "buy", "quantity": "1"}})

    def test_stale_feed_after_fill_blocks_new_commands(self):
        state, _ = apply_command(self.initial(), self.order())
        state, _ = apply_command(state, self.fill())
        state["market_data_fresh"] = False
        with self.assertRaises(ValueError):
            apply_command(state, {"command_id": "c3", "type": "order",
                                  "order": {"id": "o2", "symbol": "BTC", "side": "buy", "quantity": "1"}})

    def test_recovery_preserves_historical_fills_after_emergency_halt(self):
        state, _ = apply_command(self.initial(), self.order())
        state, _ = apply_command(state, self.fill())
        state["kill_switch"] = True
        state["market_data_fresh"] = False
        restored = json.loads(json.dumps(state))
        self.assertEqual(recover(restored)["cash"], "900")
        self.assertEqual(recover(restored)["positions"]["BTC"], "1")
        with self.assertRaises(ValueError):
            apply_command(restored, {"command_id": "c3", "type": "order",
                                    "order": {"id": "o2", "symbol": "BTC", "side": "buy", "quantity": "1"}})

    def test_corrupt_restart_snapshot_rejected(self):
        state, _ = apply_command(self.initial(), self.order())
        state["commands"]["c1"]["command"]["command_id"] = "tampered"
        with self.assertRaises(ValueError):
            recover(state)


if __name__ == "__main__":
    unittest.main()
