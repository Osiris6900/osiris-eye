#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_license_provenance as gate

HASH = "e" * 64

POLICY = {
    "schema": "osiris.license-policy.v1",
    "allowed_expressions": ["MIT", "Apache-2.0", "BSD-3-Clause"],
    "overrides": {},
}

LOCK_BODY = f"""fastapi==0.115.0 \\
    --hash=sha256:{HASH}
websockets==13.1 \\
    --hash=sha256:{HASH}
"""


def evidence_for(lock_digest: str, packages: list[dict]) -> dict:
    return {
        "schema": gate.SCHEMA,
        "source": {"repository": "Osiris6900/osiris-eye", "commit": "1" * 40},
        "lock": {"path": "services/quant-api/requirements-lock.txt", "sha256": lock_digest},
        "metadata": {"service": "PyPI JSON API", "queried_at": "2026-11-08T00:00:00Z"},
        "packages": packages,
    }


def record(name, version, expression):
    return {
        "name": name,
        "normalized_name": gate.normalize_name(name),
        "version": version,
        "license_expression": expression,
        "license_classifiers": [],
        "license_legacy": None,
        "resolved_expression": expression,
    }


class LicenseFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.lock = self.root / "requirements-lock.txt"
        self.lock.write_text(LOCK_BODY)
        self.digest = hashlib.sha256(LOCK_BODY.encode()).hexdigest()
        self.policy = self.root / "policy.json"
        self.policy.write_text(json.dumps(POLICY))
        self.evidence = self.root / "evidence.json"

    def tearDown(self):
        self.temporary.cleanup()

    def write_evidence(self, packages, digest=None):
        self.evidence.write_text(json.dumps(evidence_for(digest or self.digest, packages)))


