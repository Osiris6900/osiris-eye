"""A running local API recovers exact backup state without fabricating zero RPO."""
import copy
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts/run_paper_service_dr_exercise.py"
spec = importlib.util.spec_from_file_location("paper_service_dr", SCRIPT)
dr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dr)


@pytest.fixture(scope="module")
def exercise(tmp_path_factory):
    output = tmp_path_factory.mktemp("service-dr-parent") / "evidence"
    report = dr.run(output)
    assert report["passed"] is True, report.get("error")
    return output, report


def test_actual_service_outage_backup_restore_and_stable_intent_recovery(exercise):
    output, report = exercise
    assert report["actual_primary_sigkill"] is True
    assert report["api_outage_observed"] is True
    assert report["authenticated_api_tested"] is True
    assert report["previous_process_credential_refused"] is True
    assert report["same_loopback_port_rebound"] is True
    assert report["backup_snapshot"] == report["recovered_before_replay"]
    assert report["backup_snapshot"]["fill_count"] == 1
    assert report["acknowledged_before_failure"]["fill_count"] == 2
    assert report["resumed_snapshot"]["fill_count"] == 2
    assert report["acknowledged_fills_missing_at_restore"] == 1
    assert report["measured_rto_ms_local_only"] > 0
    assert report["recovery_point_age_at_failure_ms_local_only"] >= 0
    assert report["latest_lost_ack_age_at_failure_ms_local_only"] >= 0
    assert report["lost_intent_reapplied_once"] is True
    assert report["resumed_intent_replay_verified"] is True
    for name in ("independent_hosts_verified", "production_service_recovery_verified",
                 "rto_slo_certified", "rpo_slo_certified", "t18_release_gate_accepted",
                 "live_execution", "broker_order_submitted"):
        assert report[name] is False
    assert dr.digest(output / report["backup_file"]) == report["backup_sha256"]
    assert dr.digest(output / report["resumed_file"]) == report["resumed_database_sha256"]
    assert "Bearer " not in json.dumps(report)


@pytest.mark.parametrize("change,reason", [
    ("cash", "restored_financial_state_mismatch"),
    ("audit", "restored_journal_mismatch"),
    ("missing-audit", "restored_journal_required"),
    ("reconciliation", "restored_reconciliation_failed"),
])
def test_recovery_validation_rejects_state_or_journal_or_reconciliation_mismatch(exercise, change, reason):
    _, report = exercise
    expected = report["backup_snapshot"]
    recovered = copy.deepcopy(report["recovered_before_replay"])
    reconciliation = {"passed": True, "breaks": []}
    if change == "cash":
        recovered["cash"] = "1000"
    elif change == "audit":
        recovered["audit"] = recovered["audit"][:-1]
    elif change == "missing-audit":
        expected = copy.deepcopy(expected)
        del expected["audit"]
        del recovered["audit"]
    else:
        reconciliation = {"passed": False, "breaks": ["cash"]}
    with pytest.raises(ValueError, match=reason):
        dr.verify_recovered(expected, recovered, reconciliation)


@pytest.mark.parametrize("change,reason", [
    ("response", "intent_replay_not_idempotent"),
    ("fill_count", "replay_changed_financial_state"),
    ("live", "replay_not_paper_only"),
])
def test_idempotency_validation_rejects_new_fills_or_claimed_approval(exercise, change, reason):
    _, report = exercise
    before = report["backup_snapshot"]
    after = copy.deepcopy(before)
    replay = {"status": "idempotent_replay", "live_execution": False, "broker_order_submitted": False}
    if change == "response":
        replay["status"] = "filled"
    elif change == "fill_count":
        after["fill_count"] += 1
    else:
        replay["broker_order_submitted"] = True
    with pytest.raises(ValueError, match=reason):
        dr.verify_replay(before, after, replay)


def test_corrupt_matching_digest_backup_does_not_create_restore_target(tmp_path):
    backup, target = tmp_path / "bad.sqlite3", tmp_path / "restored.sqlite3"
    backup.write_bytes(b"not a SQLite backup")
    with pytest.raises(ValueError, match="backup_integrity_failed"):
        dr.restore_backup(backup, target, dr.digest(backup))
    assert not target.exists()


def test_backup_digest_and_no_overwrite_guards(tmp_path):
    backup, target = tmp_path / "backup.sqlite3", tmp_path / "restored.sqlite3"
    with sqlite3.connect(backup) as connection:
        connection.execute("CREATE TABLE example(id INTEGER)")
    with pytest.raises(ValueError, match="backup_digest_mismatch"):
        dr.restore_backup(backup, target, "0" * 64)
    assert not target.exists()
    target.write_bytes(b"preserve existing state")
    with pytest.raises(FileExistsError):
        dr.restore_backup(backup, target, dr.digest(backup))
    assert target.read_bytes() == b"preserve existing state"


def test_existing_evidence_directory_and_live_environment_are_refused(tmp_path, monkeypatch):
    existing = tmp_path / "existing"
    existing.mkdir()
    (existing / "preserve").write_text("evidence")
    with pytest.raises(FileExistsError):
        dr.run(existing)
    assert (existing / "preserve").read_text() == "evidence"
    monkeypatch.setenv("OSIRIS_ENABLE_LIVE_EXECUTION", "1")
    destination = tmp_path / "new"
    with pytest.raises(ValueError, match="live_execution_environment_refused"):
        dr.run(destination)
    assert not destination.exists()
