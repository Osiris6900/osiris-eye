"""Trusted capture hashes bind receive times as well as original response bytes."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta

import pytest
from test_bls_macro_capture import COMMIT, NOW, capture, policy

from osiris_api import macro_economic_watch as macro


def consistently_backdate(directory, result, *, now):
    original_anchor = result["capture_sha256"]
    earlier = now - timedelta(seconds=120)
    plan = json.loads((directory / "plan.json").read_bytes())
    plan["requested_at"] = earlier.isoformat()
    (directory / "plan.json").write_bytes(macro._canonical(plan))
    rows = macro._normalize((directory / "response.bin").read_bytes(), plan, earlier)
    (directory / "observations.json").write_bytes(macro._canonical(rows))
    result["received_at"] = earlier.isoformat()
    result["plan_sha256"] = macro._digest(macro._canonical(plan))
    result["observations_sha256"] = macro._digest(macro._canonical(rows))
    # Rehash every local field the author can edit. The original response and
    # its trusted digest remain unchanged; the caller's capture anchor does not.
    result["capture_sha256"] = macro._digest(
        macro._canonical({k: v for k, v in result.items() if k != "capture_sha256"})
    )
    (directory / "capture.json").write_bytes(macro._canonical(result))
    assert result["capture_sha256"] != original_anchor
    assert {r["available_at"] for r in rows} == {earlier.isoformat()}
    return original_anchor


def test_consistent_timestamp_rehash_cannot_replace_trusted_capture_anchor(tmp_path):
    directory, result, response_digest = capture(tmp_path)
    anchor = consistently_backdate(directory, result, now=NOW)
    assert macro._digest((directory / "response.bin").read_bytes()) == response_digest
    # The rewritten artifact is internally coherent. It is nevertheless not
    # the artifact whose metadata digest the caller retained at capture time.
    assert macro.verify_bls_capture(
        directory, expected_commit=COMMIT, expected_response_sha256=response_digest,
        expected_capture_sha256=result["capture_sha256"],
    )["passed"] is True
    with pytest.raises(ValueError, match="bls_capture_digest_mismatch"):
        macro.verify_bls_capture(
            directory, expected_commit=COMMIT, expected_response_sha256=response_digest,
            expected_capture_sha256=anchor,
        )


def test_cli_rejects_rehashed_timestamp_before_provisioning_database(tmp_path):
    now = datetime.now(UTC)
    directory, result, response_digest = capture(tmp_path, now=now)
    anchor = consistently_backdate(directory, result, now=now)
    fixture_policy = policy()
    fixture_policy[macro.PROVIDER]["quality_review"]["reviewed_at"] = now.isoformat()
    policy_file = tmp_path / "synthetic-policy.json"
    policy_file.write_text(json.dumps(fixture_policy))
    database = tmp_path / "must-not-exist.sqlite3"
    process = subprocess.run(
        [sys.executable, str(macro.ROOT / "scripts/record_bls_macro.py"), "ingest",
         "--capture-dir", str(directory), "--commit", COMMIT,
         "--response-sha256", response_digest, "--capture-sha256", anchor,
         "--database", str(database), "--policy-json", str(policy_file)],
        check=False, capture_output=True, text=True, timeout=30,
        env={**os.environ, "OSIRIS_P2P_INGEST_ENABLED": "1"},
    )
    assert process.returncode == 1
    assert json.loads(process.stdout)["error"] == "bls_capture_digest_mismatch"
    assert not database.exists()
