# P2P Network Decision Audit — Phase 1

This offline module creates a deterministic SHA-256 hash chain for Network Intelligence decisions (`observe`, `investigate`, `route_advisory`, `block_advisory`). Each entry records decision ID, peer ID, observed time, evidence digest, previous entry digest and sequence index.

A verifier detects record edits, broken links, duplicate decision IDs, and truncation **only when the expected tip hash is stored independently**. Without an independently retained trusted tip, an attacker can rewrite and rehash the whole chain. No signatures, trusted timestamps, external attestation, write-once storage or actual network actions are provided.

The module does not modify routes, authenticate peers, collect metrics, place orders, or authorize live trading.

Run: `python scripts/p2p_decision_audit_chain.py chain.json --expected-tip <independently-recorded-sha256>`.

Next: connect real Rust metrics and verified evidence digests, durable append-only storage with crash-safe writes, external tip anchoring, signing and independent cross-host validation. This is a prototype, not a compliance-certified audit log.
