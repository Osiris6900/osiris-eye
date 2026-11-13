#!/usr/bin/env python3
from __future__ import annotations

import base64
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scan_dependency_advisories as scan


class FakeClient:
    def __init__(self, matches=(), records=None):
        self.matches = list(matches)
        self.records = records or {}
        self.batch_sizes = []

    def querybatch(self, packages, batch_size=100):
        self.batch_sizes.append(batch_size)
        wanted = {(p["ecosystem"], p["name"], p["version"]) for p in packages}
        return [
            (package, advisory_id)
            for package, advisory_id in self.matches
            if (package["ecosystem"], package["name"], package["version"]) in wanted
        ]

    def vulnerability(self, advisory_id):
        return self.records[advisory_id]


def advisory(advisory_id, aliases=(), summary="test advisory"):
    return {
        "id": advisory_id,
        "aliases": list(aliases),
        "summary": summary,
        "modified": "2026-11-08T00:00:00Z",
        "affected": [],
    }


class AdvisoryScanTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.cargo = self.root / "Cargo.lock"
        self.npm = self.root / "package-lock.json"
        self.pyproject = self.root / "pyproject.toml"
        self.cargo.write_text("""version = 4

[[package]]
name = "local-app"
version = "0.1.0"

[[package]]
name = "demo"
version = "1.2.3"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
""")
        integrity = base64.b64encode(b"x" * 64).decode()
        self.npm.write_text(json.dumps({
            "lockfileVersion": 3,
            "packages": {
                "": {"name": "web", "version": "1.0.0"},
                "node_modules/example": {
                    "version": "4.5.6",
                    "license": "MIT",
                    "integrity": f"sha512-{integrity}",
                },
            },
        }))
        self.pyproject.write_text("""[project]
name = "api"
version = "0.1.0"
dependencies = ["fastapi>=0.110,<1"]
""")
        self.package = {"ecosystem": "crates.io", "name": "demo", "version": "1.2.3"}

    def tearDown(self):
        self.temporary.cleanup()

    def build(self, client, maintenance=None):
        return scan.build_report(
            self.cargo,
            self.npm,
            self.pyproject,
            maintenance or {},
            "212cad687b69ed421c0778699e451955576739c9",
            client,
            queried_at="2026-11-08T00:00:00Z",
            batch_size=1,
        )

    def test_alias_records_form_one_group_without_losing_ids(self):
        records = {
            "GHSA-one": advisory("GHSA-one", ["RUSTSEC-one", "CVE-one"]),
            "RUSTSEC-one": advisory("RUSTSEC-one", ["GHSA-one", "CVE-one"]),
        }
        client = FakeClient(
            [(self.package, "GHSA-one"), (self.package, "RUSTSEC-one")], records
        )
        report = self.build(client)
        self.assertEqual(report["summary"]["raw_advisory_matches"], 2)
        self.assertEqual(report["summary"]["advisory_groups"], 1)
        self.assertEqual(report["summary"]["vulnerability_groups"], 1)
        self.assertEqual(report["findings"][0]["ids"], ["CVE-one", "GHSA-one", "RUSTSEC-one"])
        self.assertEqual(report["findings"][0]["matched_ids"], ["GHSA-one", "RUSTSEC-one"])
        self.assertEqual(len(report["findings"][0]["records"]), 2)

    def test_unrelated_and_maintenance_advisories_stay_separate(self):
        records = {
            "GHSA-one": advisory("GHSA-one", ["RUSTSEC-one"]),
            "RUSTSEC-one": advisory("RUSTSEC-one", ["GHSA-one"]),
            "RUSTSEC-maint": advisory("RUSTSEC-maint", summary="demo - no longer maintained"),
        }
        client = FakeClient(
            [(self.package, "GHSA-one"), (self.package, "RUSTSEC-maint")], records
        )
        report = self.build(client, {"RUSTSEC-maint": {"reason": "maintenance only"}})
        self.assertEqual(report["summary"]["advisory_groups"], 2)
        self.assertEqual(report["summary"]["vulnerability_groups"], 1)
        self.assertEqual(report["summary"]["maintenance_only_groups"], 1)
        classifications = {finding["classification"] for finding in report["findings"]}
        self.assertEqual(classifications, {"vulnerability", "maintenance-only"})
        maintenance = next(f for f in report["findings"] if f["classification"] == "maintenance-only")
        self.assertEqual(maintenance["maintenance_only_evidence"][0]["reason"], "maintenance only")

    def test_coverage_hashes_batches_and_python_boundary(self):
        report = self.build(FakeClient())
        self.assertEqual(report["summary"]["queried_packages"], 2)
        self.assertEqual(report["coverage"]["crates.io"]["packages"], 1)
        self.assertEqual(report["coverage"]["npm"]["packages"], 1)
        self.assertEqual(report["coverage"]["PyPI"]["status"], "unresolved")
        self.assertEqual(report["coverage"]["PyPI"]["packages"], 0)
        self.assertEqual(report["query"]["batches"], 2)
        self.assertEqual(report["query"]["queried_at"], "2026-11-08T00:00:00Z")
        scan.validate_report(report, self.cargo, self.npm, self.pyproject)

    def test_stale_report_fails_closed(self):
        report = self.build(FakeClient())
        self.cargo.write_text(self.cargo.read_text() + "# changed after scan\n")
        with self.assertRaisesRegex(ValueError, "stale"):
            scan.validate_report(report, self.cargo, self.npm, self.pyproject)

    def test_source_commit_must_be_full_sha(self):
        with self.assertRaisesRegex(ValueError, "source commit"):
            scan.build_report(
                self.cargo, self.npm, self.pyproject, {}, "main", FakeClient()
            )

    def test_misaligned_querybatch_response_fails_closed(self):
        def opener(request, timeout):
            return io.BytesIO(b'{"results":[]}')

        client = scan.OsvClient(opener=opener, retries=1)
        with self.assertRaisesRegex(ValueError, "misaligned"):
            client.querybatch([self.package])

    def test_mismatched_vulnerability_record_fails_closed(self):
        def opener(request, timeout):
            return io.BytesIO(json.dumps({"id": "GHSA-other"}).encode())

        client = scan.OsvClient(opener=opener, retries=1)
        with self.assertRaisesRegex(ValueError, "mismatched"):
            client.vulnerability("GHSA-one")

    def test_invalid_maintenance_configuration_fails_closed(self):
        path = self.root / "maintenance.json"
        path.write_text(json.dumps({"schema": scan.MAINTENANCE_SCHEMA, "advisories": {"ID": {}}}))
        with self.assertRaisesRegex(ValueError, "reason"):
            scan.load_maintenance_only(path)

    def test_validation_rejects_inconsistent_summary(self):
        report = self.build(FakeClient())
        report["summary"]["queried_packages"] += 1
        with self.assertRaisesRegex(ValueError, "summary is inconsistent"):
            scan.validate_report(report, self.cargo, self.npm, self.pyproject)

    def test_validation_rejects_invalid_timestamp(self):
        report = self.build(FakeClient())
        report["query"]["queried_at"] = "not-a-timestamp"
        with self.assertRaisesRegex(ValueError, "timestamp is invalid"):
            scan.validate_report(report, self.cargo, self.npm, self.pyproject)

    def test_validation_rejects_invalid_finding_ids(self):
        records = {"GHSA-one": advisory("GHSA-one")}
        report = self.build(FakeClient([(self.package, "GHSA-one")], records))
        report["findings"][0]["matched_ids"] = ["GHSA-other"]
        with self.assertRaisesRegex(ValueError, "matched IDs are inconsistent"):
            scan.validate_report(report, self.cargo, self.npm, self.pyproject)


