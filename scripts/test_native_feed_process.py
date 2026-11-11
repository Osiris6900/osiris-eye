"""Exercise real mapped files, SPSC workers, multipath deduplication and Python binding."""
import hashlib
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services/quant-api/src"))
from osiris_api.control_plane.native import run_native


class NativeProcessTests(unittest.TestCase):
    def setUp(self):
        self.binary = Path(os.getenv("OSIRIS_NATIVE_TEST_BINARY", str(ROOT / "crates/native-feed/target/debug/osiris-native-feed")))
        if not self.binary.is_file():
            self.skipTest("build native-feed before its process integration tests")
        self.sha = hashlib.sha256(self.binary.read_bytes()).hexdigest()

    def test_mapped_parallel_processing_and_multipath_deduplication(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            data = b"".join(struct.pack("<QddI", i, 100.0, 2.0, i) for i in range(1, 2001))
            (root / "one.bin").write_bytes(data)
            (root / "two.bin").write_bytes(data)
            for workers in (1, 4):
                result = run_native(str(self.binary), self.sha, folder, ["one.bin", "two.bin"], workers=workers, prefetch=True)
                self.assertEqual(result["processed"], 2000)
                self.assertEqual(result["duplicates_or_out_of_order"], 2000)
                self.assertEqual(result["notional_sum"], 400000)
                self.assertTrue(result["memory_mapping"])
                self.assertEqual(result["workers"], workers)

    def test_unaligned_file_wrong_binary_digest_and_path_escape_fail(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "bad.bin").write_bytes(b"invalid")
            with self.assertRaises(ValueError):
                run_native(str(self.binary), self.sha, folder, ["bad.bin"])
            with self.assertRaises(ValueError):
                run_native(str(self.binary), "0" * 64, folder, ["bad.bin"])
            with self.assertRaises(ValueError):
                run_native(str(self.binary), self.sha, folder, [str(self.binary)])


if __name__ == "__main__":
    unittest.main()
