#!/usr/bin/env python3
"""Regression checks for P2P soak diagnostic accounting (offline, no network)."""
from collections import Counter
import re
import unittest

REJECTION = re.compile(r"^(key_binding_rejected|encrypted_message_rejected|revocation_sync_message_rejected)(?:_suppressed)?\s")
REASON = re.compile(r"\breason=([a-z_]{1,64})(?:\s|$)")
COUNT = re.compile(r"\bcount=(\d+)(?:\s|$)")

def summarize(lines):
    """Separate observed rejections from aggregated suppressed repetitions."""
    observed, suppressed = Counter(), Counter()
    for line in lines:
        match = REJECTION.match(line)
        if not match:
            continue
        reason = REASON.search(line)
        key = (match.group(1), reason.group(1) if reason else "unknown")
        if line.startswith(match.group(1) + "_suppressed "):
            count = COUNT.search(line)
            if count is None or int(count.group(1)) < 1:
                raise ValueError("invalid suppressed rejection count")
            suppressed[key] += int(count.group(1))
        else:
            observed[key] += 1
    return observed, suppressed

class DiagnosticTests(unittest.TestCase):
    def test_first_rejection_and_aggregate_are_separate(self):
        raw, aggregate = summarize([
            "peer_not_allowed_rejected topic=osiris/e2ee/messages/1 peer=a reason=peer_not_allowed",
            "peer_not_allowed_rejected_suppressed topic=osiris/e2ee/messages/1 reason=peer_not_allowed count=42 window_secs=60",
            "peer_not_allowed_rejected topic=osiris/e2ee/messages/1 peer=b reason=peer_not_allowed",
        ])
        self.assertEqual(raw[("peer_not_allowed_rejected", "peer_not_allowed")], 2)
        self.assertEqual(aggregate[("encrypted_message_rejected", "peer_not_allowed")], 42)
    def test_other_reasons_are_not_hidden(self):
        raw, aggregate = summarize(["key_binding_rejected reason=invalid_signature",
                                   "revocation_sync_message_rejected_suppressed reason=peer_not_allowed count=3"])
        self.assertEqual(raw[("key_binding_rejected", "invalid_signature")], 1)
        self.assertEqual(sum(aggregate.values()), 3)
    def test_invalid_aggregate_fails_closed(self):
        with self.assertRaises(ValueError):
            summarize(["key_binding_rejected_suppressed reason=peer_not_allowed count=0"])

if __name__ == "__main__":
    unittest.main()
