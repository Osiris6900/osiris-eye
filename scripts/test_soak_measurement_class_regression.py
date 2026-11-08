"""Regression: local simulations must never be reported as independent hosts."""
import importlib.util
from pathlib import Path
import unittest


SCRIPT = Path(__file__).with_name("run_p2p_soak.py")
spec = importlib.util.spec_from_file_location("run_p2p_soak", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class MeasurementClassRegression(unittest.TestCase):
    def test_distinct_attested_remote_hosts(self):
        hosts = {"a": {"host_fingerprint": "host-a"}, "b": {"host_fingerprint": "host-b"}}
        self.assertEqual(module.measurement_class(hosts, False), "operator_attested_real_multi_host")

    def test_same_host_is_not_independent(self):
        hosts = {"a": {"host_fingerprint": "same"}, "b": {"host_fingerprint": "same"}}
        self.assertEqual(module.measurement_class(hosts, False), "single_host_process_regression")

    def test_local_agents_never_claim_cross_host(self):
        hosts = {"a": {"host_fingerprint": "host-a"}, "b": {"host_fingerprint": "host-b"}}
        self.assertEqual(module.measurement_class(hosts, True), "single_host_process_regression")

    def test_missing_or_blank_fingerprints_fail_closed(self):
        for fingerprints in [("", "b"), (None, "b"), ("a", None)]:
            hosts = {str(i): {"host_fingerprint": fingerprint} for i, fingerprint in enumerate(fingerprints)}
            self.assertEqual(module.measurement_class(hosts, False), "single_host_process_regression")


if __name__ == "__main__":
    unittest.main()
