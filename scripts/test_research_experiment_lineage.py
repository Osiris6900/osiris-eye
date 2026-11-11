import unittest
from research_experiment_lineage import register_experiments


def experiment(eid="a", start=0, end=100):
    return {"experiment_id": eid, "commit_sha": "a" * 40,
            "dataset_sha256": "b" * 64, "parameters": {"lookback": 10},
            "window_start_ms": start, "window_end_ms": end}


class RegistryTests(unittest.TestCase):
    def test_non_string_parameter_keys_rejected(self):
        for parameters in ({1: "value"}, {"nested": {1: "value"}}):
            e = experiment()
            e["parameters"] = parameters
            self.assertEqual(register_experiments([e])["reason"], "invalid_parameters")

    def test_deep_or_recursive_parameters_rejected(self):
        deep = {}
        for _ in range(40):
            deep = {"nested": deep}
        recursive = {}
        recursive["loop"] = recursive
        for parameters in (deep, recursive):
            e = experiment()
            e["parameters"] = parameters
            self.assertEqual(register_experiments([e])["reason"], "invalid_parameters")

    def test_fingerprint_deterministic(self):
        a = register_experiments([experiment()])
        b = register_experiments([experiment()])
        self.assertEqual(a, b)
        self.assertFalse(a["model_approved"])
    def test_duplicate_id_fails(self):
        self.assertEqual(register_experiments([experiment(), experiment()])["status"], "rejected")
    def test_same_inputs_different_id_fails(self):
        self.assertEqual(register_experiments([experiment("a"), experiment("b")])["reason"],
                         "duplicate_experiment_fingerprint")
    def test_distinct_window(self):
        self.assertEqual(register_experiments([experiment("a"), experiment("b", 100, 200)])["status"],
                         "registered_in_memory")
    def test_invalid_window(self):
        self.assertEqual(register_experiments([experiment(end=0)])["reason"],
                         "invalid_experiment_window")
    def test_invalid_hash(self):
        e = experiment()
        e["dataset_sha256"] = "bad"
        self.assertEqual(register_experiments([e])["status"], "rejected")


if __name__ == "__main__":
    unittest.main()
