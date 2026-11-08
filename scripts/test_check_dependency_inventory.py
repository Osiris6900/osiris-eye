import json
import tempfile
import unittest
from pathlib import Path
from check_dependency_inventory import validate

class DependencyInventoryTests(unittest.TestCase):
    def test_missing_files_fail(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertTrue(validate(root))

    def test_valid_inventory(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            for name in ("services/p2p-node/Cargo.lock", "services/quant-api/pyproject.toml"):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("locked", encoding="utf-8")
            npm = root / "apps/web/package-lock.json"
            npm.parent.mkdir(parents=True)
            npm.write_text(json.dumps({"lockfileVersion": 3, "packages": {}}))
            self.assertEqual(validate(root), [])

    def test_invalid_npm_graph_fails(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            for name in ("services/p2p-node/Cargo.lock", "services/quant-api/pyproject.toml"):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("locked")
            npm = root / "apps/web/package-lock.json"
            npm.parent.mkdir(parents=True)
            npm.write_text("{}")
            self.assertTrue(validate(root))

if __name__ == "__main__":
    unittest.main()
