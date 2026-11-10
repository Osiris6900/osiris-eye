#!/usr/bin/env python3
"""Regression checks for cross-host lifecycle and SSH endpoint validation."""
import base64
import json
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import p2p_workflow_coordination as coordination


PUBLIC_KEY = "ssh-ed25519 " + base64.b64encode(
    b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20" + bytes(range(32))
).decode()


class CoordinationTests(unittest.TestCase):
    def test_soak_receiver_can_wait_for_a_bounded_long_run(self):
        with patch.object(coordination, 'artifact_exists', return_value=True):
            coordination.wait_for_artifact('owner/repo', '123', 'soak-finished', 2400)
        for value in (0, -1, 2701, float('inf'), float('nan')):
            with self.assertRaises(ValueError):
                coordination.wait_for_artifact('owner/repo', '123', 'soak-finished', value)

    def test_wait_retries_transient_api_error_then_observes_readiness(self):
        with patch.object(coordination, "artifact_exists", side_effect=[
            subprocess.TimeoutExpired("gh", 30), False, True
        ]), patch.object(coordination.time, "sleep") as sleep:
            coordination.wait_for_artifact("owner/repo", "123", "ready-123", 20)
            self.assertEqual(sleep.call_count, 2)

    def test_missing_artifact_times_out_without_skipping_gate(self):
        with patch.object(coordination, "artifact_exists", return_value=False), \
             patch.object(coordination.time, "monotonic", side_effect=[0, 2]):
            with self.assertRaises(TimeoutError):
                coordination.wait_for_artifact("owner/repo", "123", "ready-123", 1)

    def test_expired_artifact_does_not_satisfy_gate(self):
        result = subprocess.CompletedProcess([], 0, json.dumps({"artifacts": [
            {"name": "ready-123", "expired": True}
        ]}))
        with patch.object(coordination.subprocess, "run", return_value=result):
            self.assertFalse(coordination.artifact_exists("owner/repo", "123", "ready-123"))

    def test_previous_attempt_artifact_cannot_release_current_hosts(self):
        result = subprocess.CompletedProcess([], 0, json.dumps({"artifacts": [
            {"name": "ready-123-1", "expired": False}
        ]}))
        with patch.object(coordination.subprocess, "run", return_value=result):
            self.assertFalse(coordination.artifact_exists("owner/repo", "123", "ready-123-2"))
            self.assertTrue(coordination.artifact_exists("owner/repo", "123", "ready-123-1"))

    def test_api_call_cannot_outlive_remaining_deadline(self):
        result = subprocess.CompletedProcess([], 0, json.dumps({"artifacts": []}))
        with patch.object(coordination.subprocess, "run", return_value=result) as run, \
             patch.object(coordination.time, "monotonic", side_effect=[0, .75, 1, 1]):
            with self.assertRaises(TimeoutError):
                coordination.wait_for_artifact("owner/repo", "123", "ready-123-1", 1)
            self.assertEqual(run.call_args.kwargs["timeout"], .25)

    def test_success_arriving_after_deadline_does_not_release_gate(self):
        with patch.object(coordination, "artifact_exists", return_value=True), \
             patch.object(coordination.time, "monotonic", side_effect=[0, .5, 1.1]):
            with self.assertRaises(TimeoutError):
                coordination.wait_for_artifact("owner/repo", "123", "ready-123-1", 1)

    def test_cross_host_workflows_scope_coordination_to_attempt(self):
        workflows = Path(__file__).resolve().parents[1] / ".github" / "workflows"
        for filename in ("p2p-real-cross-host-e2ee.yml", "p2p-three-host-e2ee.yml",
                         "p2p-tailscale-cross-host.yml"):
            content = (workflows / filename).read_text()
            for line in content.splitlines():
                if "${{ github.run_id }}" in line and "run-id:" not in line:
                    self.assertIn("${{ github.run_id }}-${{ github.run_attempt }}", line)
            # Download API selector remains a run ID, not a composite artifact name.
            self.assertNotRegex(content, r"run-id:\s*.*github.run_attempt")

    def test_overlay_workflow_gates_both_jobs_before_checkout(self):
        content = (Path(__file__).resolve().parents[1] / ".github" / "workflows" /
                   "p2p-tailscale-cross-host.yml").read_text()
        jobs = re.findall(r"^  host-[ab]:\n(.*?)(?=^  host-|\Z)", content, re.M | re.S)
        self.assertEqual(len(jobs), 2)
        for job in jobs:
            gate = job.split("    steps:", 1)[0]
            self.assertIn("github.event_name == 'workflow_dispatch' ||", gate)
            for restriction in ("github.event_name == 'workflow_run'", 
                                "github.event.workflow_run.conclusion == 'success'",
                                "github.event.workflow_run.event == 'push'",
                                "github.event.workflow_run.head_branch == 'main'",
                                "github.event.workflow_run.head_repository.full_name == github.repository"):
                self.assertIn(restriction, gate)

    def test_download_retries_blob_404_until_endpoint_bytes_exist(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "p2p-endpoint.json"
            calls = []

            def simulated_download(command, **kwargs):
                calls.append(command)
                self.assertIn("--name", command)
                self.assertEqual(
                    command[command.index("--name") + 1],
                    "p2p-endpoint-42756305177-1",
                )
                self.assertEqual(command[command.index("--repo") + 1], "owner/repo")
                self.assertEqual(command[command.index("download") + 1], "42756305177")
                if len(calls) == 1:
                    return subprocess.CompletedProcess(command, 1, "", "BlobNotFound")
                staged = Path(command[command.index("--dir") + 1])
                (staged / "p2p-endpoint.json").write_text(json.dumps({
                    "tailscale_ip": "100.64.1.9", "port": 4001,
                    "peer_id": "12D3KooWValidP2PIdentity1",
                }))
                return subprocess.CompletedProcess(command, 0, "", "")

            with patch.object(coordination.subprocess, "run", side_effect=simulated_download), \
                 patch.object(coordination.time, "sleep") as sleep:
                coordination.download_same_run_artifact(
                    "owner/repo", "42756305177", "1",
                    "p2p-endpoint-42756305177-1", destination,
                )
            self.assertEqual(len(calls), 2)
            sleep.assert_called_once()
            self.assertEqual(json.loads(destination.read_text())["port"], 4001)

    def test_download_retries_success_without_valid_file(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "p2p-host-a-result.json"
            count = 0

            def simulated_download(command, **kwargs):
                nonlocal count
                count += 1
                staged = Path(command[command.index("--dir") + 1])
                (staged / destination.name).write_text(
                    '{"status":"not_a_valid_acceptance"}' if count == 1
                    else '{"status":"failure"}'
                )
                return subprocess.CompletedProcess(command, 0, "", "")

            with patch.object(coordination.subprocess, "run", side_effect=simulated_download), \
                 patch.object(coordination.time, "sleep"):
                coordination.download_same_run_artifact(
                    "owner/repo", "42756305177", "2",
                    "p2p-host-a-finished-42756305177-2", destination,
                )
            self.assertEqual(count, 2)
            self.assertEqual(json.loads(destination.read_text())["status"], "failure")

    def test_download_never_accepts_other_attempt_or_noncoordination_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "p2p-endpoint.json"
            for name, attempt, expected in (
                ("p2p-endpoint-42756305177-1", "2", "attempt"),
                ("other-endpoint", "1", "attempt"),
                ("p2p-endpoint-42756305177-1", "nan", "identity"),
            ):
                with self.assertRaisesRegex(ValueError, expected):
                    coordination.download_same_run_artifact(
                        "owner/repo", "42756305177", attempt, name, path,
                    )
            with self.assertRaisesRegex(ValueError, "destination"):
                coordination.download_same_run_artifact(
                    "owner/repo", "42756305177", "1",
                    "p2p-endpoint-42756305177-1", Path(directory) / "secret.key",
                )
            self.assertFalse(path.exists())

    def test_download_refuses_invalid_or_non_tailnet_endpoint_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "p2p-endpoint.json"
            base = {"tailscale_ip": "100.64.1.9", "port": 4001,
                    "peer_id": "12D3KooWValidP2PIdentity1"}
            for bad in (
                dict(base, tailscale_ip="127.0.0.1"),
                dict(base, tailscale_ip="100.64.1.9\\nwrong"),
                dict(base, port=True),
                dict(base, peer_id="not a valid peer id"),
                dict(base, peer_id=""),
            ):
                path.write_text(json.dumps(bad))
                self.assertFalse(
                    coordination._valid_coordinated_json("p2p-endpoint.json", path)
                )
            path.write_text(json.dumps(base))
            self.assertTrue(
                coordination._valid_coordinated_json("p2p-endpoint.json", path)
            )

    def test_download_timeout_does_not_release_host_on_404(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "p2p-endpoint.json"
            result = subprocess.CompletedProcess([], 1, "", "BlobNotFound")
            with patch.object(coordination.subprocess, "run", return_value=result), \
                 patch.object(coordination.time, "monotonic", side_effect=[0, .2, 1.1]):
                with self.assertRaises(TimeoutError):
                    coordination.download_same_run_artifact(
                        "owner/repo", "42756305177", "1",
                        "p2p-endpoint-42756305177-1", destination, timeout=1,
                    )
            self.assertFalse(destination.exists())

    def test_pin_public_host_key_from_endpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            public = base / "host.pub"
            public.write_text(PUBLIC_KEY + " runner@host\n")
            endpoint = base / "host.json"
            coordination.write_endpoint(endpoint, "100.64.0.2", public)
            known = base / "known_hosts"
            coordination.write_known_hosts(known, [endpoint])
            self.assertEqual(known.read_text(), f"100.64.0.2 {PUBLIC_KEY}\n")

    def test_endpoint_rejects_key_or_hostname_injection(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            endpoint = base / "host.json"
            endpoint.write_text(json.dumps({"ip": "100.64.0.2\nattacker", "ssh_port": 22,
                                            "ssh_host_key": PUBLIC_KEY}))
            with self.assertRaises(ValueError):
                coordination.write_known_hosts(base / "known_hosts", [endpoint])
        for key in (PUBLIC_KEY + "\n" + PUBLIC_KEY, "ssh-rsa AAAA", "ssh-ed25519 AAAA"):
            with self.assertRaises(ValueError):
                coordination.public_host_key(key)


if __name__ == "__main__":
    unittest.main()
