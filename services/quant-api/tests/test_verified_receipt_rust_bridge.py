"""Run the cross-process Rust verifier to Python ledger bridge when cargo is present."""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "test_verified_receipt_rust_bridge.py"


@pytest.mark.skipif(shutil.which("cargo") is None, reason="cargo is required to build the Rust verifier")
def test_rust_verifier_persists_to_python_ledger():
    completed = subprocess.run([sys.executable, str(SCRIPT)], check=False, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stdout + completed.stderr
