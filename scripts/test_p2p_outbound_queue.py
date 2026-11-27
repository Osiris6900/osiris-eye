#!/usr/bin/env python3
"""Exercise definitely-unsent queue recovery with real, disposable node processes.

This does not retry an accepted GossipSub publication, certify remote durability,
or satisfy the independent-host loss/resource release gates.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "receipt_harness", ROOT / "scripts/test_p2p_delivery_receipts.py"
)
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)


class QueueNodes(harness.Nodes):
    def start(self, name, *, identity_name=None, bootstrap=None, recipients_file=None,
              queue_path=None, trusted=None, listen=None, max_age="300"):
        environment = harness.node_environment(
            self.directory, identity_name or name, receipts="1" if queue_path else None
        )
        if bootstrap:
            environment["OSIRIS_BOOTSTRAP_PEERS"] = bootstrap
        if recipients_file:
            environment["OSIRIS_E2EE_RECIPIENTS_FILE"] = str(recipients_file)
        if queue_path:
            environment["OSIRIS_P2P_OUTBOUND_DB"] = str(queue_path)
        environment["OSIRIS_MAX_OBSERVATION_AGE_SECS"] = max_age
        if trusted:
            environment["OSIRIS_TRUSTED_PEERS"] = trusted
        if listen:
            environment["OSIRIS_LISTEN_ADDR"] = listen
        process = subprocess.Popen(
            [str(self.binary)], cwd=ROOT, env=environment,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, bufsize=1,
        )
        self.processes[name] = process
        pump = threading.Thread(target=self._pump, args=(name, process.stdout), daemon=True)
        self.pumps.append(pump)
        pump.start()
        return process

    def stop(self, name, *, crash=False):
        process = self.processes[name]
        # The blocking stdin reader must observe EOF before Tokio can finish
        # shutdown and join that thread.
        process.stdin.close()
        if crash:
            process.kill()
        else:
            process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        self.processes.pop(name)
        # The pipe reader observes EOF before the next generation is started.
        for pump in self.pumps:
            pump.join(timeout=0.1)
        process.stdout.close()


def refused_configuration(binary, directory, queue_path):
    for name in ("missing-freshness", "plaintext", "missing-allowlist"):
        environment = harness.node_environment(directory, name, receipts=None)
        environment["OSIRIS_P2P_OUTBOUND_DB"] = str(queue_path)
        if name == "missing-freshness":
            environment.pop("OSIRIS_MAX_OBSERVATION_AGE_SECS")
        elif name == "plaintext":
            environment["OSIRIS_E2EE_ENABLED"] = "0"
        result = subprocess.run(
            [str(binary)], cwd=ROOT, env=environment,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, timeout=10, check=False,
        )
        assert result.returncode != 0, f"unsafe outbound configuration accepted: {name}"
        assert "OSIRIS_P2P_OUTBOUND_DB" in result.stdout, result.stdout
        assert "listener_ready " not in result.stdout, result.stdout


def run(binary):
    with tempfile.TemporaryDirectory(prefix="osiris-unsent-queue-") as temporary:
        directory = Path(temporary)
        queue_directory = directory / "outbound"
        queue_directory.mkdir(mode=0o700)
        queue_path = queue_directory / "pending.sqlite3"
        refused_configuration(binary, directory, queue_path)
        assert not queue_path.exists(), "invalid configuration created queue state"
        nodes = QueueNodes(binary, directory)
        try:
            nodes.start("recipient")
            listener = nodes.wait_for(
                lambda name, line: name == "recipient" and line.startswith("listener_ready "),
                "initial recipient listener", timeout=15,
            )
            address = harness.fields(listener)["address"]
            recipient = address.rsplit("/p2p/", 1)[1]
            listen = address.rsplit("/p2p/", 1)[0]
            recipients = directory / "recipients.txt"
            recipients.write_text(recipient + "\n")
            sender = nodes.start(
                "sender", bootstrap=address, recipients_file=recipients,
                queue_path=queue_path, trusted=recipient,
            )
            sender_peer = harness.fields(nodes.wait_for(
                lambda name, line: name == "sender" and line.startswith("peer_id="),
                "sender identity", timeout=15,
            ))["peer_id"]
            nodes.wait_for(
                lambda name, line: name == "sender"
                and line.startswith(f"key_binding_accepted peer={recipient} "),
                "durable recipient binding", timeout=30,
            )
            # The outage starts only after both endpoints have persisted their
            # authenticated bindings. Prepublication storage cannot establish
            # a remote peer's unknown key state or retry an accepted send.
            nodes.wait_for(
                lambda name, line: name == "recipient"
                and line.startswith(f"key_binding_accepted peer={sender_peer} "),
                "recipient durably accepted sender binding", timeout=30,
            )
            nodes.stop("recipient")
            nodes.wait_for(
                lambda name, line: name == "sender" and line.startswith(f"peer_disconnected={recipient} ")
                and harness.fields(line).get("remaining") == "0",
                "recipient disconnect before publication", timeout=15,
            )
            value = harness.observation("P2P-UNSENT-RECOVERY", "single disposable outage observation")
            harness.publish(sender, value)
            nodes.wait_for(
                lambda name, line: name == "sender" and line.startswith("outbound_observation_deferred ")
                and harness.fields(line).get("reason") == "no_subscribed_peers",
                "durable definitely-unsent deferral", timeout=15,
            )
            nodes.stop("sender", crash=True)
            nodes.start(
                "sender-resumed", identity_name="sender", bootstrap=address,
                recipients_file=recipients, queue_path=queue_path, trusted=recipient,
            )
            nodes.wait_for(
                lambda name, line: name == "sender-resumed"
                and line.startswith("outbound_queue_enabled=true ")
                and harness.fields(line).get("queued") == "1"
                and harness.fields(line).get("held") == "0",
                "queued observation surviving sender SIGKILL", timeout=15,
            )
            nodes.start("recipient-resumed", identity_name="recipient", listen=listen)
            nodes.wait_for(
                lambda name, line: name == "recipient-resumed"
                and line.startswith("encrypted_observation_received ")
                and harness.fields(line).get("payload_sha256") == value["payload_sha256"],
                "exact queued observation delivered after recovery", timeout=45,
            )
            nodes.wait_for(
                lambda name, line: name == "sender-resumed"
                and line.startswith(f"encrypted_observation_published recipient={recipient} "),
                "one accepted publication", timeout=10,
            )
            nodes.stop("sender-resumed")
            nodes.start(
                "sender-final", identity_name="sender", bootstrap=address,
                recipients_file=recipients, queue_path=queue_path, trusted=recipient,
            )
            nodes.wait_for(
                lambda name, line: name == "sender-final"
                and line.startswith("outbound_queue_enabled=true ")
                and harness.fields(line).get("queued") == "0"
                and harness.fields(line).get("held") == "0",
                "accepted publication absent from restarted queue", timeout=15,
            )
            nodes.capture_for(3)
            deliveries = [line for name, line in nodes.history
                          if name == "recipient-resumed" and line.startswith("encrypted_observation_received ")
                          and harness.fields(line).get("payload_sha256") == value["payload_sha256"]]
            publications = [line for name, line in nodes.history
                            if line.startswith(f"encrypted_observation_published recipient={recipient} ")]
            assert len(deliveries) == len(publications) == 1, (len(deliveries), len(publications))
            return {
                "passed": True, "scope": "local-definitely-unsent-prepublication-recovery",
                "sender_sigkill_recovery": True, "recipient_restart": True,
                "accepted_publications": 1, "validated_deliveries": 1,
                "accepted_send_not_reissued_after_restart": True,
                "recipient_receipt_enabled": False,
                "post_publication_delivery_guaranteed": False,
                "independent_host_acceptance": False, "live_execution": False,
            }
        finally:
            nodes.close()


def rejected_pending_work(binary):
    """Current policy and freshness still apply after the durable enqueue."""
    with tempfile.TemporaryDirectory(prefix="osiris-unsent-policy-") as temporary:
        directory = Path(temporary)
        queue_directory = directory / "outbound"
        queue_directory.mkdir(mode=0o700)
        queue_path = queue_directory / "pending.sqlite3"
        nodes = QueueNodes(binary, directory)
        try:
            nodes.start("recipient")
            listener = nodes.wait_for(
                lambda name, line: name == "recipient" and line.startswith("listener_ready "),
                "policy-test recipient listener", timeout=15,
            )
            address = harness.fields(listener)["address"]
            recipient = address.rsplit("/p2p/", 1)[1]
            nodes.stop("recipient")
            recipients = directory / "recipients.txt"
            recipients.write_text(recipient + "\n")
            sender = nodes.start(
                "sender", bootstrap=address, recipients_file=recipients,
                queue_path=queue_path, trusted=recipient, max_age="3",
            )
            own_peer = harness.fields(nodes.wait_for(
                lambda name, line: name == "sender" and line.startswith("peer_id="),
                "policy-test sender identity", timeout=15,
            ))["peer_id"]
            stale = harness.observation("P2P-UNSENT-STALE", "must expire before any publication")
            harness.publish(sender, stale)
            nodes.wait_for(
                lambda name, line: name == "sender" and line.startswith("outbound_observation_rejected ")
                and harness.fields(line).get("reason") == "stale_observation",
                "queued data becoming stale without publication", timeout=15,
            )
            nodes.history.clear()
            removed = harness.observation("P2P-UNSENT-REMOVED", "removed recipient must not receive this")
            harness.publish(sender, removed)
            nodes.wait_for(
                lambda name, line: name == "sender" and line.startswith("outbound_observation_queued "),
                "durable enqueue before recipient removal", timeout=10,
            )
            recipients.write_text(own_peer + "\n")
            nodes.wait_for(
                lambda name, line: name == "sender" and line.startswith("outbound_observation_rejected ")
                and harness.fields(line).get("reason") == "recipient_removed",
                "queued recipient policy recheck", timeout=10,
            )
            recipients.unlink()
            os.mkfifo(recipients, 0o600)
            harness.publish(sender, harness.observation("P2P-FIFO-REFUSAL", "regular configuration required"))
            nodes.wait_for(
                lambda name, line: name == "sender" and line.startswith("publish_rejected ")
                and harness.fields(line).get("reason") == "recipient_file_read_error",
                "nonblocking refusal of FIFO configuration", timeout=5,
            )
            recipients.unlink()
            recipients.write_text(recipient + "\n")
            nodes.stop("sender")
            nodes.start(
                "sender-final", identity_name="sender", bootstrap=address,
                recipients_file=recipients, queue_path=queue_path, trusted=recipient,
            )
            nodes.wait_for(
                lambda name, line: name == "sender-final"
                and line.startswith("outbound_queue_enabled=true ")
                and harness.fields(line).get("queued") == "0",
                "rejected work absent after restart", timeout=15,
            )
            assert not any(line.startswith("encrypted_observation_published ") for _, line in nodes.history)
        finally:
            nodes.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path)
    arguments = parser.parse_args()
    binary = arguments.binary.resolve() if arguments.binary else harness.find_binary()
    report = run(binary)
    rejected_pending_work(binary)
    report.update(stale_work_suppressed=True, removed_recipient_suppressed=True,
                  fifo_configuration_refused_without_blocking=True)
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
