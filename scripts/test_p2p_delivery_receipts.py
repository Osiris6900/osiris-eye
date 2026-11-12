#!/usr/bin/env python3
"""Verify E2EE admission receipts across three isolated local node processes.

This checks a recipient's network-admission attestation, not durable research
ingestion or application processing. The opt-out probe observes a bounded quiet
window; it does not claim to exercise the 120-second pending-receipt timeout.
"""
import argparse
import hashlib
import json
import os
import queue
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import build_opener, ProxyHandler

ROOT = Path(__file__).resolve().parents[1]
RECEIPT_TOPIC = "osiris/e2ee/receipts/1"


def find_binary():
    configured = os.getenv("OSIRIS_P2P_TEST_BINARY")
    if configured:
        path = Path(configured).resolve(strict=True)
        if not path.is_file():
            raise ValueError("test binary must be a regular file")
        return path
    for path in (
        ROOT / "services" / "p2p-node" / "target" / "release" / "osiris-p2p-node",
        ROOT / "target" / "release" / "osiris-p2p-node",
    ):
        if path.is_file():
            return path
    raise FileNotFoundError("P2P binary not found; build it before running this test")


def node_environment(directory, name, *, receipts="1", e2ee="1"):
    # Keep proxy/authentication runtime variables; isolate inherited node config.
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith("OSIRIS_")
    }
    environment.update({
        "OSIRIS_NODE_KEY": str(directory / f"{name}.identity"),
        "OSIRIS_TRANSPORT_KEY": str(directory / f"{name}.transport"),
        "OSIRIS_REPLAY_CACHE": str(directory / f"{name}.replay"),
        "OSIRIS_PEER_KEY_STORE": str(directory / f"{name}.peer-keys.sqlite3"),
        "OSIRIS_PEER_CACHE": str(directory / f"{name}.peer-cache.sqlite3"),
        "OSIRIS_PEER_CACHE_DIAL_ON_START": "0",
        "OSIRIS_HEALTH_HTTP_ADDR": "127.0.0.1:0",
        "OSIRIS_HEALTH_INTERVAL_SECS": "1",
        "OSIRIS_LISTEN_ADDR": "/ip4/127.0.0.1/tcp/0",
        "OSIRIS_BOOTSTRAP_SEED_URL": "off",
        "OSIRIS_MDNS_ENABLED": "0",
        "OSIRIS_UPNP_ENABLED": "0",
        "OSIRIS_HOLE_PUNCH_ENABLED": "0",
        "OSIRIS_AUTO_RELAY": "0",
        "OSIRIS_RELAY_SERVER": "0",
        "OSIRIS_MAX_OBSERVATION_AGE_SECS": "300",
        "OSIRIS_E2EE_ENABLED": e2ee,
    })
    if receipts is not None:
        environment["OSIRIS_DELIVERY_RECEIPTS"] = receipts
    return environment


def fields(line):
    return dict(token.split("=", 1) for token in line.split() if "=" in token)


