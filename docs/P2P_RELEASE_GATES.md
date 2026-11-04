# P2P market-data release gates

The first release covers encrypted, non-sensitive market observations for
research and paper trading. Received peer observations currently stop at
validation and reception; an automatic paper-simulator bridge and live execution
adapter are not implemented. Keep broker credentials, account state and orders
outside this observation protocol.

Quality is measured against the gates below. A “world best” designation has no
defined test and is not a release status. Source implementation, a local passing
test, a cross-host passing run and an independent audit are separate evidence.

## Evidence and remaining gates

The [baseline main CI run](https://github.com/Osiris6900/osiris-eye/actions/runs/41748810081)
at commit `48755ef3d1c7a9f3fd3cbbb309d4f052ec06010b` passed its existing local tests.
It does not verify the subsequent hardening changes. The current branch's
integrated results must be recorded against its final commit before promotion.

| Gate | Acceptance criterion | Current evidence boundary |
|---|---|---|
| Crypto and input validation | Unit/adversarial suites reject modified signatures, ciphertext, wrong recipients, low-order keys, malformed metadata and expired envelopes | Implemented suites; hardening branch requires integrated run |
| Core relay E2EE | Relay plus two core nodes exchange the exact recipient payload digest with mDNS disabled and both peer paths reported as relayed | Process harness implemented; retain passing result for final commit |
| Observation freshness | Configured receiver rejects malformed, stale and future event times and continues accepting a fresh encrypted observation | Timestamp policy and process harness implemented; integrated verification required |
| Replay and durable keys | Restart retains accepted message IDs and key-epoch floors; malformed, replaced or exhausted state fails closed | Tests implemented; final-commit results required; each state path supports one writer |
| Revocation and rotation | Signed revocation persists, disconnects a live peer and blocks reconnect/message admission; operator rotation preserves epoch rules across restart | Local tests and rotation runbook exist; operational recovery exercise still required |
| Two independent hosts | Same-commit binaries on separate machines establish authenticated connectivity and deliver the exact encrypted recipient observation | SSH harness/workflow implemented; passing final-commit cross-host E2EE evidence outstanding |
| Three independent hosts | A encrypts only for B; B reports exact decrypted digest; excluded C reports recipient-gated discard without plaintext identifiers | Harness/workflow implemented; passing run outstanding; primitive wrong-key tests cover decryption failure separately |
| Outage and load | Record recovery time, latency distribution, loss/rejection counts, RSS, CPU and disk growth for a declared peer/message workload, including relay/bootstrap outage | No final capacity claim or retained soak benchmark established; set numerical acceptance limits before running |
| Public operations | Operator-controlled endpoints pass the intended networks' connectivity, health, restart and monitoring checks | Configuration/profile exists; deployment, uptime and live peer count are not established |
| Feed provenance | Each admitted feed has an authorized source, permitted redistribution, availability timestamps and independently checked quality | Peer signatures authenticate publishers, not market truth or redistribution rights; feed admission work remains |
| Independent assurance | External reviewers assess protocol, implementation and operator lifecycle; findings are remediated and retested | Internal reviews/tests do not constitute an independent cryptographic audit |

Explicit relay configuration does not implement automatic relay selection,
AutoNAT or hole punching. E2EE does not hide routing metadata and has no ratchet
guarantee against later compromise of a recipient's static transport key.
Replay journals and registry snapshots require a single writer per state path;
multiple processes must use separate state. Do not clear replay history to make
a capacity failure disappear.

## Reproduce local validation

Run from the repository root with Rust, Python 3.11+, and API development
dependencies installed. Use a clean checkout of the exact candidate commit.

```bash
pip install -e 'services/quant-api[dev]'
pytest -q services/quant-api/tests
python scripts/test_p2p_workflow_coordination.py
python scripts/test_p2p_ssh_harness.py
python deploy/p2p/test_preflight.py
cargo test --manifest-path services/p2p-node/Cargo.toml --all-targets
cargo build --release --manifest-path services/p2p-node/Cargo.toml
python scripts/test_p2p_e2ee_three_process.py
python scripts/test_p2p_core_relay_e2ee.py --evidence /tmp/osiris-core-relay-e2ee.json
python scripts/test_p2p_observation_freshness.py
python scripts/test_p2p_bootstrap_restart.py
python scripts/test_p2p_global_revocation_sync.py
python scripts/test_p2p_live_swarm_revocation.py
```

Historical research mode leaves freshness unconfigured while still validating
UTC timestamp syntax. Set `OSIRIS_MAX_OBSERVATION_AGE_SECS` to `1..86400` for
live observation admission; the hardened receiver example uses `300` seconds
with a 30-second future tolerance. A fresh event timestamp is not proof of
provider freshness or correctness.

For independent-machine acceptance, follow the
[two-host E2EE procedure](P2P_CROSS_HOST_E2EE_ACCEPTANCE.md) and
[three-host gate](P2P_THREE_HOST_E2EE_ACCEPTANCE.md). Run the workflows against
the reviewed candidate revision with operator-configured network credentials.
Never substitute three local processes for three independent hosts.

Each retained acceptance record should contain commit SHA, clean/dirty state,
binary hashes, workflow URL and attempt, UTC times, topology, public PeerIds,
declared recipient set, exact synthetic payload digest and assertion results.
The core relay harness can emit a JSON record; capture the other tests' sanitized
logs and exit status with equivalent metadata. Retain failures as failures.
Exclude private keys, credentials and private state from all artifacts.

The [hardened receiver guide](P2P_HARDENED_DEPLOYMENT.md) defines operator setup
and profile limits. Public rollout remains gated on the independent-host,
operational, feed-admission and security evidence above.
