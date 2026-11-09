"""Inventory rules for the release-gate status manifest."""
import copy
import json
import unittest
from pathlib import Path

from check_release_gate_status import (
    DOC_PATH,
    MANIFEST_PATH,
    ROOT,
    collect_facts,
    validate,
    validate_document,
)


class ReleaseGateInventoryTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        self.facts = collect_facts(ROOT)

    def test_checked_in_inventory_matches_worktree_and_doc(self):
        self.assertEqual(validate(self.manifest, self.facts), [])
        document = DOC_PATH.read_text(encoding="utf-8")
        self.assertEqual(validate_document(self.manifest, document), [])
        self.assertIn("5176eae8843a1e5144ac903f37843fe9bb46cdd9", document)

    def test_only_evidenced_rows_are_implemented(self):
        implemented = [item["id"] for item in self.manifest["items"] if item["status"] == "Implemented with evidence"]
        self.assertEqual(implemented, ["T02"])
        self.assertFalse(self.manifest["live_trading_authorized"])

    def test_implemented_without_ci_run_is_rejected(self):
        manifest = copy.deepcopy(self.manifest)
        manifest["items"][1]["evidence"] = ["docs/ROADMAP.md"]
        errors = validate(manifest, self.facts)
        self.assertTrue(any("CI run URL" in error for error in errors))

    def test_partial_without_blocker_is_rejected(self):
        manifest = copy.deepcopy(self.manifest)
        manifest["items"][0]["blocker"] = " "
        self.assertTrue(any("T01" in error for error in validate(manifest, self.facts)))

    def test_false_acceptance_flag_is_rejected(self):
        manifest = copy.deepcopy(self.manifest)
        manifest["items"][0]["status"] = "Implemented with evidence"
        errors = validate(manifest, self.facts)
        self.assertTrue(any("acceptance_satisfied" in error for error in errors))

    def test_stale_mutable_action_count_is_rejected(self):
        facts = copy.deepcopy(self.facts)
        facts["mutable_action_uses"] = 59
        self.assertTrue(any("mutable_action_uses" in error for error in validate(self.manifest, facts)))

    def test_document_must_repeat_status(self):
        self.assertTrue(any("T02" in error for error in validate_document(self.manifest, "T02 missing")))


if __name__ == "__main__":
    unittest.main()
