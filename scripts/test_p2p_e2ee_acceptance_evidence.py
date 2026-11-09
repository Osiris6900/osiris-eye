#!/usr/bin/env python3
"""Offline checks for recipient-E2EE evidence retention."""
import hashlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from p2p_e2ee_acceptance_evidence import (
    DISTINCT_BOOT_IDS,
    LOCAL_OR_SAME_HOST,
    AcceptanceEvidence,
    measurement_class,
    parse_sha256sum,
    remote_file_sha256,
)


class Completed:
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _git_runner(args, check=False, capture_output=False, text=False, timeout=None):
    if args[:2] == ["git", "rev-parse"]:
        return Completed(0, "1234567890abcdef1234567890abcdef12345678\n")
    if args[:2] == ["git", "status"]:
        return Completed(0, "")
    raise AssertionError(args)


class EvidenceTests(unittest.TestCase):
    def test_same_boot_id_is_not_labeled_independent(self):
        shared = "a" * 64
        self.assertEqual(measurement_class([shared, shared]), LOCAL_OR_SAME_HOST)
        self.assertEqual(measurement_class([shared, None]), LOCAL_OR_SAME_HOST)
        self.assertEqual(measurement_class(["a" * 64, "b" * 64, "c" * 64]), DISTINCT_BOOT_IDS)

    def test_sha256sum_rejects_extra_output_and_wrong_name(self):
        self.assertEqual(parse_sha256sum("ab" * 32 + "  /bin/node\n", "/bin/node"), "ab" * 32)
        with self.assertRaises(ValueError):
            parse_sha256sum("ab" * 32 + "  /bin/node\nextra\n", "/bin/node")
        with self.assertRaises(ValueError):
            parse_sha256sum("AB" * 32 + "  /bin/node\n", "/bin/node")
        with self.assertRaises(ValueError):
            parse_sha256sum("ab" * 32 + "  /other\n", "/bin/node")

    def test_remote_hash_failure_does_not_echo_stderr(self):
        def runner(command, check, capture_output, text, timeout):
            return Completed(1, stdout="", stderr="secret-token-should-not-surface")

        with self.assertRaises(RuntimeError) as caught:
            remote_file_sha256("runner@host", "/opt/osiris-p2p-node", [], runner)
        self.assertNotIn("secret-token", str(caught.exception))

    def test_local_run_records_hashes_without_claiming_an_audit_or_distinct_hosts(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            binary = root / "osiris-p2p-node"
            binary.write_bytes(b"local-binary")
            boot = root / "boot_id"
            boot.write_bytes(b"same-boot\n")
            evidence = AcceptanceEvidence(
                topology="two_host_recipient",
                marker="CROSS_HOST_E2EE_RECIPIENT_DELIVERY",
                evidence_path=root / "evidence.json",
                environ={},
                runner=_git_runner,
                boot_id_path=boot,
            )
            evidence.add_local_binary("host-a", "sender", binary)
            evidence.add_local_binary("host-b", "recipient", binary)
            evidence.set_peer("host-a", "12D3KooWPeerA")
            evidence.set_peer("host-b", "12D3KooWPeerB")
            evidence.set_delivery(
                payload_sha256="c" * 64,
                recipient_peer_id="12D3KooWPeerB",
                recipient_digest_matched=True,
            )
            record = evidence.finish(True)
            self.assertEqual(record["measurement_class"], LOCAL_OR_SAME_HOST)
            self.assertTrue(record["test_passed"])
            self.assertFalse(record["independent_cryptographic_audit"])
            self.assertFalse(record["plaintext_retained"])
            self.assertEqual(record["commit_sha"], "1234567890abcdef1234567890abcdef12345678")
            self.assertTrue(record["clean_worktree"])
            encoded = json.dumps(record)
            self.assertNotIn("private", encoded.lower())
            self.assertNotIn("payload\":", encoded)
            stored = json.loads((root / "evidence.json").read_text(encoding="utf-8"))
            self.assertEqual(stored["hosts"][0]["binary_sha256"], hashlib.sha256(b"local-binary").hexdigest())

    def test_required_distinct_hosts_fail_closed_when_boot_ids_match(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            evidence = AcceptanceEvidence(
                topology="three_host_recipient_only",
                marker="THREE_HOST_E2EE_RECIPIENT_ONLY",
                require_distinct_hosts=True,
                evidence_path=root / "evidence.json",
                environ={"GITHUB_ACTIONS": "true", "GITHUB_SHA": "a" * 40},
                runner=_git_runner,
            )
            for host_id in ("host-a", "host-b", "host-c"):
                evidence._add(host_id, host_id, "d" * 64, "e" * 64)
            with self.assertRaisesRegex(RuntimeError, "host_boot_ids_not_distinct"):
                evidence.finish(True)
            stored = json.loads((root / "evidence.json").read_text(encoding="utf-8"))
            self.assertEqual(stored["measurement_class"], LOCAL_OR_SAME_HOST)
            self.assertFalse(stored["test_passed"])
            self.assertTrue(stored["e2ee_assertions_passed"])
            self.assertFalse(stored["independent_cryptographic_audit"])
            self.assertEqual(stored["invocation"], "github_actions")

    def test_distinct_boot_ids_keep_binary_hashes_and_still_deny_an_audit_claim(self):
        evidence = AcceptanceEvidence(
            topology="two_host_recipient",
            marker="CROSS_HOST_E2EE_RECIPIENT_DELIVERY",
            require_distinct_hosts=True,
            environ={
                "GITHUB_ACTIONS": "true",
                "GITHUB_SHA": "b" * 40,
                "GITHUB_SERVER_URL": "https://github.com",
                "GITHUB_REPOSITORY": "Osiris6900/osiris-eye",
                "GITHUB_RUN_ID": "42424830531",
                "GITHUB_RUN_ATTEMPT": "1",
            },
            runner=_git_runner,
        )
        evidence._add("host-a", "sender", "a" * 64, "1" * 64)
        evidence._add("host-b", "recipient", "b" * 64, "2" * 64)
        record = evidence.finish(True)
        self.assertEqual(record["measurement_class"], DISTINCT_BOOT_IDS)
        self.assertTrue(record["test_passed"])
        self.assertFalse(record["independent_cryptographic_audit"])
        self.assertEqual(
            record["workflow_url"],
            "https://github.com/Osiris6900/osiris-eye/actions/runs/42424830531",
        )
        self.assertEqual([host["binary_sha256"] for host in record["hosts"]], ["a" * 64, "b" * 64])

    def test_private_key_material_in_an_error_is_not_retained(self):
        evidence = AcceptanceEvidence(
            topology="two_host_recipient",
            marker="CROSS_HOST_E2EE_RECIPIENT_DELIVERY",
            environ={},
            runner=_git_runner,
        )
        evidence._add("host-a", "sender", "a" * 64, None)
        record = evidence.finish(False, error="-----BEGIN OPENSSH PRIVATE KEY----- secret")
        self.assertEqual(record["error"], "error_redacted")
        self.assertFalse(record["test_passed"])


if __name__ == "__main__":
    unittest.main()
