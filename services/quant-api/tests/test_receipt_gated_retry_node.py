"""Run the Rust/Python retry process test when the node binary is already built."""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "test_receipt_gated_retry_node.py"
BINARIES = (
    ROOT / "services" / "p2p-node" / "target" / "release" / "osiris-p2p-node",
    ROOT / "services" / "p2p-node" / "target" / "debug" / "osiris-p2p-node",
)


@pytest.mark.skipif(
    not any(path.is_file() for path in BINARIES),
    reason="osiris-p2p-node binary is not built",
)
def test_node_process_does_not_remint_a_tracked_envelope():
    completed = subprocess.run(
        [sys.executable, str(SCRIPT)], check=False, capture_output=True, text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
