"""P2P soak measurement classification and tail-latency regression tests (#176)."""
import unittest

from run_p2p_soak import measurement_class, percentile


class SoakAcceptanceRegressions(unittest.TestCase):
    def test_no_hosts_is_not_attested_multi_host(self):
        self.assertNotEqual(measurement_class({}, False), "operator_attested_real_multi_host")

    def test_two_distinct_hosts_can_be_attested(self):
        hosts = {
            "host-a": {"host_fingerprint": "machine-a"},
            "host-b": {"host_fingerprint": "machine-b"},
        }
        self.assertEqual(measurement_class(hosts, False), "operator_attested_real_multi_host")

    def test_duplicate_host_fingerprints_cannot_fake_multi_host(self):
        hosts = {
            "host-a": {"host_fingerprint": "same-machine"},
            "host-b": {"host_fingerprint": "same-machine"},
        }
        self.assertNotEqual(measurement_class(hosts, False), "operator_attested_real_multi_host")

    def test_nearest_rank_p99_preserves_extreme_tail(self):
        samples = [1] * 99 + [5000]
        self.assertEqual(percentile(samples, 0.99), 1)
        self.assertEqual(percentile(samples, 1.0), 5000)


if __name__ == "__main__":
    unittest.main()
