#!/usr/bin/env python3
"""Fail-closed audit of the no-single-bootstrap operating configuration.

This is configuration validation, NOT a claim of Internet connectivity,
NAT traversal, decentralized peer discovery, or successful E2EE.
"""
import argparse
import json
from pathlib import Path


def validate(config):
    if not isinstance(config, dict):
        raise ValueError("configuration must be an object")
    peers = config.get("bootstrap_peers")
    if not isinstance(peers, list) or len(peers) < 2:
        raise ValueError("at least two independently operated bootstrap peers required")
    seen_ids, seen_operators = set(), set()
    for entry in peers:
        if not isinstance(entry, dict):
            raise ValueError("each bootstrap peer must be an object")
        address, operator = entry.get("multiaddr"), entry.get("operator")
        if not isinstance(address, str) or not address.startswith(("/ip4/", "/ip6/", "/dns4/", "/dns6/")):
            raise ValueError("bootstrap requires explicit network multiaddr")
        if "/p2p/" not in address or not address.rsplit("/p2p/", 1)[-1]:
            raise ValueError("bootstrap must end with peer identity")
        if not isinstance(operator, str) or not operator.strip():
            raise ValueError("bootstrap operator must be declared")
        peer_id = address.rsplit("/p2p/", 1)[-1]
        if "/" in peer_id or peer_id in seen_ids:
            raise ValueError("invalid or repeated peer identity")
        seen_ids.add(peer_id)
        seen_operators.add(operator.strip())
    if len(seen_operators) < 2:
        raise ValueError("bootstrap peers must have distinct declared operators")
    if config.get("central_authority_required") is not False:
        raise ValueError("central_authority_required must be false")
    return {"status": "configuration_pass", "bootstrap_peers": len(peers),
            "distinct_declared_operators": len(seen_operators),
            "verified_network_independence": False,
            "verified_public_reachability": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(validate(json.loads(args.config.read_text()))))
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        parser.exit(1, f"BLOCKED: {exc}\n")


if __name__ == "__main__":
    main()