class Nodes:
    def __init__(self, binary, directory):
        self.binary = binary
        self.directory = directory
        self.events = queue.Queue()
        self.processes = {}
        self.history = []
        self.pumps = []

    def start(self, name, *, bootstrap=None, recipients_file=None, receipts="1", receipt_bridge=None):
        environment = node_environment(self.directory, name, receipts=receipts)
        if bootstrap is not None:
            environment["OSIRIS_BOOTSTRAP_PEERS"] = bootstrap
        if recipients_file is not None:
            environment["OSIRIS_E2EE_RECIPIENTS_FILE"] = str(recipients_file)
        if receipt_bridge is not None:
            environment.update({
                "OSIRIS_VERIFIED_RECEIPT_SOCKET": str(receipt_bridge["socket"]),
                "OSIRIS_VERIFIED_RECEIPT_SPOOL": str(receipt_bridge["spool"]),
                "OSIRIS_VERIFIED_RECEIPT_LISTENER_PID": str(receipt_bridge["listener_pid"]),
            })
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

    def _pump(self, name, stream):
        for line in iter(stream.readline, ""):
            self.events.put((name, line.rstrip()))
        self.events.put((name, "<stdout-closed>"))

    def _receive(self, timeout):
        try:
            item = self.events.get(timeout=timeout)
        except queue.Empty:
            return None
        self.history.append(item)
        print(f"[{item[0]}] {item[1]}", flush=True)
        exited = [name for name, process in self.processes.items() if process.poll() is not None]
        if exited:
            raise RuntimeError(f"nodes exited before acceptance completed: {exited}")
        return item

    def wait_for(self, predicate, description, timeout=30):
        for name, line in self.history:
            if predicate(name, line):
                return line
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            item = self._receive(min(0.5, max(0.01, deadline - time.monotonic())))
            if item is not None and predicate(*item):
                return item[1]
            if any(process.poll() is not None for process in self.processes.values()):
                raise RuntimeError(f"node exited while waiting for {description}")
        recent = self.history[-30:]
        raise TimeoutError(f"timed out waiting for {description}; recent output: {recent}")

    def capture_for(self, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self._receive(min(0.25, max(0.01, deadline - time.monotonic())))

    def close(self):
        for process in self.processes.values():
            if process.poll() is None:
                process.terminate()
        for process in self.processes.values():
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            if process.stdin is not None:
                try:
                    process.stdin.close()
                except (BrokenPipeError, OSError):
                    pass
        for pump in self.pumps:
            pump.join(timeout=2)
        for process in self.processes.values():
            if process.stdout is not None:
                process.stdout.close()


def check_refused_configuration(binary, directory):
    for name, receipts, e2ee in (
        ("invalid-flag", "enabled", "1"),
        ("plaintext-receipts", "1", "0"),
    ):
        result = subprocess.run(
            [str(binary)], cwd=ROOT,
            env=node_environment(directory, name, receipts=receipts, e2ee=e2ee),
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, timeout=10, check=False,
        )
        assert result.returncode != 0, f"unsafe configuration accepted: {name}"
        assert "OSIRIS_DELIVERY_RECEIPTS" in result.stdout, result.stdout
        assert "OSIRIS EYE P2P node starting" not in result.stdout, result.stdout


def observation(instrument, payload):
    return {
        "schema": "osiris.observation.v1",
        "instrument": instrument,
        "observed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "provider": "synthetic-delivery-receipts",
        "payload": payload,
        "payload_sha256": hashlib.sha256(payload.encode()).hexdigest(),
    }


def publish(process, value):
    process.stdin.write(json.dumps(value, separators=(",", ":")) + "\n")
    process.stdin.flush()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--binary", type=Path,
        help="node executable to test; defaults to the repository release binary",
    )
    arguments = parser.parse_args()
    binary = arguments.binary.resolve() if arguments.binary is not None else find_binary()
    if not binary.is_file():
        parser.error(f"node binary does not exist: {binary}")
    with tempfile.TemporaryDirectory(prefix="osiris-delivery-receipts-") as temporary:
        directory = Path(temporary)
        check_refused_configuration(binary, directory)
        nodes = Nodes(binary, directory)
        try:
            recipients = directory / "recipients.txt"
            recipients.write_text("", encoding="utf-8")
            sender = nodes.start("node-a", recipients_file=recipients)
            listener = nodes.wait_for(
                lambda name, line: name == "node-a" and line.startswith("listener_ready "),
                "sender loopback listener", timeout=15,
            )
            address = fields(listener)["address"]
            sender_peer = address.rsplit("/p2p/", 1)[1]
            nodes.start("node-b", bootstrap=address)
            # Default opt-out is also tested: C can decrypt when addressed but
            # must not issue admission receipts without explicit enablement.
            nodes.start("node-c", bootstrap=address, receipts=None)
            peers = {"node-a": sender_peer}
            for node in ("node-b", "node-c"):
                line = nodes.wait_for(
                    lambda name, line, node=node: name == node and line.startswith("peer_id="),
                    f"{node} identity", timeout=15,
                )
                peers[node] = fields(line)["peer_id"]

            for node, enabled in (("node-a", "true"), ("node-b", "true"), ("node-c", "false")):
                nodes.wait_for(
                    lambda name, line, node=node, enabled=enabled: name == node
                    and line.startswith(f"delivery_receipts_enabled={enabled} ")
                    and fields(line).get("receipt_topic") == RECEIPT_TOPIC,
                    f"{node} receipt opt-in state",
                )
            for observer, publisher in (
                ("node-a", "node-b"), ("node-b", "node-a"),
                ("node-a", "node-c"), ("node-c", "node-a"),
            ):
                nodes.wait_for(
                    lambda name, line, observer=observer, publisher=publisher: name == observer
                    and line.startswith(f"key_binding_accepted peer={peers[publisher]} "),
                    f"{observer} verified key binding for {publisher}",
                )
            # Subscription and signed binding announcements use separate topics.
            # A bounded settling interval avoids publishing ahead of topic joins.
            nodes.capture_for(2)

            recipients.write_text(peers["node-b"] + "\n", encoding="utf-8")
            first = observation("P2P-RECEIPT-TEST", "recipient-only admission receipt probe")
            publish(sender, first)
            published = fields(nodes.wait_for(
                lambda name, line: name == "node-a"
                and line.startswith(f"encrypted_observation_published recipient={peers['node-b']} "),
                "tracked encrypted publication to B",
            ))
            assert published["receipts_tracked"] == "true", published
            message_id, digest = published["message_id"], published["envelope_sha256"]
            assert len(digest) == 64 and all(character in "0123456789abcdef" for character in digest)
            nodes.wait_for(
                lambda name, line: name == "node-b" and line.startswith("encrypted_observation_received ")
                and fields(line).get("payload_sha256") == first["payload_sha256"]
                and fields(line).get("instrument") == first["instrument"],
                "B's validated decryption with the exact synthetic payload digest",
            )
            receipt = fields(nodes.wait_for(
                lambda name, line: name == "node-b" and line.startswith("delivery_receipt_published ")
                and fields(line).get("message_id") == message_id,
                "B's signed admission receipt",
            ))
            assert receipt["sender"] == sender_peer, receipt
            assert receipt["recipient"] == peers["node-b"], receipt
            assert receipt["envelope_sha256"] == digest, receipt
            acknowledged = fields(nodes.wait_for(
                lambda name, line: name == "node-a" and line.startswith("observation_delivery_acknowledged ")
                and fields(line).get("message_id") == message_id,
                "A's authenticated matching admission acknowledgement",
            ))
            assert acknowledged["recipient"] == peers["node-b"], acknowledged
            assert acknowledged["envelope_sha256"] == digest, acknowledged
            assert acknowledged["admission"] == "validated_decrypted_replay_recorded", acknowledged
            address_line = nodes.wait_for(lambda name, line: name == "node-a" and line.startswith("health_http_listening="), "sender health listener")
            metrics_url = "http://" + address_line.split("=", 1)[1] + "/peer-metrics"
            opener = build_opener(ProxyHandler({}))
            deadline = time.monotonic() + 10
            measured = None
            while time.monotonic() < deadline:
                with opener.open(metrics_url, timeout=2) as response:
                    metrics = json.loads(response.read(262144))
                measured = next((peer for peer in metrics["peers"] if peer["peer_id"] == peers["node-b"]), None)
                if measured and measured["cryptographically_verified_receipts"] >= 1:
                    break
                time.sleep(.1)
            assert measured and measured["cryptographically_verified_receipts"] >= 1, measured
            assert measured["published_messages"] >= 1, measured
            assert measured["latency_history"][-1]["receipt_latency_ms"] >= 0, measured
            assert metrics["independent_hosts_verified"] is False, metrics
            print("P2P_VERIFIED_RECEIPT_PEER_METRICS=PASS", flush=True)
            nodes.wait_for(
                lambda name, line: name == "node-c" and line.startswith(
                    "encrypted_message_discarded reason=not_for_this_recipient_or_sender_mismatch"
                ),
                "excluded C's recipient gate",
            )
            for name, line in nodes.history:
                if name == "node-c":
                    assert first["instrument"] not in line, line
                    assert first["payload"] not in line, line
                    assert first["payload_sha256"] not in line, line
                    assert not line.startswith("delivery_receipt_published "), line

            recipients.write_text(peers["node-c"] + "\n", encoding="utf-8")
            second = observation("P2P-RECEIPT-OPTOUT", "default receiver opt-out probe")
            publish(sender, second)
            unconfirmed = fields(nodes.wait_for(
                lambda name, line: name == "node-a"
                and line.startswith(f"encrypted_observation_published recipient={peers['node-c']} "),
                "tracked encrypted publication to opt-out C",
            ))
            assert unconfirmed["receipts_tracked"] == "true", unconfirmed
            nodes.wait_for(
                lambda name, line: name == "node-c" and line.startswith("encrypted_observation_received ")
                and fields(line).get("payload_sha256") == second["payload_sha256"],
                "opt-out C's valid decryption",
            )
            nodes.capture_for(3)
            assert not any(
                name == "node-a" and line.startswith("observation_delivery_acknowledged ")
                and fields(line).get("message_id") == unconfirmed["message_id"]
                for name, line in nodes.history
            ), "sender reported recipient admission without an opted-in recipient receipt"
            assert not any(
                name == "node-c" and line.startswith("delivery_receipt_published ")
                for name, line in nodes.history
            ), "default opt-out recipient published a receipt"
            print(
                "PASS: exact recipient admission receipt verified; excluded peer saw no plaintext; "
                "opt-out delivery remained unconfirmed during the bounded probe.",
                flush=True,
            )
        finally:
            nodes.close()


if __name__ == "__main__":
    main()
