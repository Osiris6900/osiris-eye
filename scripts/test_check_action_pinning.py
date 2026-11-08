import tempfile
import unittest
from pathlib import Path
from check_action_pinning import inspect, check

class PinningTests(unittest.TestCase):
    def test_full_sha_and_local_action(self):
        data = "permissions:\n  contents: read\nsteps:\n  - uses: actions/checkout@" + "a"*40 + "\n  - uses: ./local\n"
        self.assertEqual(inspect(data, "ci.yml"), [])

    def test_mutable_ref_rejected(self):
        self.assertTrue(inspect("permissions:\n  contents: read\n- uses: actions/checkout@v4\n", "ci.yml"))

    def test_unpinned_docker_rejected(self):
        self.assertTrue(inspect("permissions:\n  contents: read\n- uses: docker://alpine:latest\n", "ci.yml"))

    def test_missing_permissions_rejected(self):
        self.assertTrue(inspect("- uses: actions/checkout@" + "a"*40, "ci.yml"))

    def test_empty_workflow_dir_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertTrue(check(temp))

    def test_scan_all_yaml_extensions(self):
        with tempfile.TemporaryDirectory() as temp:
            Path(temp, "one.yml").write_text("permissions:\n  contents: read\n")
            Path(temp, "two.yaml").write_text("permissions:\n  contents: read\n- uses: actions/setup-python@v5\n")
            self.assertTrue(check(temp))

if __name__ == "__main__":
    unittest.main()
