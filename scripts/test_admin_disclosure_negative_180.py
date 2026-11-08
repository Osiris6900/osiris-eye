"""Security regression tests: offline admin must never be mislabeled as live."""
import unittest

from check_admin_prototype import validate_admin_prototype


class AdminDisclosureNegativeTests(unittest.TestCase):
    REQUIRED = (
        "PROTOTYPE ONLY",
        "no live P2P connection",
        "Authentication not implemented",
        "Changes are local only",
        "Export draft JSON",
    )

    def test_each_disclosure_is_individually_mandatory(self):
        for missing in self.REQUIRED:
            with self.subTest(missing=missing):
                html = " | ".join(x for x in self.REQUIRED if x != missing)
                with self.assertRaisesRegex(ValueError, "admin_prototype_guard_missing"):
                    validate_admin_prototype(html)

    def test_complete_disclosure_passes(self):
        validate_admin_prototype(" | ".join(self.REQUIRED))


if __name__ == "__main__":
    unittest.main()
