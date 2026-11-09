import tempfile
import unittest
from pathlib import Path

from check_action_pins import violations


class ActionPinTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / ".github" / "workflows").mkdir(parents=True)

    def write(self, value):
        (self.root / ".github" / "workflows" / "ci.yml").write_text(value)

    def test_full_sha_passes(self):
        self.write("steps:\n  - uses: actions/checkout@" + "a" * 40 + "\n")
        self.assertEqual(violations(self.root), [])

    def test_mutable_tag_fails(self):
        self.write("steps:\n  - uses: actions/checkout@v4\n")
        self.assertEqual(len(violations(self.root)), 1)

    def test_local_action_allowed(self):
        self.write("steps:\n  - uses: ./local-action\n")
        self.assertEqual(violations(self.root), [])

    def test_unversioned_action_fails(self):
        self.write("steps:\n  - uses: owner/action\n")
        self.assertEqual(len(violations(self.root)), 1)


if __name__ == "__main__":
    unittest.main()
