"""Stacked workstream PRs need the same read-only build/security coverage."""

import unittest
from fnmatch import fnmatchcase
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


class IntegrationTargets(unittest.TestCase):
    def test_core_workflows_cover_main_and_nested_workstream_targets(self):
        for name in (
            "ci.yml",
            "dependency-security.yml",
            "model-research-evidence.yml",
        ):
            with self.subTest(workflow=name):
                workflow = yaml.load(
                    (ROOT / ".github/workflows" / name).read_text(),
                    Loader=yaml.BaseLoader,
                )
                patterns = workflow["on"]["pull_request"]["branches"]
                for branch in (
                    "main",
                    "codex/platform-20-integration-20261111",
                    "codex/dashboard-api-integration-20261112",
                ):
                    self.assertTrue(
                        any(fnmatchcase(branch, pattern) for pattern in patterns),
                        branch,
                    )
                self.assertEqual(workflow["permissions"], {"contents": "read"})


if __name__ == "__main__":
    unittest.main()
