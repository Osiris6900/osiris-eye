# Crypto and signed receipt verification

Tracking issue: #177

## Implementation scope
Audit recipient binding, signature verification, revocation epochs, replay journal and receipt-ledger boundary; add malformed input and restart fault injection.

## Acceptance evidence
Negative tests, security evidence, reviewer signoff and no plaintext leakage.

## Current status

Fault-injection tests call the production E2EE, revocation-sync, receipt, and
replay functions. They reject key-binding mismatch, impersonated senders,
revoked-key use after a synced revocation, tampered/forged/wrong-signer
receipts, duplicate replay keys, and a nonce copied from another envelope.
They do not weaken fail-closed behavior.

Recipient-only multi-host evidence is still open for current `main`
`2036888bbada42213a221526914b09d50fc1f1df`. Older passing runs and the exact
missing binary hashes are listed in
[P2P_SECURITY_MODEL.md](../P2P_SECURITY_MODEL.md). The SSH harness now retains
those hashes and labels `local_or_same_host` separately from
`distinct_boot_id_hosts`. No new independent-host run was produced with this
change. An independent cryptographic audit has not been done.

No live trading is enabled. This note does not close #177 or #207.