class ExpressionTests(unittest.TestCase):
    def test_aliases_normalize(self):
        self.assertEqual(gate.normalize_license("MIT License"), "MIT")
        self.assertEqual(gate.normalize_license("Apache Software License"), "Apache-2.0")
        self.assertEqual(gate.normalize_license("BSD License"), "BSD-3-Clause")
        self.assertEqual(gate.normalize_license("GPL-3.0-only"), "GPL-3.0")
        self.assertIsNone(gate.normalize_license("some unheard-of license"))

    def test_or_expression_allowed_when_any_branch_allowed(self):
        self.assertTrue(gate.expression_allowed("Apache-2.0 OR BSD-3-Clause", {"BSD-3-Clause"}))
        self.assertFalse(gate.expression_allowed("GPL-3.0 OR Proprietary", {"MIT"}))

    def test_and_expression_requires_all_branches(self):
        self.assertTrue(gate.expression_allowed("MIT AND Apache-2.0", {"MIT", "Apache-2.0"}))
        self.assertFalse(gate.expression_allowed("MIT AND Apache-2.0", {"MIT"}))

    def test_unknown_expression_denied(self):
        self.assertFalse(gate.expression_allowed("WTFPL", {"MIT"}))

    def test_mixed_operators_rejected(self):
        with self.assertRaisesRegex(ValueError, "mixed"):
            gate.expression_allowed("MIT OR Apache-2.0 AND BSD-3-Clause", {"MIT"})

    def test_policy_requires_reason_for_overrides(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "policy.json"
            path.write_text(json.dumps({
                "schema": gate.POLICY_SCHEMA,
                "allowed_expressions": ["MIT"],
                "overrides": {"demo": {"expression": "MIT"}},
            }))
            with self.assertRaisesRegex(ValueError, "reason"):
                gate.load_policy(path)


class GateTests(LicenseFixture):
    def test_valid_evidence_passes(self):
        self.write_evidence([
            record("fastapi", "0.115.0", "MIT"),
            record("websockets", "13.1", "BSD-3-Clause"),
        ])
        performed = gate.check_evidence(self.lock, self.evidence, self.policy)
        self.assertTrue(any("2 locked distributions" in line for line in performed))

    def test_dual_or_expression_passes(self):
        self.write_evidence([
            record("fastapi", "0.115.0", "Apache-2.0 OR BSD-3-Clause"),
            record("websockets", "13.1", "MIT"),
        ])
        gate.check_evidence(self.lock, self.evidence, self.policy)

    def test_missing_evidence_record_fails_closed(self):
        self.write_evidence([record("fastapi", "0.115.0", "MIT")])
        with self.assertRaisesRegex(ValueError, "websockets: missing license evidence"):
            gate.check_evidence(self.lock, self.evidence, self.policy)

    def test_version_mismatch_fails_closed(self):
        self.write_evidence([
            record("fastapi", "0.114.0", "MIT"),
            record("websockets", "13.1", "MIT"),
        ])
        with self.assertRaisesRegex(ValueError, "evidence version"):
            gate.check_evidence(self.lock, self.evidence, self.policy)

    def test_disallowed_license_fails_closed(self):
        self.write_evidence([
            record("fastapi", "0.115.0", "GPL-3.0"),
            record("websockets", "13.1", "MIT"),
        ])
        with self.assertRaisesRegex(ValueError, "not policy-allowed"):
            gate.check_evidence(self.lock, self.evidence, self.policy)

    def test_missing_expression_fails_closed(self):
        self.write_evidence([
            record("fastapi", "0.115.0", None),
            record("websockets", "13.1", "MIT"),
        ])
        with self.assertRaisesRegex(ValueError, "no usable license expression"):
            gate.check_evidence(self.lock, self.evidence, self.policy)

    def test_documented_override_covers_missing_metadata(self):
        self.policy.write_text(json.dumps({
            **POLICY,
            "overrides": {"fastapi": {"expression": "MIT", "reason": "upstream LICENSE file is MIT (reviewed)"}},
        }))
        self.write_evidence([
            record("fastapi", "0.115.0", None),
            record("websockets", "13.1", "MIT"),
        ])
        gate.check_evidence(self.lock, self.evidence, self.policy)

    def test_stale_evidence_fails_closed(self):
        self.write_evidence([
            record("fastapi", "0.115.0", "MIT"),
            record("websockets", "13.1", "MIT"),
        ], digest="0" * 64)
        with self.assertRaisesRegex(ValueError, "stale evidence"):
            gate.check_evidence(self.lock, self.evidence, self.policy)

    def test_extra_evidence_records_fail_closed(self):
        self.write_evidence([
            record("fastapi", "0.115.0", "MIT"),
            record("websockets", "13.1", "MIT"),
            record("numpy", "2.0.0", "BSD-3-Clause"),
        ])
        with self.assertRaisesRegex(ValueError, "not present in lock"):
            gate.check_evidence(self.lock, self.evidence, self.policy)

    def test_classifier_fallback_used_when_expression_missing(self):
        fallback = record("websockets", "13.1", None)
        fallback["license_classifiers"] = ["BSD License"]
        fallback["resolved_expression"] = "BSD License"
        self.write_evidence([record("fastapi", "0.115.0", "MIT"), fallback])
        gate.check_evidence(self.lock, self.evidence, self.policy)


class MetadataExtractionTests(unittest.TestCase):
    def test_modern_expression_preferred(self):
        info = {
            "license_expression": "MIT",
            "license": "MIT License",
            "classifiers": ["License :: OSI Approved :: MIT License"],
        }
        fields = gate.metadata_license_fields(info)
        self.assertEqual(fields["license_expression"], "MIT")
        self.assertEqual(gate.best_expression(fields), "MIT")

    def test_long_legacy_license_text_ignored(self):
        info = {"license_expression": None, "license": "x" * 500, "classifiers": []}
        fields = gate.metadata_license_fields(info)
        self.assertIsNone(fields["license_legacy"])
        self.assertIsNone(gate.best_expression(fields))

    def test_repo_gate_passes_on_checked_in_files(self):
        root = Path(__file__).resolve().parents[1]
        performed = gate.check_evidence(
            root / "services/quant-api/requirements-lock.txt",
            root / "docs/PYTHON_LICENSE_PROVENANCE.json",
            root / "config/license_policy.json",
        )
        self.assertTrue(performed)


if __name__ == "__main__":
    unittest.main(verbosity=2)
