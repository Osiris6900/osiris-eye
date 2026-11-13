#!/usr/bin/env python3
"""Retained artifact regressions: rehashed omissions must not pass verification."""
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

import verify_dependency_sbom as verifier
from generate_dependency_sbom import build_sbom, load_cache, pypi_license_map


class RetainedSbomTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.commit = verifier.git(verifier.ROOT, "rev-parse", "HEAD").decode().strip()
        cls.tree = verifier.git(verifier.ROOT, "rev-parse", "HEAD^{tree}").decode().strip()
        cls.run_id = "12345"
        cls.attempt = "2"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = {}
            for name in (*verifier.INPUTS, verifier.GENERATOR, verifier.CRATE_METADATA, verifier.PYTHON_METADATA):
                source[name] = verifier.git(verifier.ROOT, "show", f"{cls.commit}:{name}")
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(source[name])
            lock = root / "services/quant-api/requirements-lock.txt"
            sbom = build_sbom(root / "services/p2p-node/Cargo.lock", root / "apps/web/package-lock.json",
                root / "services/quant-api/pyproject.toml", load_cache(root / verifier.CRATE_METADATA), True,
                lock, pypi_license_map(root / verifier.PYTHON_METADATA, lock),
                additional_cargo_locks={name: root / name for name in verifier.INPUTS if name.startswith("crates/")})
        cls.sbom = sbom
        serialized = (json.dumps(sbom, indent=2, sort_keys=True) + "\n").encode()
        metadata = (json.dumps(json.loads(source[verifier.CRATE_METADATA]), indent=2, sort_keys=True) + "\n").encode()
        cls.provenance = {
            "schema": "osiris.dependency-sbom-provenance.v1", "checkout_commit": cls.commit,
            "candidate_commit": cls.commit, "checkout_tree": cls.tree, "checkout_dirty": False,
            "generator_sha256": verifier.digest(source[verifier.GENERATOR]),
            "crate_license_metadata_sha256": verifier.digest(metadata),
            "python_license_evidence_sha256": verifier.digest(source[verifier.PYTHON_METADATA]),
            "input_sha256": {name: verifier.digest(source[name]) for name in verifier.INPUTS},
            "sbom_sha256": verifier.digest(serialized), "component_count": len(sbom["components"]),
            "ci_run_id": cls.run_id, "ci_run_attempt": cls.attempt, "signature_verified": False,
            "licences_independently_reviewed": False, "release_gate_accepted": False,
            "live_trading_authorized": False,
        }

    def files(self, sbom=None, **changes):
        serialized = (json.dumps(sbom or self.sbom, indent=2, sort_keys=True) + "\n").encode()
        provenance = {**copy.deepcopy(self.provenance), "sbom_sha256": verifier.digest(serialized), **changes}
        return {
            "dependency-sbom.json": serialized,
            "dependency-sbom.json.sha256": (verifier.digest(serialized) + "  dependency-sbom.json\n").encode(),
            "dependency-sbom-provenance.json": json.dumps(provenance).encode(),
        }

    def verify(self, files=None, **changes):
        arguments = {"repository": verifier.ROOT, "commit": self.commit, "candidate": self.commit,
                     "run_id": self.run_id, "run_attempt": self.attempt, **changes}
        return verifier.verify(self.files() if files is None else files, **arguments)

    def test_complete_committed_snapshot_reproduces_offline(self):
        result = self.verify()
        self.assertEqual(result["component_count"], len(self.sbom["components"]))
        self.assertEqual(set(result["input_sha256"]), set(verifier.INPUTS))
        self.assertEqual(result["checkout_commit"], self.commit)
        self.assertTrue(result["candidate_tree_matches_checkout"])

    def test_rehashed_omissions_from_every_ecosystem_are_rejected(self):
        for name in ("osiris-native-feed", "osiris-risk-kernel", "memmap2", "react", "numpy"):
            with self.subTest(component=name):
                reduced = copy.deepcopy(self.sbom)
                reduced["components"] = [item for item in reduced["components"] if item["name"] != name]
                self.assertLess(len(reduced["components"]), len(self.sbom["components"]))
                # Both the self-hash and declared count agree with the forgery.
                with self.assertRaisesRegex(ValueError, "does not reproduce"):
                    self.verify(self.files(reduced, component_count=len(reduced["components"])))

    def test_changed_licenses_and_package_checksums_fail_despite_rehashing(self):
        for field, value in (("licenses", [{"expression": "MIT"}]), ("hashes", [])):
            forged = copy.deepcopy(self.sbom)
            target = next(item for item in forged["components"] if item["name"] == "memmap2")
            self.assertNotEqual(target[field], value)
            target[field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "does not reproduce"):
                self.verify(self.files(forged))

    def test_wrong_source_metadata_run_and_false_authority_claims_fail(self):
        changes = {
            "checkout_commit": "0" * 40, "candidate_commit": "0" * 40,
            "checkout_tree": "0" * 40, "checkout_dirty": True, "generator_sha256": "0" * 64,
            "crate_license_metadata_sha256": "0" * 64, "python_license_evidence_sha256": "0" * 64,
            "ci_run_id": "12346", "ci_run_attempt": "1", "input_sha256": {},
            "signature_verified": True, "licences_independently_reviewed": True,
            "release_gate_accepted": True, "live_trading_authorized": True,
        }
        for key, value in changes.items():
            with self.subTest(field=key), self.assertRaisesRegex(ValueError, "provenance." + key):
                self.verify(self.files(**{key: value}))
        for key in ("checkout_dirty", "release_gate_accepted", "live_trading_authorized"):
            with self.subTest(field=key), self.assertRaises(ValueError):
                self.verify(self.files(**{key: 0}))

    def test_missing_extra_and_tampered_file_bytes_fail(self):
        files = self.files()
        for name in verifier.FILES:
            incomplete = {key: value for key, value in files.items() if key != name}
            with self.subTest(missing=name), self.assertRaises(ValueError):
                self.verify(incomplete)
        with self.assertRaises(ValueError):
            self.verify({**files, "extra": b""})
        with self.assertRaisesRegex(ValueError, "sidecar"):
            self.verify({**files, "dependency-sbom.json.sha256": b"0" * 64})
        with self.assertRaisesRegex(ValueError, "sbom_sha256"):
            self.verify({**files, "dependency-sbom.json": files["dependency-sbom.json"] + b"\n"})
        with self.assertRaisesRegex(ValueError, "component_count"):
            self.verify(self.files(component_count=True))

    def test_duplicate_json_keys_non_objects_and_nan_are_rejected(self):
        for data in (b'{"passed":true,"passed":false}', b'[]', b'{"value":NaN}', b'{"value":Infinity}'):
            with self.subTest(data=data), self.assertRaises((ValueError, TypeError)):
                verifier.document(data)

    def test_moving_refs_and_invalid_ci_identity_are_rejected(self):
        for changes in ({"commit": "main"}, {"candidate": "HEAD"}, {"run_id": "0"},
                        {"run_attempt": "0"}, {"run_id": "1/../2"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.verify(**changes)

    def archive(self, files, extra=None):
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            for name, content in files.items():
                bundle.writestr(name, content)
            if extra:
                bundle.writestr(*extra)
        return data.getvalue()

    def test_downloaded_zip_and_directory_verify_identical_bytes(self):
        files = self.files()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = self.archive(files)
            path = root / "artifact.zip"
            path.write_bytes(data)
            loaded, archive_digest = verifier.read_artifact(path, archive=True, expected_digest=verifier.digest(data))
            self.assertEqual(loaded, files)
            self.assertEqual(archive_digest, verifier.digest(data))
            artifacts = root / "files"
            artifacts.mkdir()
            for name, content in files.items():
                (artifacts / name).write_bytes(content)
            self.assertEqual(verifier.read_artifact(artifacts, archive=False), (files, None))
            for wrong in (None, "0" * 64):
                with self.assertRaises(ValueError):
                    verifier.read_artifact(path, archive=True, expected_digest=wrong)

    def test_path_traversal_duplicate_members_symlinks_and_oversize_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "artifact.zip"
            for extra in (("../escape", b"bad"), ("dependency-sbom.json", b"duplicate")):
                with self.subTest(extra=extra[0]):
                    data = self.archive(self.files(), extra)
                    path.write_bytes(data)
                    with self.assertRaises(ValueError):
                        verifier.read_artifact(path, archive=True, expected_digest=verifier.digest(data))
            for mode, content in ((stat.S_IFLNK | 0o777, b"/etc/passwd"), (stat.S_IFREG | 0o644, b"x" * (verifier.LIMIT + 1))):
                member = zipfile.ZipInfo("dependency-sbom.json")
                member.create_system = 3
                member.external_attr = mode << 16
                data = io.BytesIO()
                with zipfile.ZipFile(data, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
                    for name, value in self.files().items():
                        bundle.writestr(member if name == member.filename else name, content if name == member.filename else value)
                path.write_bytes(data.getvalue())
                with self.assertRaises(ValueError):
                    verifier.read_artifact(path, archive=True, expected_digest=verifier.digest(data.getvalue()))
            self.assertFalse((Path(directory).parent / "escape").exists())

    def test_cli_never_approves_release_and_preserves_an_existing_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifacts = root / "files"
            artifacts.mkdir()
            for name, value in self.files().items():
                (artifacts / name).write_bytes(value)
            report = root / "result.json"
            command = [sys.executable, str(verifier.ROOT / "scripts/verify_dependency_sbom.py"),
                "--artifact-dir", str(artifacts), "--commit", self.commit,
                "--run-id", self.run_id, "--run-attempt", self.attempt, "--report", str(report)]
            completed = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            result = json.loads(report.read_text())
            self.assertTrue(result["passed"])
            for key in ("ci_identity_authenticated", "release_gate_accepted", "live_trading_authorized", "independent_review_verified"):
                self.assertIs(result[key], False)
            original = report.read_bytes()
            repeated = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertNotEqual(repeated.returncode, 0)
            self.assertEqual(report.read_bytes(), original)
            report.unlink()
            (artifacts / "dependency-sbom.json").write_bytes(b"{}")
            failed = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertNotEqual(failed.returncode, 0)
            self.assertIs(json.loads(report.read_text())["passed"], False)


if __name__ == "__main__":
    unittest.main()
