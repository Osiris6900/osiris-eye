#!/usr/bin/env python3
from __future__ import annotations

import hashlib
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_python_lock as check
import python_lock
from python_lock import LOCK_HEADER_SCHEMA, parse_hashed_lock, parse_lock_header

HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64

PYPROJECT = """[project]
name = "api"
version = "0.1.0"
dependencies = ["fastapi>=0.110,<1", "pydantic>=2.6,<3"]

[project.optional-dependencies]
streams = ["websockets>=12,<18"]
ibkr = ["ib_async>=2.0,<3"]
"""

HEADER = """# schema: {schema}
# generated_by: scripts/resolve_python_lock.py
# generator: pip-compile (pip-tools) --resolver=backtracking --generate-hashes --extra streams
# python_version: 3.11
# pyproject_sha256: {digest}
# generated_at: 2026-11-08T00:00:00Z
"""


class LockFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.pyproject = self.root / "pyproject.toml"
        self.pyproject.write_text(PYPROJECT)
        self.lock = self.root / "requirements-lock.txt"
        self.digest = hashlib.sha256(PYPROJECT.encode()).hexdigest()

    def tearDown(self):
        self.temporary.cleanup()

    def write_lock(self, body: str, digest: str | None = None, schema: str = LOCK_HEADER_SCHEMA):
        self.lock.write_text(HEADER.format(schema=schema, digest=digest or self.digest) + "\n" + body)


VALID_BODY = f"""fastapi==0.115.0 \\
    --hash=sha256:{HASH_A} \\
    --hash=sha256:{HASH_B}
pydantic==2.9.2 \\
    --hash=sha256:{HASH_C}
starlette==0.38.6 \\
    --hash=sha256:{HASH_A}
websockets==13.1 \\
    --hash=sha256:{HASH_B}
"""