PYTHON_LOCK_TEXT = """# schema: osiris.python-deployment-lock.v1
# generated_by: scripts/resolve_python_lock.py
# generator: pip-compile (pip-tools) --resolver=backtracking --generate-hashes
# python_version: 3.11
# pyproject_sha256: unused-in-scanner-tests
# generated_at: 2026-11-08T00:00:00Z

fastapi==0.115.0 \\
    --hash=sha256:{digest}
pydantic==2.9.2 \\
    --hash=sha256:{digest}
""".format(digest="d" * 64)


class PythonLockScanTests(AdvisoryScanTests):
    def setUp(self):
        super().setUp()
        self.python_lock = self.root / "requirements-lock.txt"
        self.python_lock.write_text(PYTHON_LOCK_TEXT)
        self.pypi_package = {"ecosystem": "PyPI", "name": "fastapi", "version": "0.115.0"}

    def build_python(self, client, maintenance=None):
        return scan.build_report(
            self.cargo,
            self.npm,
            self.pyproject,
            maintenance or {},
            "212cad687b69ed421c0778699e451955576739c9",
            client,
            queried_at="2026-11-08T00:00:00Z",
            batch_size=2,
            python_lock=self.python_lock,
        )

    def test_pypi_pins_are_scanned_and_validated(self):
        report = self.build_python(FakeClient())
        self.assertEqual(report["coverage"]["PyPI"]["status"], "scanned")
        self.assertEqual(report["coverage"]["PyPI"]["packages"], 2)
        self.assertEqual(report["coverage"]["PyPI"]["lock"], scan.LOCK_RELATIVE_PATH)
        self.assertEqual(report["summary"]["queried_packages"], 4)
        self.assertEqual(report["query"]["batches"], 2)
        self.assertIn(
            scan.LOCK_RELATIVE_PATH, report["source"]["inputs"]
        )
        self.assertNotIn("remain unresolved", report["limitations"][2])
        scan.validate_report(report, self.cargo, self.npm, self.pyproject, self.python_lock)

    def test_pypi_findings_flow_through_grouping(self):
        records = {"GHSA-py": advisory("GHSA-py", ["CVE-py"])}
        client = FakeClient([(self.pypi_package, "GHSA-py")], records)
        report = self.build_python(client)
        self.assertEqual(report["summary"]["vulnerability_groups"], 1)
        self.assertEqual(report["findings"][0]["ecosystem"], "PyPI")
        scan.validate_report(report, self.cargo, self.npm, self.pyproject, self.python_lock)

    def test_stale_python_lock_fails_closed(self):
        report = self.build_python(FakeClient())
        self.python_lock.write_text(self.python_lock.read_text() + "# changed\n")
        with self.assertRaisesRegex(ValueError, "stale"):
            scan.validate_report(report, self.cargo, self.npm, self.pyproject, self.python_lock)

    def test_scanned_report_rejected_without_python_lock(self):
        report = self.build_python(FakeClient())
        with self.assertRaisesRegex(ValueError, "input inventory"):
            scan.validate_report(report, self.cargo, self.npm, self.pyproject)

    def test_unresolved_report_rejected_with_python_lock(self):
        report = self.build(FakeClient())
        with self.assertRaisesRegex(ValueError, "stale for services/quant-api/requirements-lock.txt"):
            scan.validate_report(report, self.cargo, self.npm, self.pyproject, self.python_lock)

    def test_unparseable_python_lock_fails_closed(self):
        self.python_lock.write_text("fastapi>=0.110\n")
        with self.assertRaisesRegex(ValueError, "exactly pinned"):
            self.build_python(FakeClient())

    def test_checked_in_report_matches_current_lock_inputs(self):
        root = Path(__file__).resolve().parents[1]
        report_path = root / "docs/P2P_DEPENDENCY_ADVISORIES.json"
        self.assertTrue(report_path.is_file(), f"missing {report_path}")
        report = json.loads(report_path.read_text())
        scan.validate_report(
            report,
            root / "services/p2p-node/Cargo.lock",
            root / "apps/web/package-lock.json",
            root / "services/quant-api/pyproject.toml",
            root / scan.LOCK_RELATIVE_PATH,
            scan.cargo_lock_inputs(root),
        )
        packages = scan.locked_packages(
            root / "services/p2p-node/Cargo.lock",
            root / "apps/web/package-lock.json",
            root / scan.LOCK_RELATIVE_PATH,
            scan.cargo_lock_inputs(root),
        )
        findings = report["findings"]
        self.assertEqual(report["summary"], {
            "queried_packages": len(packages),
            "raw_advisory_matches": sum(len(f["matched_ids"]) for f in findings),
            "affected_packages": len({
                (f["ecosystem"], f["name"], f["version"]) for f in findings
            }),
            "advisory_groups": len(findings),
            "vulnerability_groups": sum(
                f["classification"] == "vulnerability" for f in findings
            ),
            "maintenance_only_groups": sum(
                f["classification"] == "maintenance-only" for f in findings
            ),
        })

    def test_offline_cli_never_constructs_a_network_client_or_changes_the_report(self):
        root = Path(__file__).resolve().parents[1]
        report_path = root / "docs/P2P_DEPENDENCY_ADVISORIES.json"
        original = report_path.read_bytes()
        with patch.object(scan, "OsvClient", side_effect=AssertionError("network forbidden")), \
             patch.object(sys, "argv", ["scan", "--check", str(report_path), "--fail-on-vulnerability"]):
            scan.main()
        self.assertEqual(report_path.read_bytes(), original)


