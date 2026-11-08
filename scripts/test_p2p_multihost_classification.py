#!/usr/bin/env python3
"""Offline regression tests for the multi-host evidence classification boundary."""
import unittest
from run_p2p_soak import measurement_class

class HostClassificationTests(unittest.TestCase):
    def test_distinct_hosts(self):
        m = {"a": {"host_fingerprint": "host-one"},
             "b": {"host_fingerprint": "host-two"}}
        self.assertEqual(measurement_class(m, False), "operator_attested_real_multi_host")
    def test_same_host_cannot_claim_multihost(self):
        m = {"a": {"host_fingerprint": "same"}, "b": {"host_fingerprint": "same"}}
        self.assertEqual(measurement_class(m, False), "single_host_process_regression")
    def test_local_agents_cannot_claim_multihost(self):
        m = {"a": {"host_fingerprint": "one"}, "b": {"host_fingerprint": "two"}}
        self.assertEqual(measurement_class(m, True), "single_host_process_regression")
    def test_one_host_cannot_claim_multihost(self):
        self.assertEqual(measurement_class({"a": {"host_fingerprint": "one"}}, False),
                         "single_host_process_regression")

if __name__ == "__main__":
    unittest.main()
