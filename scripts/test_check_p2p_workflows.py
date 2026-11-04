#!/usr/bin/env python3
"""Regression tests for workflow YAML/Bash parsing, without running job code."""
from pathlib import Path
import tempfile
import unittest

import yaml

from check_p2p_workflows import validate_workflow


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


if __name__ == "__main__":
    unittest.main()