class MultiRootAndPaginationTests(AdvisoryScanTests):
    def test_extra_root_is_scanned_shared_crates_deduplicate_and_hashes_are_bound(self):
        extra = self.root / "native.lock"
        extra.write_text(self.cargo.read_text() + '''
[[package]]
name = "native-only"
version = "7.8.9"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
''')
        locks = {"crates/native-feed/Cargo.lock": extra}
        package = {"ecosystem": "crates.io", "name": "native-only", "version": "7.8.9"}
        client = FakeClient([(package, "NATIVE-ADVISORY")],
                            {"NATIVE-ADVISORY": advisory("NATIVE-ADVISORY")})
        report = scan.build_report(self.cargo, self.npm, self.pyproject, {}, "a" * 40,
                                   client, additional_cargo_locks=locks)
        self.assertEqual(report["coverage"]["crates.io"]["packages"], 2)
        self.assertEqual(report["findings"][0]["name"], "native-only")
        scan.validate_report(report, self.cargo, self.npm, self.pyproject,
                             additional_cargo_locks=locks)
        extra.write_text(extra.read_text() + "# changed\n")
        with self.assertRaisesRegex(ValueError, "stale for crates/native-feed"):
            scan.validate_report(report, self.cargo, self.npm, self.pyproject,
                                 additional_cargo_locks=locks)

    def test_missing_extra_root_or_conflicting_checksum_fails_before_network(self):
        extra = self.root / "native.lock"
        with self.assertRaises(OSError):
            scan.locked_packages(self.cargo, self.npm, additional_cargo_locks={"native": extra})
        extra.write_text(self.cargo.read_text().replace("a" * 64, "b" * 64))
        with self.assertRaisesRegex(ValueError, "conflicting Cargo checksum"):
            scan.locked_packages(self.cargo, self.npm, additional_cargo_locks={"native": extra})

    def test_input_mutation_during_scan_is_refused(self):
        client = FakeClient()
        def mutate(packages, batch_size):
            self.cargo.write_text(self.cargo.read_text() + "# changed mid-scan\n")
            return []
        client.querybatch = mutate
        with self.assertRaisesRegex(ValueError, "inputs changed"):
            self.build(client)

    def test_unexpected_or_omitted_lock_inventory_is_rejected(self):
        report = self.build(FakeClient())
        report["source"]["inputs"]["extra.lock"] = {"sha256": "c" * 64}
        with self.assertRaisesRegex(ValueError, "input inventory"):
            scan.validate_report(report, self.cargo, self.npm, self.pyproject)
        with self.assertRaisesRegex(ValueError, "invalid additional"):
            scan.build_report(self.cargo, self.npm, self.pyproject, {}, "a" * 40,
                              FakeClient(), additional_cargo_locks={"../outside": self.cargo})

    def test_osv_continuations_keep_later_vulnerabilities_and_bind_each_package(self):
        calls = []
        other = {"ecosystem": "npm", "name": "other", "version": "2.0.0"}
        responses = [
            {"results": [{"vulns": [{"id": "FIRST"}], "next_page_token": "next"}, {}]},
            {"vulns": [{"id": "FIRST"}, {"id": "SECOND"}], "next_page_token": "last"},
            {"vulns": [{"id": "THIRD"}]},
        ]
        def opener(request, timeout):
            calls.append((request.full_url, json.loads(request.data)))
            return io.BytesIO(json.dumps(responses.pop(0)).encode())
        found = scan.OsvClient(opener=opener, retries=1).querybatch([self.package, other])
        self.assertEqual(found, [(self.package, name) for name in ("FIRST", "SECOND", "THIRD")])
        self.assertEqual(calls[1], (scan.API_BASE + "/query", {
            "package": {"ecosystem": "crates.io", "name": "demo"},
            "version": "1.2.3", "page_token": "next",
        }))
        self.assertEqual(calls[2][1]["page_token"], "last")

    def test_invalid_repeated_or_failed_continuations_never_become_a_clean_scan(self):
        for bad in (None, [], 1):
            def opener(request, timeout):
                return io.BytesIO(json.dumps({"results": [{"next_page_token": bad}]}).encode())
            with self.subTest(token=bad), self.assertRaisesRegex(ValueError, "page token"):
                scan.OsvClient(opener=opener, retries=1).querybatch([self.package])
        responses = [{"results": [{"next_page_token": "again"}]}, {"next_page_token": "again"}]
        with patch.object(scan.OsvClient, "_json", side_effect=responses):
            with self.assertRaisesRegex(ValueError, "pagination repeated"):
                scan.OsvClient().querybatch([self.package])
        with patch.object(scan.OsvClient, "_json", side_effect=[
                {"results": [{"next_page_token": "next"}]}, ValueError("offline")]):
            with self.assertRaisesRegex(ValueError, "offline"):
                scan.OsvClient().querybatch([self.package])


if __name__ == "__main__":
    unittest.main(verbosity=2)
