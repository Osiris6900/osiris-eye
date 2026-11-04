#!/usr/bin/env python3
"""Fail-closed configuration checks for the optional hardened systemd profile.

This validates profile invariants; the Rust node performs cryptographic and
multiaddr validation. No environment values or private keys are printed.
"""
from __future__ import annotations

import ipaddress
import os
from pathlib import PurePosixPath
import re
import sys
from collections.abc import Mapping

STATE = PurePosixPath("/var/lib/osiris-p2p")
PEER = re.compile(r"[1-9A-HJ-NP-Za-km-z]{32,128}\Z")
STATE_KEYS = (
    "OSIRIS_NODE_KEY", "OSIRIS_TRANSPORT_KEY", "OSIRIS_PEER_KEY_STORE",
    "OSIRIS_REPLAY_CACHE", "OSIRIS_REVOCATION_REGISTRY",
)


def peer_set(raw: str, name: str) -> set[str]:
    entries = raw.split(",")
    if not 1 <= len(entries) <= 128 or any(not PEER.fullmatch(p.strip()) for p in entries):
        raise ValueError(f"{name} must contain 1..128 explicit PeerIds")
    return {p.strip() for p in entries}


def check(env: Mapping[str, str]) -> None:
    if any("REPLACE_" in value for name, value in env.items() if name.startswith("OSIRIS_")):
        raise ValueError("replace example configuration placeholders")
    if env.get("OSIRIS_E2EE_ENABLED") != "1":
        raise ValueError("OSIRIS_E2EE_ENABLED must be 1")
    if env.get("OSIRIS_MDNS_ENABLED") != "0":
        raise ValueError("OSIRIS_MDNS_ENABLED must be 0 in this profile")
    if env.get("OSIRIS_ROTATE_NODE_KEY", "0") != "0":
        raise ValueError("perform identity rotation separately; do not rotate on service restart")
    trusted = peer_set(env.get("OSIRIS_TRUSTED_PEERS", ""), "OSIRIS_TRUSTED_PEERS")
    recipients = peer_set(env.get("OSIRIS_E2EE_RECIPIENTS", ""), "OSIRIS_E2EE_RECIPIENTS")
    if not recipients <= trusted:
        raise ValueError("all recipients must appear in OSIRIS_TRUSTED_PEERS")
    if env.get("OSIRIS_E2EE_RECIPIENTS_FILE"):
        raise ValueError("this profile uses an explicit recipient list, not a recipient file")
    revoked_raw = env.get("OSIRIS_REVOKED_PEERS", "")
    if revoked_raw and trusted & peer_set(revoked_raw, "OSIRIS_REVOKED_PEERS"):
        raise ValueError("remove revoked PeerIds from the trusted-peer configuration")
    for name in STATE_KEYS:
        raw = env.get(name, "")
        path = PurePosixPath(raw)
        if not path.is_absolute() or ".." in path.parts or path.parent != STATE:
            raise ValueError(f"{name} must be a file directly inside the private state directory")
    authority = env.get("OSIRIS_REVOCATION_AUTHORITY_PUBLIC_KEY", "")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", authority):
        raise ValueError("OSIRIS_REVOCATION_AUTHORITY_PUBLIC_KEY must be a 32-byte hex public key")
    for name, maximum in (("OSIRIS_TRANSPORT_KEY_EPOCH", 2**63 - 1),
                          ("OSIRIS_MAX_OBSERVATION_AGE_SECS", 86400)):
        raw = env.get(name, "")
        too_many_age_digits = name == "OSIRIS_MAX_OBSERVATION_AGE_SECS" and len(raw) > 5
        if too_many_age_digits or not raw.isascii() or not raw.isdecimal() or not 1 <= int(raw) <= maximum:
            raise ValueError(f"{name} must be an explicit positive bounded integer")
    try:
        host, port = env.get("OSIRIS_HEALTH_HTTP_ADDR", "").rsplit(":", 1)
        # Match Rust SocketAddr syntax: IPv6 hosts require square brackets;
        # IPv4 hosts must be unbracketed. Preserve valid expanded IPv6 forms.
        bracketed = host.startswith("[") and host.endswith("]")
        address = ipaddress.ip_address(host[1:-1] if bracketed else host)
        if bracketed != (address.version == 6):
            raise ValueError
        if not address.is_loopback or not 1 <= int(port) <= 65535:
            raise ValueError
    except ValueError:
        raise ValueError("OSIRIS_HEALTH_HTTP_ADDR must bind a loopback IP and nonzero port") from None
    bootstraps = env.get("OSIRIS_BOOTSTRAP_PEERS", "").split(",")
    relay = env.get("OSIRIS_RELAY_ADDRESS", "")
    for value in [*bootstraps, *([relay] if relay else [])]:
        address = value.strip()
        if not address.startswith("/") or "/p2p/" not in address:
            raise ValueError("configure explicit bootstrap/relay multiaddrs")
        peer = address.rsplit("/p2p/", 1)[1]
        if peer not in trusted:
            raise ValueError("each bootstrap/relay destination must be explicitly trusted")
        # A circuit path also authenticates its relay before the recipient.
        if "/p2p-circuit/" in address:
            relay_peer = address.split("/p2p-circuit/", 1)[0].rsplit("/p2p/", 1)[-1]
            if relay_peer not in trusted:
                raise ValueError("circuit relay PeerId must be explicitly trusted")


def main() -> int:
    try:
        check(os.environ)
    except ValueError as error:
        print(f"P2P profile preflight rejected: {error}", file=sys.stderr)
        return 1
    print("P2P profile preflight passed; cryptographic and address validation follows in the node")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
