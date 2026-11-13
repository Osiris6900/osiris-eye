"""Exercise the actual CLI pipeline, replacing only the external HTTP boundary."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def runner(monkeypatch):
    spec = importlib.util.spec_from_file_location("depth_paper_probe_test", ROOT / "scripts/run_depth_paper_acceptance.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Tests run against mutable worktrees. Genuine CLI execution requires clean,
    # committed source; this fixture substitutes only those Git read results.
    monkeypatch.setattr(module.subprocess, "check_output", lambda args, **kw:
                        "a" * 40 if args == ["git", "rev-parse", "HEAD"] else b"")
    monkeypatch.delenv("OSIRIS_ENABLE_LIVE_EXECUTION", raising=False)
    monkeypatch.delenv("OSIRIS_PUBLIC_COINBASE_ENABLED", raising=False)
    return module


def test_full_probe_preserves_operator_state_and_retains_original_source_bytes(runner, tmp_path, monkeypatch):
    raw = json.dumps({"sequence": 1, "asks": [["101", "1", 1], ["102", "1", 1]],
                      "bids": [["99", "1", 1], ["98", "1", 1]]}).encode()

    class Opener:
        def open(self, request, timeout):
            assert request.get_method() == "GET"
            assert request.full_url.startswith("https://api.exchange.coinbase.com/products/")
            return io.BytesIO(raw)

    monkeypatch.setattr(runner, "build_opener", lambda *args: Opener())
    operator = tmp_path / "operator.sqlite3"
    operator.write_bytes(b"existing operator state")
    monkeypatch.setenv("OSIRIS_PAPER_EXECUTION_DB", str(operator))
    directory = tmp_path / "evidence"
    result = runner.probe(directory)
    assert result["passed"] is True, result.get("error")
    assert result["crash_rollback_verified"] is True
    assert result["new_process_replay_verified"] is True
    assert result["snapshot"]["fill_count"] == 4
    assert result["snapshot"]["cash"] == "9999.99956"
    assert result["snapshot"]["realized_pnl"] == "-0.00044"
    assert result["snapshot"]["positions"] == []
    assert len(result["captures"]) == 2
    assert (directory / "response-1.json").read_bytes() == raw
    assert result["captures"][0]["body_sha256"] == hashlib.sha256(raw).hexdigest()
    assert operator.read_bytes() == b"existing operator state"
    assert os.environ["OSIRIS_PAPER_EXECUTION_DB"] == str(operator)
    assert result["release_gate_accepted"] is result["execution_calibrated"] is False


def test_probe_source_failure_retains_failed_result_and_restores_account_env(runner, tmp_path, monkeypatch):
    class Opener:
        def open(self, *args, **kwargs):
            raise OSError("source unavailable")

    monkeypatch.setattr(runner, "build_opener", lambda *args: Opener())
    monkeypatch.delenv("OSIRIS_PAPER_EXECUTION_DB", raising=False)
    directory = tmp_path / "failed"
    result = runner.probe(directory)
    assert result["passed"] is False
    assert result["exercises"] == result["captures"] == []
    assert result["error"]["type"] == "OSError"
    assert json.loads((directory / "result.json").read_text())["passed"] is False
    assert "OSIRIS_PAPER_EXECUTION_DB" not in os.environ
