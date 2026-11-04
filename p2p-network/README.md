# P2P Network — experimental prototype

This directory is an isolated proof of concept. It does **not** move real money, provide settlement, or implement a production blockchain.

## Run

Requires Python 3.11+ (standard library only).

Terminal A:
```bash
python p2p-network/node.py --port 8765
```

Terminal B:
```bash
python p2p-network/node.py --port 8766 --peer 127.0.0.1:8765
```

Type a message in either terminal and press Enter. Messages are relayed with IDs, TTL and duplicate suppression.

## Security limitations

This is an **unencrypted local development transport** with no peer authentication, transaction ledger, balance, consensus, or double-spend protection. Never send passwords, keys, personal data, or real payment instructions. Use only on trusted local development machines.

## Next milestones

1. Ed25519 node identities and signed message envelopes.
2. Authenticated encrypted transport and peer discovery.
3. Replay resistance, rate limits, protocol versioning, tests.
4. Separately design the value and settlement model before implementing payment features.
