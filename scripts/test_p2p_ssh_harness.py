#!/usr/bin/env python3
"""Regression tests for cross-host acceptance evidence and remote cleanup."""
import contextlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from test_p2p_cross_host_e2ee_ssh import EventLog, MAX_LOG_LINE_CHARS, pump, remote_command, wait_for


class Process:
    def __init__(self, returncode=None):
        self.returncode = returncode

    def poll(self):
        return self.returncode


class SSHHarnessTests(unittest.TestCase):
    def test_interleaved_events_remain_available_for_later_waits(self):
        events = EventLog()
        for source, line in (("C", "peer_id=c"), ("A", "key_binding_accepted peer=c "),
                             ("B", "peer_id=b"), ("A", "key_binding_accepted peer=b ")):
            events.record(source, line)
        processes = [("A", Process()), ("B", Process()), ("C", Process())]
        for source, expected in (("B", "peer_id=b"), ("A", "key_binding_accepted peer=b "),
                                 ("C", "peer_id=c"), ("A", "key_binding_accepted peer=c ")):
            self.assertEqual(wait_for(events, processes, lambda n, l: n == source and l == expected,
                                      .01, expected), expected)

    def test_dead_process_fails_even_when_matching_output_is_already_buffered(self):
        events = EventLog()
        events.record("A", "ready")
        with self.assertRaisesRegex(RuntimeError, "A exited with 7"):
            wait_for(events, [("A", Process(7))], lambda n, l: l == "ready", .1, "ready")

    def test_closed_output_fails_even_before_process_exit_is_observed(self):
        events = EventLog()
        events.record("A", "<stdout-closed>")
        with self.assertRaisesRegex(RuntimeError, "output closed"):
            wait_for(events, [("A", Process())], lambda n, l: False, .1, "ready")

    def test_timeout_is_bounded_and_reports_recent_sanitized_output(self):
        events = EventLog()
        events.record("A", "starting")
        with self.assertRaisesRegex(TimeoutError, "starting"):
            wait_for(events, [("A", Process())], lambda n, l: False, 0, "ready")

    def test_overflow_fails_closed_instead_of_discarding_required_evidence(self):
        events = EventLog(max_events=2)
        for line in ("first", "second", "third"):
            events.record("A", line)
        self.assertEqual(len(events.history), 2)
        with self.assertRaisesRegex(RuntimeError, "capacity exceeded"):
            wait_for(events, [], lambda n, l: True, .1, "anything")

    def test_observer_plaintext_is_rejected_without_copying_it_into_failure_logs(self):
        events = EventLog(forbidden_by_source={"C": ("secret-observation",)})
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            pump("C", io.StringIO("secret-observation\n"), events)
        self.assertNotIn("secret-observation", output.getvalue())
        with self.assertRaises(RuntimeError) as caught:
            wait_for(events, [], lambda n, l: True, .1, "discard")
        self.assertNotIn("secret-observation", str(caught.exception))

    def test_oversized_line_is_not_accepted_as_partial_readiness(self):
        events = EventLog()
        with contextlib.redirect_stdout(io.StringIO()):
            pump("A", io.StringIO("x" * (MAX_LOG_LINE_CHARS + 2) + "\n"), events)
        with self.assertRaisesRegex(RuntimeError, "maximum output line length"):
            wait_for(events, [], lambda n, l: True, .1, "ready")

    def test_private_key_material_and_secret_values_are_redacted_from_history_and_stdout(self):
        events = EventLog()
        output = io.StringIO()
        text = "-----BEGIN OPENSSH PRIVATE KEY-----\nPRIVATEBYTES\n-----END OPENSSH PRIVATE KEY-----\ntoken=TOKENBYTES\n"
        with contextlib.redirect_stdout(output):
            pump("A", io.StringIO(text), events)
        for secret in ("PRIVATEBYTES", "TOKENBYTES"):
            self.assertNotIn(secret, output.getvalue())
            self.assertNotIn(secret, repr(events.history))

    @unittest.skipUnless(os.name == "posix", "remote launcher targets POSIX SSH hosts")
    def test_remote_launcher_unsets_inherited_peer_policy_instead_of_exporting_empty(self):
        with tempfile.TemporaryDirectory(prefix="osiris-ssh-policy-") as directory:
            binary = Path(directory) / "node"
            binary.write_text(
                '#!/bin/sh\n'
                'test "${OSIRIS_TRUSTED_PEERS+x}" != x || exit 41\n'
                'test "${OSIRIS_TRUSTED_PEERS_FILE+x}" != x || exit 42\n'
                'test "${OSIRIS_REVOKED_PEERS+x}" != x || exit 43\n'
                'test "${OSIRIS_REVOKED_PEERS_FILE+x}" != x || exit 44\n'
                'test "$OSIRIS_E2EE_ENABLED" = 1 || exit 45\n'
            )
            binary.chmod(0o700)
            env = dict(os.environ, OSIRIS_TRUSTED_PEERS="operator-policy",
                       OSIRIS_TRUSTED_PEERS_FILE="operator-policy-file",
                       OSIRIS_REVOKED_PEERS="operator-revocations",
                       OSIRIS_REVOKED_PEERS_FILE="operator-revocation-file")
            result = subprocess.run(
                ["sh", "-c", remote_command(str(binary), "bootstrap")],
                input="", text=True, capture_output=True, timeout=10, env=env,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(os.name == "posix", "remote launcher targets POSIX SSH hosts")
    def test_remote_command_quotes_paths_preserves_exit_status_and_cleans_state(self):
        with tempfile.TemporaryDirectory(prefix="osiris-ssh-harness-") as directory:
            binary = Path(directory) / "node '$(echo BAD)'"
            binary.write_text('#!/bin/sh\nprintf "%s\\n" "$OSIRIS_NODE_KEY"\nexit 17\n')
            binary.chmod(0o700)
            result = subprocess.run(["sh", "-c", remote_command(str(binary), "/ip4/127.0.0.1/tcp/1")],
                                    input="", text=True, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 17, result.stderr)
            state_path = Path(result.stdout.strip())
            self.assertTrue(str(state_path).startswith("/tmp/osiris-xhost-e2ee."))
            self.assertFalse(state_path.parent.exists())
            self.assertNotIn("BAD", result.stdout)

    @unittest.skipUnless(os.name == "posix", "remote launcher targets POSIX SSH hosts")
    def test_remote_signal_cleanup_kills_a_child_that_ignores_term(self):
        with tempfile.TemporaryDirectory(prefix="osiris-ssh-signal-") as directory:
            binary = Path(directory) / "node"
            binary.write_text(f'#!{sys.executable}\nimport os, signal\n'
                              'signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
                              'print(os.environ["OSIRIS_NODE_KEY"], flush=True)\n'
                              'while True: signal.pause()\n')
            binary.chmod(0o700)
            proc = subprocess.Popen(["sh", "-c", remote_command(str(binary), "bootstrap")],
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True)
            try:
                state_path = Path(proc.stdout.readline().strip())
                self.assertTrue(str(state_path).startswith("/tmp/osiris-xhost-e2ee."))
                proc.terminate()
                proc.communicate(timeout=8)
                self.assertEqual(proc.returncode, 143)
                self.assertFalse(state_path.parent.exists())
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main()
