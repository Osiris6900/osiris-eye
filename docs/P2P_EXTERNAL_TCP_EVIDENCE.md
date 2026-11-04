# External-network TCP evidence gate (operator run)

This is the next verification step after PR #96. **No public server is provisioned by this change.** A TCP connection alone does not establish authenticated libp2p, Noise, E2EE, message delivery, or relay fallback.

1. Operator provisions/approves a real public bootstrap or relay TCP endpoint and verifies its firewall/allowlist. Do not use example/documentation IP addresses.
2. On two independently administered networks (different providers/egress), run the same commit's probe with distinct declared vantage IDs:

```bash
python scripts/probe_p2p_public_tcp.py --host YOUR_REAL_DNS --port 4001 --vantage-id network-a > network-a.json
python scripts/probe_p2p_public_tcp.py --host YOUR_REAL_DNS --port 4001 --vantage-id network-b > network-b.json
```

3. Transfer only these sanitized JSON files to a trusted operator workstation and run:

```bash
python scripts/check_p2p_public_evidence.py --target-host YOUR_REAL_DNS --target-port 4001 network-a.json network-b.json
```

The gate rejects stale (>1 hour), future, unreachable, duplicate-vantage, mismatched-target and inflated E2EE claims. A passing result only means two **operator-declared** vantage records report public TCP success. Network independence is **not attested** by this script; operators must independently inspect their hosts and egress.

Next release gates: same-commit cross-host authenticated PeerId/Noise connection, exact E2EE recipient payload digest, forced relay fallback with direct path blocked, relay quota/failure recovery, measured outage/load, and security review. Keep raw private keys, broker credentials and account data out of evidence.