class ParserTests(LockFixture):
    def test_valid_lock_parses_sorted_with_hashes(self):
        self.write_lock(VALID_BODY)
        entries = parse_hashed_lock(self.lock)
        self.assertEqual([e.normalized_name for e in entries], ["fastapi", "pydantic", "starlette", "websockets"])
        fastapi = entries[0]
        self.assertEqual(fastapi.version, "0.115.0")
        self.assertEqual(fastapi.hashes, tuple(sorted((HASH_A, HASH_B))))

    def test_header_metadata_parsed(self):
        self.write_lock(VALID_BODY)
        header = parse_lock_header(self.lock)
        self.assertEqual(header["schema"], LOCK_HEADER_SCHEMA)
        self.assertEqual(header["pyproject_sha256"], self.digest)
        self.assertEqual(header["python_version"], "3.11")

    def test_unpinned_requirement_rejected(self):
        self.write_lock(f"fastapi>=0.110 --hash=sha256:{HASH_A}\n")
        with self.assertRaisesRegex(ValueError, "exactly pinned"):
            parse_hashed_lock(self.lock)

    def test_missing_hash_rejected(self):
        self.write_lock("fastapi==0.115.0\n")
        with self.assertRaisesRegex(ValueError, "no --hash=sha256"):
            parse_hashed_lock(self.lock)

    def test_malformed_hash_rejected(self):
        self.write_lock("fastapi==0.115.0 --hash=sha256:nothex\n")
        with self.assertRaisesRegex(ValueError, "unexpected token"):
            parse_hashed_lock(self.lock)

    def test_md5_hash_rejected(self):
        self.write_lock(f"fastapi==0.115.0 --hash=md5:{HASH_A[:32]}\n")
        with self.assertRaisesRegex(ValueError, "unexpected token"):
            parse_hashed_lock(self.lock)

    def test_duplicate_distribution_rejected(self):
        self.write_lock(
            f"fastapi==0.115.0 --hash=sha256:{HASH_A}\nFASTAPI==0.115.1 --hash=sha256:{HASH_B}\n"
        )
        with self.assertRaisesRegex(ValueError, "duplicate distribution"):
            parse_hashed_lock(self.lock)

    def test_unterminated_continuation_rejected(self):
        self.write_lock(f"fastapi==0.115.0 \\\n    --hash=sha256:{HASH_A} \")
        with self.assertRaisesRegex(ValueError, "continuation"):
            parse_hashed_lock(self.lock)

    def test_empty_lock_rejected(self):
        self.write_lock("# nothing here\n")
        with self.assertRaisesRegex(ValueError, "no requirements"):
            parse_hashed_lock(self.lock)

    def test_marker_recorded_but_not_required(self):
        self.write_lock(f'fastapi==0.115.0 ; python_version >= "3.11" \\\n    --hash=sha256:{HASH_A}\n')
        entries = parse_hashed_lock(self.lock)
        self.assertEqual(entries[0].marker, '; python_version >= "3.11"')

    def test_extras_marker_stripped_from_name(self):
        self.write_lock(f"uvicorn[standard]==0.54.0 \\\n    --hash=sha256:{HASH_A}\n")
        entries = parse_hashed_lock(self.lock)
        self.assertEqual(entries[0].name, "uvicorn")
        self.assertEqual(entries[0].version, "0.54.0")

    def test_normalize_name_handles_pep503(self):
        self.assertEqual(python_lock.normalize_name("Foo_Bar.Baz"), "foo-bar-baz")


class CheckerTests(LockFixture):
    def test_valid_lock_passes_all_checks(self):
        self.write_lock(VALID_BODY)
        performed = check.check_lock(self.lock, self.pyproject)
        self.assertTrue(any("4 pinned+hashed" in line for line in performed))

    def test_stale_pyproject_digest_fails_closed(self):
        self.write_lock(VALID_BODY, digest="0" * 64)
        with self.assertRaisesRegex(ValueError, "stale lock"):
            check.check_lock(self.lock, self.pyproject)

    def test_missing_header_key_fails_closed(self):
        self.lock.write_text(f"# schema: {LOCK_HEADER_SCHEMA}\n\n" + VALID_BODY)
        with self.assertRaisesRegex(ValueError, "missing header metadata"):
            check.check_lock(self.lock, self.pyproject)

    def test_wrong_schema_fails_closed(self):
        self.write_lock(VALID_BODY, schema="osiris.other.v1")
        with self.assertRaisesRegex(ValueError, "unsupported lock schema"):
            check.check_lock(self.lock, self.pyproject)

    def test_direct_requirement_missing_fails_closed(self):
        body = VALID_BODY.replace("websockets==13.1 \\\n    --hash=sha256:%s\n" % HASH_B, "")
        self.write_lock(body)
        with self.assertRaisesRegex(ValueError, "websockets is not locked"):
            check.check_lock(self.lock, self.pyproject)

    def test_version_outside_declared_range_fails_closed(self):
        body = VALID_BODY.replace("fastapi==0.115.0", "fastapi==1.0.0")
        self.write_lock(body)
        with self.assertRaisesRegex(ValueError, "violates declared range"):
            check.check_lock(self.lock, self.pyproject)

    def test_optional_non_deployment_extra_not_required(self):
        # ib_async (ibkr extra) is intentionally absent from the deployment lock
        self.write_lock(VALID_BODY)
        performed = check.check_lock(self.lock, self.pyproject)
        self.assertTrue(any("streams" in line for line in performed))

    def test_invalid_pep440_version_fails_closed(self):
        body = VALID_BODY.replace("pydantic==2.9.2", "pydantic==not a version")
        self.write_lock(body)
        with self.assertRaisesRegex(ValueError, "exactly pinned|invalid PEP 440"):
            check.check_lock(self.lock, self.pyproject)

    def test_repo_lock_is_consistent_with_repo_pyproject(self):
        root = Path(__file__).resolve().parents[1]
        lock = root / python_lock.LOCK_RELATIVE_PATH
        self.assertTrue(lock.is_file(), f"missing {lock}")
        performed = check.check_lock(lock, root / "services/quant-api/pyproject.toml")
        self.assertTrue(performed)


if __name__ == "__main__":
    unittest.main(verbosity=2)
