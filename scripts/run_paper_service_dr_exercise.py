#!/usr/bin/env python3
"""Exercise a real local API process through backup, SIGKILL and restore.

Only disposable synthetic paper state and generated bearer credentials are used.
Measured recovery belongs to one local host, not production or independent-host DR.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services/quant-api/src"))
sys.path.insert(0, str(ROOT))
from scripts.run_paper_dr_exercise import digest, verify_sqlite  # noqa: E402

SCHEMA = "osiris.local-paper-service-dr.v1"
MAX_BACKUP_BYTES = 16 * 1024 * 1024
STATE_FIELDS = (
    "cash", "initial_cash", "realized_pnl", "unrealized_pnl", "fill_count",
    "order_count", "positions", "marks", "orders", "open_orders", "recent_fills",
    "kill_switch_active", "live_execution", "broker_order_submitted", "mode",
)
SOURCE_FILES = (
    "scripts/run_paper_service_dr_exercise.py", "scripts/run_paper_dr_exercise.py",
    "services/quant-api/src/osiris_api/main.py",
    "services/quant-api/src/osiris_api/paper_execution.py",
    "services/quant-api/src/osiris_api/decimal_safety.py",
    "services/quant-api/src/osiris_api/risk.py",
    "services/quant-api/requirements-lock.txt",
)


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError(reason)


def state(payload: dict) -> dict:
    require(isinstance(payload, dict) and payload.get("initialized") is True,
            "initialized_snapshot_required")
    require(payload.get("live_execution") is False
            and payload.get("broker_order_submitted") is False, "paper_only_required")
    require(all(name in payload for name in STATE_FIELDS), "incomplete_snapshot")
    return {name: payload[name] for name in STATE_FIELDS}


def verify_recovered(expected: dict, recovered: dict, reconciliation: dict) -> None:
    require(state(expected) == state(recovered), "restored_financial_state_mismatch")
    require(all(isinstance(item.get("audit"), list) and item["audit"]
                and all(isinstance(row, dict) for row in item["audit"])
                for item in (expected, recovered)), "restored_journal_required")
    require(expected.get("audit") == recovered.get("audit"), "restored_journal_mismatch")
    require(reconciliation.get("passed") is True and not reconciliation.get("breaks"),
            "restored_reconciliation_failed")


def verify_replay(before: dict, after: dict, replay: dict) -> None:
    require(replay.get("status") == "idempotent_replay", "intent_replay_not_idempotent")
    require(replay.get("live_execution") is False
            and replay.get("broker_order_submitted") is False, "replay_not_paper_only")
    require(state(before) == state(after), "replay_changed_financial_state")


def restore_backup(backup: Path, target: Path, expected_sha256: str) -> None:
    require(isinstance(expected_sha256, str)
            and re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is not None,
            "expected_backup_digest_required")
    require(backup.is_file() and not backup.is_symlink()
            and 0 < backup.stat().st_size <= MAX_BACKUP_BYTES, "invalid_backup_file")
    require(digest(backup) == expected_sha256, "backup_digest_mismatch")
    try:
        verify_sqlite(backup)
    except (sqlite3.Error, RuntimeError) as error:
        raise ValueError("backup_integrity_failed") from error
    # Validation precedes target creation; no existing service file is overwritten.
    with backup.open("rb") as source, target.open("xb") as destination:
        os.chmod(target, 0o600)
        shutil.copyfileobj(source, destination)
    require(digest(target) == expected_sha256, "restore_copy_digest_mismatch")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Service:
    def __init__(self, directory: Path, database: Path, token: str, *, port: int = 0):
        self.directory, self.database, self.token = directory, database, token
        self.proc = None
        self.log = None
        self.port = port

    def start(self) -> None:
        self.directory.mkdir(mode=0o700)
        self.log = (self.directory / "service.log").open("xb")
        os.chmod(self.directory / "service.log", 0o600)
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith("OSIRIS_") and key != "PYTHONPATH"}
        environment.update({
            "OSIRIS_ENV": "production", "OSIRIS_API_TOKEN": self.token,
            "OSIRIS_PAPER_EXECUTION_DB": str(self.database),
            "OSIRIS_DB_PATH": str(self.directory / "research.sqlite3"),
            "OSIRIS_CONTROL_DB": str(self.directory / "control.sqlite3"),
            "PYTHONPATH": str(ROOT / "services/quant-api/src"),
            "OSIRIS_PUBLIC_COINBASE_ENABLED": "false",
            "OSIRIS_PUBLIC_KRAKEN_ENABLED": "false",
            "OSIRIS_PUBLIC_BINANCE_ENABLED": "false",
            "OSIRIS_PUBLIC_STOOQ_ENABLED": "false",
        })
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", self.port))
            listener.listen()
            self.port = listener.getsockname()[1]
            self.proc = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "osiris_api.main:app", "--fd",
                 str(listener.fileno()), "--log-level", "error", "--no-access-log"],
                cwd=self.directory, env=environment, stdin=subprocess.DEVNULL,
                stdout=self.log, stderr=self.log, pass_fds=(listener.fileno(),),
            )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            require(self.proc.poll() is None, "service_exited_before_ready")
            try:
                if self.request("GET", "/health", authenticated=False)[0] == 200:
                    return
            except (URLError, TimeoutError, OSError):
                pass
            time.sleep(0.05)
        raise ValueError("service_start_timeout")

    def request(self, method: str, path: str, body=None, *, authenticated: bool = True):
        require(path.startswith("/paper/execution/") or path == "/health",
                "fixed_local_api_path_required")
        headers = {"Content-Type": "application/json"}
        if authenticated:
            headers["Authorization"] = "Bearer " + self.token
        request = Request(f"http://127.0.0.1:{self.port}" + path, method=method,
                          data=None if body is None else json.dumps(body).encode(), headers=headers)
        try:
            response = build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            raw = response.read(2_097_153)
            require(len(raw) <= 2_097_152, "api_response_too_large")
            return response.status, json.loads(raw)

    def call(self, method: str, path: str, body=None) -> dict:
        status, payload = self.request(method, path, body)
        require(status == 200 and isinstance(payload, dict), "paper_api_request_failed")
        return payload

    def kill(self) -> None:
        require(self.proc is not None and self.proc.poll() is None, "primary_not_running")
        self.proc.kill()
        require(self.proc.wait(timeout=5) == -signal.SIGKILL, "primary_not_sigkilled")

    def close(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
        if self.log is not None:
            self.log.close()


def sources() -> dict:
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCE_FILES}


def write(path: Path, payload: dict) -> None:
    with path.open("xb") as file:
        os.chmod(path, 0o600)
        file.write((json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + "\n").encode())


def run(output: Path) -> dict:
    require(not os.getenv("OSIRIS_ENABLE_LIVE_EXECUTION"), "live_execution_environment_refused")
    require(not output.resolve().is_relative_to(ROOT), "evidence_must_be_outside_checkout")
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    before_sources = sources()
    report = {
        "schema": SCHEMA, "passed": False, "candidate_commit": commit,
        "source_sha256": before_sources,
        "working_tree_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT)),
        "scope": "one-local-host-disposable-authenticated-api-processes",
        "live_execution": False, "broker_order_submitted": False,
        "independent_hosts_verified": False, "production_service_recovery_verified": False,
        "rto_slo_certified": False, "rpo_slo_certified": False,
        "t18_release_gate_accepted": False,
    }
    with tempfile.TemporaryDirectory(prefix="osiris-paper-service-dr-") as temporary:
        root = Path(temporary)
        primary_path, restored_path = root / "primary.sqlite3", root / "restored.sqlite3"
        primary = Service(root / "primary-service", primary_path, secrets.token_urlsafe(32))
        restored = None
        try:
            primary.start()
            require(primary.request("GET", "/paper/execution/snapshot", authenticated=False)[0] == 401,
                    "unauthenticated_api_was_not_refused")
            primary.call("POST", "/paper/execution/initialize", {"initial_cash": "1000"})
            now = datetime.now(timezone.utc).isoformat()
            order = {"client_order_id": "service-dr-order", "symbol": "BTC", "side": "BUY",
                     "quantity": "2", "price": "100", "fee": "1", "working": True,
                     "observed_at": now, "now": now}
            require(primary.call("POST", "/paper/execution/orders", order)["state"] == "accepted",
                    "working_order_not_accepted")
            first = {"client_fill_id": "service-dr-backed-up", "quantity": "1",
                     "observed_at": now, "now": now}
            path = "/paper/execution/orders/service-dr-order/fills"
            primary.call("POST", path, first)
            expected = primary.call("GET", "/paper/execution/snapshot")
            backup = output / "service-online-backup.sqlite3"
            with sqlite3.connect(primary_path) as source, sqlite3.connect(backup) as destination:
                source.backup(destination)
            os.chmod(backup, 0o600)
            verify_sqlite(backup)
            backup_completed = time.monotonic()
            backup_sha = digest(backup)
            last = {"client_fill_id": "service-dr-after-backup", "quantity": "1",
                    "observed_at": now, "now": now}
            require(primary.call("POST", path, last)["state"] == "filled", "post_backup_write_failed")
            last_acknowledged = time.monotonic()
            acknowledged = primary.call("GET", "/paper/execution/snapshot")
            require(acknowledged["fill_count"] == expected["fill_count"] + 1,
                    "post_backup_acknowledged_fill_missing")
            outage_started = time.monotonic()
            primary.kill()
            try:
                primary.request("GET", "/health", authenticated=False)
            except (URLError, TimeoutError, OSError):
                report["api_outage_observed"] = True
            else:
                raise ValueError("primary_api_still_responds_after_sigkill")
            primary_path.unlink()
            restore_backup(backup, restored_path, backup_sha)
            restored = Service(root / "restored-service", restored_path,
                               secrets.token_urlsafe(32), port=primary.port)
            restored.start()
            # The replacement has a new token; the previous process token is refused.
            old_token = restored.token
            restored.token = primary.token
            require(restored.request("GET", "/paper/execution/snapshot")[0] == 401,
                    "old_service_credential_not_refused")
            restored.token = old_token
            recovery = restored.call("POST", "/paper/execution/recover")
            require(recovery.get("passed") is True, "api_recovery_failed")
            recovered = restored.call("GET", "/paper/execution/snapshot")
            verify_recovered(expected, recovered,
                             restored.call("GET", "/paper/execution/reconciliation-report"))
            recovered_at = time.monotonic()
            before_replay = recovered
            for endpoint, payload in (("/paper/execution/orders", order), (path, first)):
                replay = restored.call("POST", endpoint, payload)
                after = restored.call("GET", "/paper/execution/snapshot")
                verify_replay(before_replay, after, replay)
                before_replay = after
            # The acknowledged write after the backup is actually absent. Reapply
            # its stable client fill ID once; then prove its retry is idempotent.
            require(restored.call("POST", path, last)["state"] == "filled", "lost_intent_not_resumed")
            resumed = restored.call("GET", "/paper/execution/snapshot")
            require(state(acknowledged) == state(resumed), "resumed_state_does_not_match_acknowledged")
            replay = restored.call("POST", path, last)
            final = restored.call("GET", "/paper/execution/snapshot")
            verify_replay(resumed, final, replay)
            require(restored.call("GET", "/paper/execution/reconcile")["passed"] is True,
                    "resumed_reconciliation_failed")
            restored.close()
            retained = output / "service-resumed.sqlite3"
            shutil.copyfile(restored_path, retained)
            os.chmod(retained, 0o600)
            require(before_sources == sources() and commit == subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                "source_changed_during_service_dr")
            report.update({
                "passed": True, "authenticated_api_tested": True,
                "previous_process_credential_refused": True,
                "actual_primary_sigkill": True, "consistent_online_backup_tested": True,
                "different_database_restore_tested": True, "same_loopback_port_rebound": True,
                "backup_sha256": backup_sha, "resumed_database_sha256": digest(retained),
                "backup_file": backup.name, "resumed_file": retained.name,
                "backup_snapshot": expected, "acknowledged_before_failure": acknowledged,
                "recovered_before_replay": recovered, "resumed_snapshot": final,
                "measured_rto_ms_local_only": round((recovered_at - outage_started) * 1000, 3),
                "recovery_point_age_at_failure_ms_local_only": round((outage_started - backup_completed) * 1000, 3),
                "latest_lost_ack_age_at_failure_ms_local_only": round((outage_started - last_acknowledged) * 1000, 3),
                "acknowledged_fills_missing_at_restore": acknowledged["fill_count"] - recovered["fill_count"],
                "backed_up_intent_replays_verified": 2,
                "lost_intent_reapplied_once": True, "resumed_intent_replay_verified": True,
                "limitations": ["one local host", "generated temporary credentials", "synthetic paper orders",
                    "ledger database only; not every platform store", "one deliberate post-backup write is lost until replay",
                    "no deployment storage, routing, external custody or RTO/RPO acceptance"],
            })
        except Exception as error:
            report["error"] = {"type": type(error).__name__, "message": str(error)}
        finally:
            primary.close()
            if restored is not None:
                restored.close()
    write(output / "service-recovery-report.json", report)
    write(output / "files-sha256.json", {p.name: digest(p) for p in sorted(output.iterdir()) if p.is_file()})
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = run(args.output_dir)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(json.dumps({"passed": False, "error": str(error), "t18_release_gate_accepted": False}))
        return 1
    print(json.dumps({key: result[key] for key in (
        "passed", "candidate_commit", "scope", "t18_release_gate_accepted")}
        | {"error": result.get("error"), "measured_rto_ms_local_only": result.get("measured_rto_ms_local_only"),
           "acknowledged_fills_missing_at_restore": result.get("acknowledged_fills_missing_at_restore")}))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
