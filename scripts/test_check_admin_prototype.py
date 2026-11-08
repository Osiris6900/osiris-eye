"""Tests for offline founder-admin deployment guard."""
import unittest
from check_admin_prototype import validate_admin_prototype


class AdminGuardTests(unittest.TestCase):
    def test_required_prototype_disclosures(self):
        sample = ("PROTOTYPE ONLY no live P2P connection "
                  "Authentication not implemented Changes are local only Export draft JSON")
        validate_admin_prototype(sample)

    def test_missing_disclosure_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "admin_prototype_guard_missing"):
            validate_admin_prototype("PROTOTYPE ONLY")


if __name__ == "__main__":
    unittest.main()
