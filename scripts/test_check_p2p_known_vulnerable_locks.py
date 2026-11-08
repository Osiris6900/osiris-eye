#!/usr/bin/env python3
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_p2p_known_vulnerable_locks as ratchet

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "services/p2p-node/Cargo.lock"


def lock_text(packages):
    lines = ["version = 4", ""]
    for name, version in packages:
        lines += ["[[package]]", f'name = "{name}"', f'version = "{version}"', ""]
    return "\n".join(lines)


class VulnerableLockRatchetTests(unittest.TestCase):
    def inspect(self, packages):
        return ratchet.inspect(tomllib.loads(lock_text(packages)))

    def test_versions_below_the_fix_line_are_blocked(self):
        blocked, maintenance = self.inspect([
            ("hickory-proto", "0.25.2"),
            ("hickory-resolver", "0.26.1"),
            ("hickory-net", "0.26.0"),
            ("yamux", "0.12.1"),
            ("yamux", "0.13.9"),
            ("yamux", "0.14.1"),
            ("paste", "1.0.15"),
        ])
        self.assertEqual(
            [(name, version) for name, version, _floor, _advisory in blocked],
            [
                ("hickory-proto", "0.25.2"),
                ("hickory-resolver", "0.26.1"),
                ("hickory-net", "0.26.0"),
                ("yamux", "0.12.1"),
                ("yamux", "0.13.9"),
            ],
        )
        self.assertEqual(maintenance, [
            ("paste", "1.0.15", "RUSTSEC-2024-0436 (unmaintained; no fixed crates.io release)"),
        ])

    def test_fixed_lines_and_maintenance_only_paste_pass(self):
        blocked, maintenance = self.inspect([
            ("hickory-proto", "0.26.1"),
            ("hickory-resolver", "0.26.2"),
            ("hickory-net", "0.26.1"),
            ("yamux", "0.13.10"),
            ("paste", "1.0.15"),
        ])
        self.assertEqual(blocked, [])
        self.assertEqual(len(maintenance), 1)

    def test_prerelease_below_the_fix_line_is_blocked(self):
        blocked, _maintenance = self.inspect([("hickory-proto", "0.26.1-beta.1")])
        self.assertEqual(blocked[0][1], "0.26.1-beta.1")

    def test_malformed_lock_fails_closed(self):
        with self.assertRaises(ValueError):
            ratchet.inspect({"package": [{"name": "yamux"}]})

    def test_current_p2p_lock_keeps_patched_versions(self):
        blocked, maintenance = ratchet.inspect(tomllib.loads(LOCK.read_text()))
        self.assertEqual(blocked, [])
        versions = {}
        for name, version, _advisory in maintenance:
            versions.setdefault(name, set()).add(version)
        self.assertEqual(versions, {"paste": {"1.0.15"}})
        present = {
            (package["name"], package["version"])
            for package in tomllib.loads(LOCK.read_text())["package"]
        }
        self.assertTrue({
            ("hickory-proto", "0.26.3"),
            ("hickory-resolver", "0.26.3"),
            ("hickory-net", "0.26.3"),
            ("yamux", "0.14.1"),
        }.issubset(present))
        self.assertFalse(present & {
            ("hickory-proto", "0.25.2"),
            ("hickory-resolver", "0.25.2"),
            ("yamux", "0.12.1"),
            ("yamux", "0.13.10"),
        })
        completed = subprocess.run(
            [sys.executable, str(Path(ratchet.__file__)), str(LOCK)],
            check=False, capture_output=True, text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("REVIEW: paste 1.0.15:", completed.stdout)
        self.assertNotIn("BLOCK:", completed.stdout)

    def test_cli_rejects_a_vulnerable_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "Cargo.lock"
            path.write_text(lock_text([("yamux", "0.12.1")]))
            completed = subprocess.run(
                [sys.executable, str(Path(ratchet.__file__)), str(path)],
                check=False, capture_output=True, text=True,
            )
        self.assertEqual(completed.returncode, 1)
        self.assertIn("BLOCK: yamux 0.12.1:", completed.stdout)
        self.assertIn("fixed in 0.13.10", completed.stdout)


if __name__ == "__main__":
    unittest.main()
