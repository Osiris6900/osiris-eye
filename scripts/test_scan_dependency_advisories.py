#!/usr/bin/env python3
from __future__ import annotations

import base64
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

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
        )
        packages = scan.locked_packages(
            root / "services/p2p-node/Cargo.lock",
            root / "apps/web/package-lock.json",
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
