#!/usr/bin/env python3
"""Offline tests for deterministic locked-dependency SBOM generation."""
import base64
import copy
import json
from pathlib import Path
import tempfile
import unittest

from generate_dependency_sbom import (
    CARGO_REGISTRY,
    build_sbom,
    empty_cache,
    npm_hash,
    pypi_license_map,
    purl,
)
from python_lock import LOCK_RELATIVE_PATH, parse_hashed_lock


class DependencySbomTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.cargo = self.root / "Cargo.lock"
        self.npm = self.root / "package-lock.json"
        self.python = self.root / "pyproject.toml"
        self.checksum = "ab" * 32
        self.cargo.write_text(f'''version = 4

[[package]]
name = "osiris-p2p-node"
version = "0.1.0"

[[package]]
name = "demo-crate"
version = "1.2.3"
source = "{CARGO_REGISTRY}"
checksum = "{self.checksum}"
''')
        self.npm.write_text(json.dumps({
            "name": "osiris-eye-web",
            "version": "0.1.0",
            "lockfileVersion": 3,
            "packages": {
                "": {"name": "osiris-eye-web", "version": "0.1.0"},
                "node_modules/demo": {
                    "version": "2.0.0",
                    "resolved": "https://registry.npmjs.org/demo/-/demo-2.0.0.tgz",
                    "integrity": "sha512-" + base64.b64encode(b"a" * 64).decode(),
                    "license": "MIT",
                },
                "node_modules/@scope/dev": {
                    "version": "3.0.0",
                    "resolved": "https://registry.npmjs.org/@scope/dev/-/dev-3.0.0.tgz",
                    "integrity": "sha256-" + base64.b64encode(b"b" * 32).decode(),
                    "license": "ISC",
                    "dev": True,
                },
            },
        }))
        self.python.write_text('''[project]
name = "osiris-quant-api"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["fastapi>=0.110,<1"]
''')
        self.cache = empty_cache()
        self.cache["crates"]["demo-crate@1.2.3"] = {
            "license": "MIT",
            "license_file": None,
            "checksum": self.checksum,
            "source": "https://crates.io/api/v1/crates/demo-crate/1.2.3",
        }

    def tearDown(self):
        self.directory.cleanup()

    def build(self):
        return build_sbom(self.cargo, self.npm, self.python, copy.deepcopy(self.cache), True)

    def test_locked_components_and_local_applications_are_inventoried(self):
        sbom = self.build()
        self.assertEqual(sbom["bomFormat"], "CycloneDX")
        self.assertEqual(sbom["specVersion"], "1.5")
        self.assertEqual(len(sbom["components"]), 6)
        crate = next(c for c in sbom["components"] if c["name"] == "demo-crate")
        self.assertEqual(crate["hashes"], [{"alg": "SHA-256", "content": self.checksum}])
        self.assertEqual(crate["licenses"], [{"expression": "MIT"}])
        dev = next(c for c in sbom["components"] if c["name"] == "@scope/dev")
        self.assertEqual(dev["scope"], "optional")
        self.assertEqual(dev["purl"], "pkg:npm/%40scope/dev@3.0.0")

    def test_python_is_explicitly_unresolved_not_fabricated_as_locked(self):
        sbom = self.build()
        python = next(c for c in sbom["components"] if c["name"] == "osiris-quant-api")
        properties = {p["name"]: p["value"] for p in python["properties"]}
        self.assertEqual(properties["osiris:dependency-resolution"], "unresolved")
        self.assertIn("fastapi>=0.110,<1", properties["osiris:declared-dependencies"])
        self.assertFalse(any(c.get("purl", "").startswith("pkg:pypi/") for c in sbom["components"]))

    def test_output_is_deterministic_and_checkout_path_independent(self):
        first = self.build()
        other = self.root / "different-location"
        other.mkdir()
        cargo = other / "Cargo.lock"
        npm = other / "package-lock.json"
        python = other / "pyproject.toml"
        cargo.write_text(self.cargo.read_text())
        npm.write_text(self.npm.read_text())
        python.write_text(self.python.read_text())
        second = build_sbom(cargo, npm, python, copy.deepcopy(self.cache), True)
        self.assertEqual(first, second)

    def test_missing_offline_crate_metadata_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "offline crate cache is missing"):
            build_sbom(self.cargo, self.npm, self.python, empty_cache(), True)

    def test_crate_checksum_mismatch_fails_closed(self):
        self.cache["crates"]["demo-crate@1.2.3"]["checksum"] = "cd" * 32
        with self.assertRaisesRegex(ValueError, "crate checksum mismatch"):
            self.build()

    def test_missing_npm_license_fails_closed(self):
        document = json.loads(self.npm.read_text())
        del document["packages"]["node_modules/demo"]["license"]
        self.npm.write_text(json.dumps(document))
        with self.assertRaisesRegex(ValueError, "license missing"):
            self.build()

    def test_unsupported_cargo_source_fails_closed(self):
        self.cargo.write_text(self.cargo.read_text().replace(CARGO_REGISTRY, "git+https://example.invalid/repo"))
        with self.assertRaisesRegex(ValueError, "unsupported Cargo source"):
            self.build()

    def test_npm_integrity_is_decoded_to_cyclonedx_hex(self):
        result = npm_hash("sha256-" + base64.b64encode(b"d" * 32).decode())
        self.assertEqual(result, {"alg": "SHA-256", "content": (b"d" * 32).hex()})

    def test_scoped_npm_purl_requires_a_package_name(self):
        with self.assertRaisesRegex(ValueError, "invalid scoped npm package"):
            purl("npm", "@scope", "1.0.0")

    def write_lock(self, text):
        path = self.root / "requirements-lock.txt"
        path.write_text(text)
        return path

    def test_hashed_python_lock_adds_exact_pins_and_artifact_digests(self):
        first = "ab" * 32
        second = "cd" * 32
        lock = self.write_lock(
            "Foo_Bar==1.2.3 \\\n"
            f"    --hash=sha256:{second} \\\n"
            f"    --hash=sha256:{first}\n"
        )
        licenses = {("foo-bar", "1.2.3"): "MIT"}
        sbom = build_sbom(
            self.cargo, self.npm, self.python, copy.deepcopy(self.cache), True, lock, licenses,
        )
        component = next(item for item in sbom["components"] if item["name"] == "foo-bar")
        self.assertEqual(component["purl"], "pkg:pypi/foo-bar@1.2.3")
        self.assertEqual(component["version"], "1.2.3")
        self.assertEqual(component["licenses"], [{"expression": "MIT"}])
        self.assertEqual(
            component["hashes"],
            [{"alg": "SHA-256", "content": first}, {"alg": "SHA-256", "content": second}],
        )
        properties = {item["name"]: item["value"] for item in component["properties"]}
        self.assertEqual(properties["osiris:artifact-sha256"], f"{first} {second}")
        self.assertEqual(properties["osiris:hash-source"], LOCK_RELATIVE_PATH)
        python = next(item for item in sbom["components"] if item["name"] == "osiris-quant-api")
        resolution = {item["name"]: item["value"] for item in python["properties"]}
        self.assertEqual(resolution["osiris:dependency-resolution"], "hashed-deployment-lock")
        metadata = {
            item["name"]: item["value"] for item in sbom["metadata"]["component"]["properties"]
        }
        self.assertEqual(metadata["osiris:python-deployment-resolution"], "hashed-deployment-lock")
        self.assertIn(f"osiris:input-sha256:{LOCK_RELATIVE_PATH}", metadata)

    def test_python_lock_without_license_evidence_fails_closed(self):
        lock = self.write_lock("demo==1.0.0 --hash=sha256:" + ("aa" * 32) + "\n")
        with self.assertRaisesRegex(ValueError, "license evidence"):
            build_sbom(self.cargo, self.npm, self.python, copy.deepcopy(self.cache), True, lock, {})

    def test_stale_license_evidence_fails_closed(self):
        lock = self.write_lock("demo==1.0.0 --hash=sha256:" + ("aa" * 32) + "\n")
        evidence = self.root / "evidence.json"
        evidence.write_text(json.dumps({
            "schema": "osiris.python-license-provenance.v1",
            "lock": {"sha256": "0" * 64},
            "packages": [],
        }))
        with self.assertRaisesRegex(ValueError, "does not match"):
            pypi_license_map(evidence, lock)

    def test_repository_deployment_lock_is_represented_exactly(self):
        root = Path(__file__).resolve().parents[1]
        lock = root / LOCK_RELATIVE_PATH
        evidence = root / "docs/PYTHON_LICENSE_PROVENANCE.json"
        licenses = pypi_license_map(evidence, lock)
        entries = parse_hashed_lock(lock)
        sbom = build_sbom(
            self.cargo, self.npm, self.python, copy.deepcopy(self.cache), True, lock, licenses,
        )
        pinned = [item for item in sbom["components"] if item["purl"].startswith("pkg:pypi/")]
        self.assertEqual(len(pinned), len(entries))
        by_purl = {item["purl"]: item for item in pinned}
        for entry in entries:
            component = by_purl[purl("pypi", entry.normalized_name, entry.version)]
            properties = {item["name"]: item["value"] for item in component["properties"]}
            self.assertEqual(properties["osiris:artifact-sha256"].split(), list(entry.hashes))
            self.assertEqual(
                component["licenses"],
                [{"expression": licenses[(entry.normalized_name, entry.version)]}],
            )


if __name__ == "__main__":
    unittest.main()
