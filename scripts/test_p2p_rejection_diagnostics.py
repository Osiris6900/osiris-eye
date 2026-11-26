#!/usr/bin/env python3
"""Soak diagnostic accounting for the production rejection throttle."""
import unittest

from run_p2p_soak import classify_node_diagnostic


class DiagnosticTests(unittest.TestCase):
    def test_visible_and_suppressed_lines_stay_separate(self):
        visible = classify_node_diagnostic(
            "peer_not_allowed_rejected topic=osiris/e2ee/messages/1 peer=a reason=peer_not_allowed"
        )
        aggregate = classify_node_diagnostic(
            "peer_not_allowed_rejected_suppressed topic=osiris/e2ee/messages/1 "
            "peer=a reason=peer_not_allowed count=42 window_secs=60"
        )
        other = classify_node_diagnostic(
            "peer_not_allowed_rejected topic=osiris/e2ee/key-bindings/1 peer=b reason=peer_not_allowed"
        )
        self.assertEqual(visible, ("peer_not_allowed_rejected:peer_not_allowed", 1))
        self.assertEqual(aggregate, ("peer_not_allowed_rejected_suppressed:peer_not_allowed", 42))
        self.assertEqual(other, ("peer_not_allowed_rejected:peer_not_allowed", 1))

    def test_non_allowlist_rejections_are_not_rewritten(self):
        observed = classify_node_diagnostic("key_binding_rejected peer=a reason=invalid_signature")
        self.assertEqual(observed, ("key_binding_rejected:invalid_signature", 1))
        self.assertIsNone(classify_node_diagnostic("listener_ready address=/ip4/127.0.0.1/tcp/1"))

    def test_invalid_aggregate_fails_closed(self):
        for line in (
            "peer_not_allowed_rejected_suppressed reason=peer_not_allowed count=0 window_secs=60",
            "peer_not_allowed_rejected_suppressed reason=peer_not_allowed window_secs=60",
            "outbound_observation_rearmed recipient=a count=0",
        ):
            with self.assertRaises(ValueError):
                classify_node_diagnostic(line)

    def test_queue_rearm_accounts_for_every_advanced_row(self):
        self.assertEqual(classify_node_diagnostic("outbound_observation_rearmed recipient=a count=3"),
                         ("outbound_observation_rearmed", 3))

    def test_retry_attempt_is_counted_separately_from_delivery(self):
        digest = 'a' * 64
        line = f"outbound_observation_retry id=7 recipient=a attempt=2 observation_sha256={digest}"
        self.assertEqual(classify_node_diagnostic(line), ("outbound_observation_retry", 1))


if __name__ == "__main__":
    unittest.main()
