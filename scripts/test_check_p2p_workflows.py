#!/usr/bin/env python3
"""Offline regression tests for workflow parsing and action-pin ratcheting."""
import json
from pathlib import Path
import tempfile
import unittest

import yaml

from check_p2p_workflows import (
    action_pin_error,
    audit_action_pins,
    unpinned_action_references,
    validate_workflow,
)


class WorkflowValidationTests(unittest.TestCase):
    def check(self, content: str) -> int:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "workflow.yml"
            path.write_text(content)
            return validate_workflow(path)

    def test_expression_is_inert_and_shell_is_never_executed(self):
        self.assertEqual(self.check("""jobs:
  test:
    steps:
      - run: |
          printf '%s\\n' '${{ github.run_id }}'
          exit 42
"""), 1)

    def test_multiline_printf_that_escapes_yaml_block_is_rejected(self):
        with self.assertRaises(yaml.YAMLError):
            self.check("""jobs:
  test:
    steps:
      - run: |
          printf '%s
' value
""")

    def test_invalid_shell_block_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "shell syntax"):
            self.check("jobs:\n  test:\n    steps:\n      - run: 'if true; then echo missing-fi'\n")

    def test_duplicate_gate_is_rejected(self):
        with self.assertRaisesRegex(yaml.YAMLError, "duplicate key"):
            self.check("jobs:\n  test:\n    if: false\n    if: true\n    steps: []\n")

    def test_non_shell_step_and_reusable_workflow_are_not_sent_to_bash(self):
        self.assertEqual(self.check("""jobs:
  reusable:
    uses: owner/repo/.github/workflows/check.yml@main
  test:
    steps:
      - shell: python
        run: print('not bash syntax')
"""), 0)


class ActionPinningTests(unittest.TestCase):
    def write_baseline(self, directory: str, allowed: dict[str, int]) -> Path:
        path = Path(directory) / "baseline.json"
        path.write_text(json.dumps({
            "schema": "osiris.ci-action-pin-baseline.v1",
            "allowed_unpinned": allowed,
        }))
        return path

    def test_remote_actions_require_full_commit_shas(self):
        accepted = [
            "./.github/actions/local-check",
            "owner/repo@0123456789abcdef0123456789ABCDEF01234567",
            "owner/repo/.github/workflows/check.yml@0123456789abcdef0123456789abcdef01234567",
            "docker://alpine@sha256:" + "a" * 64,
        ]
        rejected = [
            "owner/repo",
            "owner/repo@",
            "owner/repo@main",
            "owner/repo@v4",
            "owner/repo@0123456789abcdef",
            "docker://alpine:latest",
            "docker://alpine@sha256:abc",
            "",
        ]
        for reference in accepted:
            with self.subTest(accepted=reference):
                self.assertIsNone(action_pin_error(reference))
        for reference in rejected:
            with self.subTest(rejected=reference):
                self.assertIsNotNone(action_pin_error(reference))

    def test_audit_includes_reusable_jobs_and_ignores_local_or_pinned_steps(self):
        content = """jobs:
  reusable:
    uses: owner/repo/.github/workflows/check.yml@main
  test:
    steps:
      - uses: ./.github/actions/local-check
      - uses: owner/repo@0123456789abcdef0123456789abcdef01234567
      - uses: actions/checkout@v4
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "workflow.yml"
            path.write_text(content)
            self.assertEqual(
                unpinned_action_references(path),
                ["owner/repo/.github/workflows/check.yml@main", "actions/checkout@v4"],
            )

    def test_baseline_allows_debt_to_shrink(self):
        with tempfile.TemporaryDirectory() as directory:
            workflow = Path(directory) / "workflow.yml"
            workflow.write_text("jobs:\n  test:\n    steps: []\n")
            baseline = self.write_baseline(directory, {"actions/checkout@v4": 2})
            self.assertEqual(sum(audit_action_pins([workflow], baseline).values()), 0)

    def test_new_or_changed_mutable_reference_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            workflow = Path(directory) / "workflow.yml"
            workflow.write_text("""jobs:
  test:
    steps:
      - uses: actions/checkout@v5
""")
            baseline = self.write_baseline(directory, {"actions/checkout@v4": 1})
            with self.assertRaisesRegex(ValueError, "new unpinned reference"):
                audit_action_pins([workflow], baseline)

    def test_increased_occurrence_count_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            workflow = Path(directory) / "workflow.yml"
            workflow.write_text("""jobs:
  test:
    steps:
      - uses: actions/checkout@v4
      - uses: actions/checkout@v4
""")
            baseline = self.write_baseline(directory, {"actions/checkout@v4": 1})
            with self.assertRaisesRegex(ValueError, "baseline allows 1"):
                audit_action_pins([workflow], baseline)

    def test_checked_in_workflows_stay_within_their_baseline(self):
        root = Path(__file__).resolve().parents[1]
        paths = sorted((root / ".github/workflows").glob("*.y*ml"))
        audit_action_pins(paths, root / "config/ci_action_pin_baseline.json")


if __name__ == "__main__":
    unittest.main()
