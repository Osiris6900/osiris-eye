#!/usr/bin/env python3
"""Reject rehashed incomplete sources and inconsistent retained paper traces."""
import copy
import io
import json
import stat
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

import verify_receipt_paper_artifact as verifier


class ReceiptArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.repository = Path(cls.temporary.name)
        for command in (["init", "-q"], ["config", "user.name", "Fixture"],
                        ["config", "user.email", "fixture@example.invalid"]):
            verifier.git(cls.repository, *command)
        (cls.repository / "source.py").write_text("raise RuntimeError('must never execute source')\n")
        (cls.repository / "evidence.txt").write_text("fixture, not release evidence\n")
        (cls.repository / "ignored-link").symlink_to("source.py")
        verifier.git(cls.repository, "add", ".")
        verifier.git(cls.repository, "commit", "-qm", "fixture")
        cls.commit = verifier.git(cls.repository, "rev-parse", "HEAD").decode().strip()
        cls.source = verifier.committed_source(cls.repository, cls.commit)

    def fixture(self):
        audit = []

        def append(event, identifier, payload):
            row = {"seq": len(audit) + 1, "event": event, "client_order_id": identifier,
                   "prev_hash": audit[-1]["row_hash"] if audit else "GENESIS",
                   "payload": json.dumps({"live_execution": False, **payload}, sort_keys=True)}
            material = {"prev": row["prev_hash"], **{key: row[key] for key in
                        ("seq", "event", "client_order_id", "payload")}}
            row["row_hash"] = verifier.digest(json.dumps(material, sort_keys=True, separators=(",", ":")).encode())
            audit.append(row)
            return row

        append("account_initialized", None, {"cash": "1000"})
        traces = []
        for index, (side, price, cash, pnl) in enumerate((("BUY", "100", "799", "0"), ("SELL", "110", "1018", "18"))):
            identifier = "order-" + str(index)
            economics = {"side": side, "price": price, "quantity": "2", "fee": "1"}
            decision = append("order_decided", identifier, {**economics, "limits_evaluated": True,
                               "origin_reference": str(index + 1) * 64})
            append("fill_recorded", identifier, {**economics, "realized_pnl": pnl, "risk_decision_id": decision["row_hash"]})
            account = {"cash": cash, "realized_pnl": pnl, "fill_count": index + 1, "audit": copy.deepcopy(audit)}
            order = {**economics, "mode": "paper_only", "live_execution": False, "broker_order_submitted": False,
                     "limits_evaluated": True, "state": "filled", "original_status": "filled",
                     "status": "idempotent_replay" if index == 0 else "filled", "filled_quantity": "2",
                     "leaves_quantity": "0", "client_order_id": identifier,
                     "origin_reference": str(index + 1) * 64, "risk_decision_id": decision["row_hash"],
                     "fill": {"client_fill_id": identifier, "price": price, "quantity": "2",
                              "realized_pnl": pnl, "seq": decision["seq"]}}
            traces.append({"schema": "osiris.receipt-paper-execution.v1", "mode": "paper_only",
                "source_mode": "synthetic", "live_execution": False, "broker_order_submitted": False,
                "receipt_is_order_approval": False, "new_execution_authorized": False,
                "independent_model_approval": False, "independent_vendor_rights_verified": False,
                "replayed": index == 0, "trace_id": identifier, "pipeline_run_id": identifier,
                "order": order, "account": account,
                "current_risk": {"approved_for_paper": True, "limits_evaluated": True,
                                 "live_execution": False, "order_notional": 200.0 if index == 0 else 220.0},
                "reconciliation": {"passed": True, "breaks": [], "mismatches": [],
                                   "live_execution": False, "broker_order_submitted": False},
                "receipt": {"event_id": str(index + 1) * 64, "envelope_sha256": "a" * 64,
                            "message_id": "message-" + str(index), "recipient_peer_id": "recipient",
                            "sender_peer_id": "sender", "rust_pid": 123, "rust_uid": 1000, "accepted_at": 10}})
        final_account = {"mode": "paper_only", "live_execution": False, "broker_order_submitted": False,
                         "initialized": True, "kill_switch_active": True, "initial_cash": "1000",
                         "cash": "1018", "realized_pnl": "18", "unrealized_pnl": "0", "fill_count": 2,
                         "order_count": 2, "positions": [], "open_orders": [], "audit": audit}
        return {"schema": "osiris.local-receipt-paper-acceptance.v1", "passed": True,
                "source_unchanged": True, "scope": verifier.SCOPE, "source": copy.deepcopy(self.source),
                "synthetic_market_and_strategy": True, "kernel_credentials_checked": True,
                "independent_hosts_verified": False, "independent_review_verified": False,
                "release_gate_accepted": False, "live_execution": False, "broker_order_submitted": False,
                "checks": sorted(verifier.CHECKS), "binary_sha256": "b" * 64,
                "started_at": "2026-11-13T00:00:00+00:00", "completed_at": "2026-11-13T00:00:10+00:00",
                "traces": traces, "final_account": final_account}

    def files(self, report=None):
        raw = (json.dumps(self.fixture() if report is None else report, sort_keys=True) + "\n").encode()
        return {"receipt-paper.json": raw, "receipt-paper.json.sha256":
                (verifier.digest(raw) + "  receipt-paper.json\n").encode()}

    def verify(self, report=None, **changes):
        return verifier.verify(self.files(report), **{"repository": self.repository, "commit": self.commit, **changes})

    def test_complete_sources_and_consistent_trace(self):
        result = self.verify()
        self.assertEqual(result["source_files_verified"], 2)
        self.assertEqual(result["traces_verified"], 2)
        self.assertNotIn("ignored-link", self.source["files_sha256"])
        # Current worktree contents must never stand in for exact committed bytes.
        path = self.repository / "source.py"
        original = path.read_bytes()
        try:
            path.write_bytes(b"changed uncommitted source")
            self.assertEqual(self.verify(), result)
        finally:
            path.write_bytes(original)

    def test_rehashed_missing_extra_and_changed_sources_fail(self):
        for mode in ("missing", "extra", "changed"):
            report = self.fixture()
            hashes = report["source"]["files_sha256"]
            if mode == "missing":
                del hashes["source.py"]
            elif mode == "extra":
                hashes["extra.py"] = "0" * 64
            else:
                hashes["source.py"] = "0" * 64
            report["source"]["worktree_sha256"] = verifier.digest(json.dumps(hashes, sort_keys=True).encode())
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, "source.files_sha256"):
                self.verify(report)

    def test_wrong_commit_tree_and_dirty_sources_fail(self):
        for name, value in (("git_head", "0" * 40), ("git_tree", "0" * 40), ("dirty", True), ("dirty", 0), ("worktree_sha256", "0" * 64)):
            report = self.fixture()
            report["source"][name] = value
            with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                self.verify(report)
        for commit in ("HEAD", "main", "0" * 40, "HEAD:source.py"):
            with self.subTest(commit=commit), self.assertRaises((ValueError, subprocess.SubprocessError)):
                self.verify(commit=commit)

    def test_false_success_missing_checks_duplicate_checks_and_claimed_authority_fail(self):
        for name, value in (("passed", False), ("passed", 1), ("checks", []),
                            ("checks", sorted(verifier.CHECKS) + [next(iter(verifier.CHECKS))]),
                            ("checks", [True]), ("independent_hosts_verified", True),
                            ("independent_review_verified", True), ("release_gate_accepted", True),
                            ("broker_order_submitted", True), ("live_execution", True),
                            ("scope", "production"), ("source_unchanged", False), ("binary_sha256", "bad")):
            report = self.fixture()
            report[name] = value
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.verify(report)

    def test_missing_and_reused_traces_fail(self):
        for traces in (None, [], [self.fixture()["traces"][0]] * 2):
            report = self.fixture()
            report["traces"] = traces
            with self.subTest(traces=type(traces)), self.assertRaises(ValueError):
                self.verify(report)

    def test_rehashed_cash_pnl_fill_and_risk_inconsistencies_fail(self):
        changes = [("final_account", "cash", "1019"), ("final_account", "fill_count", 3),
                   ("final_account", "fill_count", True), ("final_account", "realized_pnl", "19"),
                   ("current_risk", "approved_for_paper", False), ("current_risk", "limits_evaluated", False),
                   ("order", "risk_decision_id", "0" * 64), ("order", "origin_reference", "0" * 64),
                   ("order", "quantity", "3"), ("order", "status", "rejected"),
                   ("receipt", "rust_pid", 0), ("receipt", "rust_uid", True), ("receipt", "event_id", "bad"),
                   ("reconciliation", "breaks", ["cash"])]
        for section, name, value in changes:
            report = self.fixture()
            target = report[section] if section == "final_account" else report["traces"][0][section]
            target[name] = value
            with self.subTest(section=section, name=name), self.assertRaises(ValueError):
                self.verify(report)

    def test_audit_mutation_omission_reordering_and_duplicate_fail(self):
        for mode in ("payload", "missing", "reordered", "duplicate", "hash", "sequence"):
            report = self.fixture()
            rows = report["final_account"]["audit"]
            if mode == "payload":
                rows[1]["payload"] = '{"quantity":"3"}'
            elif mode == "missing":
                rows.pop(1)
            elif mode == "reordered":
                rows[1], rows[2] = rows[2], rows[1]
            elif mode == "duplicate":
                rows.append(copy.deepcopy(rows[-1]))
            elif mode == "hash":
                rows[-1]["row_hash"] = "0" * 64
            else:
                rows[0]["seq"] = True
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                self.verify(report)

    def test_timestamps_must_be_ordered_and_aware(self):
        for completed in ("2026-11-13T00:00:00", "2026-11-12T00:00:00+00:00", "invalid", None):
            report = self.fixture()
            report["completed_at"] = completed
            with self.subTest(completed=completed), self.assertRaises((ValueError, TypeError)):
                self.verify(report)

    def test_empty_duplicate_json_keys_and_nonfinite_values_fail(self):
        for raw in (b'{}', b'[]', b'{"passed":true,"passed":false}', b'{"passed":NaN}'):
            files = {"receipt-paper.json": raw, "receipt-paper.json.sha256":
                     (verifier.digest(raw) + "  receipt-paper.json\n").encode()}
            with self.subTest(raw=raw), self.assertRaises((ValueError, TypeError)):
                verifier.verify(files, self.repository, self.commit)

    def test_wrong_sidecar_and_missing_member_fail(self):
        files = self.files()
        for invalid in ({"receipt-paper.json": files["receipt-paper.json"]},
                        {**files, "receipt-paper.json.sha256": b"0" * 64}, {**files, "extra": b""}):
            with self.assertRaises(ValueError):
                verifier.verify(invalid, self.repository, self.commit)

    def test_archive_digest_and_members_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "artifact.zip"
            for extra in (None, "../escape", "receipt-paper.json"):
                data = io.BytesIO()
                with zipfile.ZipFile(data, "w") as archive:
                    for name, raw in self.files().items():
                        archive.writestr(name, raw)
                    if extra:
                        archive.writestr(extra, b"bad")
                path.write_bytes(data.getvalue())
                args = {"archive": True, "expected_digest": verifier.digest(data.getvalue()),
                        "expected_files": verifier.FILES}
                if extra:
                    with self.assertRaises(ValueError):
                        verifier.read_artifact(path, **args)
                else:
                    files, actual = verifier.read_artifact(path, **args)
                    self.assertEqual(files, self.files())
                    self.assertEqual(actual, args["expected_digest"])
                    with self.assertRaises(ValueError):
                        verifier.read_artifact(path, **{**args, "expected_digest": "0" * 64})
            # Exact-name symlinks must not become report contents.
            data = io.BytesIO()
            with zipfile.ZipFile(data, "w") as archive:
                for name, raw in self.files().items():
                    member = zipfile.ZipInfo(name)
                    member.create_system = 3
                    member.external_attr = (stat.S_IFLNK | 0o777) << 16
                    archive.writestr(member, raw)
            path.write_bytes(data.getvalue())
            with self.assertRaises(ValueError):
                verifier.read_artifact(path, archive=True, expected_digest=verifier.digest(data.getvalue()),
                                       expected_files=verifier.FILES)

    def test_cli_records_limits_and_never_overwrites_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifacts = root / "files"
            artifacts.mkdir()
            for name, raw in self.files().items():
                (artifacts / name).write_bytes(raw)
            report = root / "verified.json"
            command = [sys.executable, str(verifier.ROOT / "scripts/verify_receipt_paper_artifact.py"),
                       "--artifact-dir", str(artifacts), "--commit", self.commit,
                       "--repository", str(self.repository), "--report", str(report)]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            evidence = json.loads(report.read_bytes())
            for name in ("release_gate_accepted", "live_trading_authorized", "ci_identity_authenticated",
                         "independent_hosts_verified", "independent_review_verified", "binary_reproduced",
                         "receipt_signature_reverified", "receipt_origin_recomputed", "run_truth_independently_verified"):
                self.assertIs(evidence[name], False)
            saved = report.read_bytes()
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(report.read_bytes(), saved)


if __name__ == "__main__":
    unittest.main()
