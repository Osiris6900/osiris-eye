#!/usr/bin/env python3
"""Operator-run public TCP reachability probe; never claims authenticated P2P success.

Run on a separately administered network, not on the node's own host:
  python scripts/probe_p2p_public_tcp.py --host node.example.org --port 4001

Only checks TCP reachability. Does not dial libp2p, verify a PeerId, establish
Noise/E2EE, or demonstrate relay fallback. No private keys are needed.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import socket
import sys
from datetime import datetime, timezone


def validate_target(host: str, port: int) -> None:
    if not host or len(host) > 253 or any(c.isspace() for c in host):
        raise ValueError("host must be a nonempty hostname or IP without whitespace")
    if not 1 <= port <= 65535:
        raise ValueError("port must be between 1 and 65535")
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        if any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-" for c in host):
            raise ValueError("host must be a DNS name or IP address") from None
        if any(not part or len(part) > 63 or part[0] == "-" or part[-1] == "-" for part in host.split(".")):
            raise ValueError("invalid DNS hostname")
        return
    if not ip.is_global:
        raise ValueError("target must be a globally routable IP; no loopback/private/documentation addresses")


def probe(host: str, port: int, timeout: float) -> dict:
    validate_target(host, port)
    if not 0.1 <= timeout <= 30:
        raise ValueError("timeout must be between 0.1 and 30 seconds")
    result = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "target_host": host,
        "target_port": port,
        "tcp_reachable": False,
        "verification_level": "tcp_only",
        "authenticated_p2p_verified": False,
        "e2ee_verified": False,
        "relay_fallback_verified": False,
    }
    try:
        # getaddrinfo may return multiple A/AAAA records. Test each address
        # without leaking credentials or requiring a libp2p handshake.
        candidates = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        for family, kind, proto, _, sockaddr in candidates:
            ip = ipaddress.ip_address(sockaddr[0])
            if not ip.is_global:
                continue
            try:
                with socket.socket(family, kind, proto) as sock:
                    sock.settimeout(timeout)
                    sock.connect(sockaddr)
                result["tcp_reachable"] = True
                break
            except OSError:
                continue
    except OSError:
        pass
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True)
    parser.add_argument("--vantage-id", help="operator-declared identifier for the probing network")
    parser.add_argument("--port", type=int, default=4001)
    parser.add_argument("--timeout", type=float, default=3)
    args = parser.parse_args(argv)
    try:
        result = probe(args.host, args.port, args.timeout)
    except ValueError as exc:
        parser.error(str(exc))
    if args.vantage_id is not None:
        if not 1 <= len(args.vantage_id) <= 64 or not all(ch.isalnum() or ch in "-_" for ch in args.vantage_id):
            parser.error("vantage-id must be 1..64 alphanumeric, dash or underscore characters")
        result["vantage_id"] = args.vantage_id
    print(json.dumps(result, sort_keys=True))
    return 0 if result["tcp_reachable"] else 1


if __name__ == "__main__":
    sys.exit(main())
