"""Validate that three-stage receipt evidence is bound to its real file bytes.

These are offline contract tests, never a production/remote E2E acceptance.
"""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from check_cross_module_acceptance import STAGE_SCHEMA, STAGES, validate

SHA = "a" * 40
URL = "https://github.com/Osiris6900/osiris-eye/actions/runs/123"


def encode(value):
    return (json.dumps(value, sort_keys=True) + "\n").encode()


class CrossModuleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.report = {
            "commit_sha": SHA, "live_execution_enabled": False,
            "t19_gate_accepted": False, "stages": {},
        }
        previous = None
        for i, name in enumerate(STAGES):
            rel = f"{name}.json"
            payload = {
                "schema": STAGE_SCHEMA,
                "stage": name,
                "commit_sha": SHA,
                "ci_run_url": URL,
                "passed": True,
                "output_receipt_id": f"receipt-{i}",
                "input_receipt_id": previous,
                "live_execution_enabled": False,
                "broker_order_submitted": False,
                "mode": "paper_only",
                "paper_fill_simulated": name == "paper_execution",
            }
            content = encode(payload)
            (self.root / rel).write_bytes(content)
            self.report["stages"][name] = {
                "passed": True, "commit_sha": SHA, "ci_run_url": URL,
                "artifact": rel, "sha256": hashlib.sha256(content).hexdigest(),
                "output_receipt_id": payload["output_receipt_id"],
                "input_receipt_id": previous,
            }
            previous = payload["output_receipt_id"]

    def rewrite_payload(self, name, **changes):
        file = self.root / f"{name}.json"
        payload = json.loads(file.read_text())
        payload.update(changes)
        content = encode(payload)
        file.write_bytes(content)
        self.report["stages"][name]["sha256"] = hashlib.sha256(content).hexdigest()

    def test_valid_contract_does_not_accept_release_gate(self):
        self.assertEqual(validate(self.report, self.root), [])
        self.assertFalse(self.report["t19_gate_accepted"])

    def test_missing_stage(self):
        del self.report["stages"]["paper_execution"]
        self.assertTrue(validate(self.report, self.root))

    def test_receipt_mismatch(self):
        self.report["stages"]["paper_execution"]["input_receipt_id"] = "wrong"
        self.assertTrue(validate(self.report, self.root))

    def test_tampered_artifact(self):
        (self.root / "python_ledger_import.json").write_text("tampered")
        self.assertTrue(validate(self.report, self.root))

    def test_forged_manifest_receipt_rejected_even_if_file_unchanged(self):
        self.report["stages"]["rust_p2p_receipt"]["output_receipt_id"] = "forged"
        errors = validate(self.report, self.root)
        self.assertTrue(any("payload output_receipt_id" in x for x in errors))

    def test_changed_stage_payload_with_updated_hash_still_fails(self):
        self.rewrite_payload("python_ledger_import", stage="rust_p2p_receipt")
        self.assertTrue(validate(self.report, self.root))

    def test_different_ci_runs_cannot_be_assembled_as_one_chain(self):
        self.rewrite_payload(
            "python_ledger_import",
            ci_run_url="https://github.com/Osiris6900/osiris-eye/actions/runs/456",
        )
        self.report["stages"]["python_ledger_import"]["ci_run_url"] = \
            "https://github.com/Osiris6900/osiris-eye/actions/runs/456"
        self.assertIn(
            "python_ledger_import: CI run URL differs across stages",
            validate(self.report, self.root),
        )

    def test_paper_execution_claim_cannot_enable_broker(self):
        self.rewrite_payload("paper_execution", broker_order_submitted=True)
        self.assertTrue(validate(self.report, self.root))

    def test_paper_fill_claim_required(self):
        self.rewrite_payload("paper_execution", paper_fill_simulated=False)
        self.assertTrue(validate(self.report, self.root))

    def test_invalid_json_with_recomputed_hash_is_rejected(self):
        raw = b"not json"
        (self.root / "paper_execution.json").write_bytes(raw)
        self.report["stages"]["paper_execution"]["sha256"] = \
            hashlib.sha256(raw).hexdigest()
        self.assertTrue(validate(self.report, self.root))

    def test_path_traversal(self):
        self.report["stages"]["paper_execution"]["artifact"] = "../outside"
        self.assertTrue(validate(self.report, self.root))

    def test_live_execution(self):
        self.report["live_execution_enabled"] = True
        self.assertTrue(validate(self.report, self.root))

    def test_forged_gate_acceptance(self):
        self.report["t19_gate_accepted"] = True
        self.assertTrue(validate(self.report, self.root))

    def test_empty_and_oversized_stage(self):
        file = self.root / "paper_execution.json"
        file.write_bytes(b"")
        self.assertTrue(validate(self.report, self.root))
        file.write_bytes(b"x" * (1_048_576 + 1))
        self.assertTrue(validate(self.report, self.root))


if __name__ == "__main__":
    unittest.main()
