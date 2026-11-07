# P2P market-data release gates

The first release covers encrypted, non-sensitive market observations for
research and paper trading. Stored observations can drive a durable, idempotent
paper-intent workflow after current authorization, freshness, signal/backtest
and risk gates. This is not a fill-level paper simulator, portfolio reconciliation
or live execution adapter. Keep broker credentials, account state and orders
outside this observation protocol.

Quality is measured against the gates below. A “world best” designation has no
defined test and is not a release status. Source implementation, a local passing
test, a cross-host passing run and an independent audit are separate evidence.

## Evidence and remaining gates

The [main CI run](https://github.com/Osiris6900/osiris-eye/actions/runs/42423590055)
at commit `ce16dbde1e7b2377c82e5f14622238e60c04ff07` passed its Python, web and
Rust jobs on 2026-11-07. The Rust job also passed the core relay E2EE,
observation freshness, restart, revocation and signed delivery-receipt process
checks. This establishes integrated local acceptance for that commit only;
subsequent changes require their own results before promotion.

At `974de9ce04585e1d38824136514325f231b2bdaf`, the
[Tailscale two-host run](https://github.com/Osiris6900/osiris-eye/actions/runs/42421052643)
passed both receiver and sender jobs, and the
[three-peer mesh run](https://github.com/Osiris6900/osiris-eye/actions/runs/42421052595)
passed. The Tailscale workflow does not enable recipient E2EE, so it establishes
cross-host observation delivery, not cross-host recipient E2EE acceptance.
Neither result establishes independent three-host recipient exclusion,
public deployment, sustained load acceptance or an independent audit.

| Gate | Acceptance criterion | Current evidence boundary |
|---|---|---|
| Crypto and input validation | Unit/adversarial suites reject modified signatures, ciphertext, wrong recipients, low-order keys, malformed metadata and expired envelopes | Passed in integrated CI at `ce16dbde`; later candidates require rerun |
| Core relay E2EE | Relay plus two core nodes exchange the exact recipient payload digest with mDNS disabled and both peer paths reported as relayed | Passed in integrated CI at `ce16dbde`; core relay evidence upload also passed |
| Observation freshness | Configured receiver rejects malformed, stale and future event times and continues accepting a fresh encrypted observation | Passed in integrated CI at `ce16dbde`; later candidates require rerun |
| Replay and durable keys | Restart retains accepted message IDs and key-epoch floors; malformed, replaced or exhausted state fails closed | Local suites passed at `ce16dbde`; each state path supports one writer; later candidates require rerun |
| Revocation and rotation | Signed revocation persists, disconnects a live peer and blocks reconnect/message admission; operator rotation preserves epoch rules across restart | Local tests and rotation runbook exist; operational recovery exercise still required |
| Two independent hosts | Same-commit binaries on separate machines establish authenticated connectivity and deliver the exact encrypted recipient observation | SSH harness/workflow implemented; passing final-commit cross-host E2EE evidence outstanding |
| Three independent hosts | A encrypts only for B; B reports exact decrypted digest; excluded C reports recipient-gated discard without plaintext identifiers | Harness/workflow implemented; passing run outstanding; primitive wrong-key tests cover decryption failure separately |
| Outage and load | Record recovery time, latency distribution, loss/rejection counts, RSS, CPU and disk growth for a declared peer/message workload, including relay/bootstrap outage | [Numerical policy and fail-closed evidence checker](P2P_OUTAGE_LOAD_ACCEPTANCE.md) implemented; actual final-candidate multi-host soak and retained passing artifact outstanding |
| Public operations | Operator-controlled endpoints pass the intended networks' connectivity, health, restart and monitoring checks | Configuration/profile exists; deployment, uptime and live peer count are not established |
| Feed provenance | Each admitted feed has an authorized source, permitted redistribution, availability timestamps and independently checked quality | Fail-closed operator policy and durable attestation implemented; actual rights, entitlements and independent quality evidence remain required |
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
python -m unittest scripts/test_check_p2p_load_acceptance.py -v
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

The core relay harness sets `OSIRIS_HOLE_PUNCH_ENABLED=0` on both peers and
checks their startup logs before publishing. This disables automatic DCUtR
upgrades so its relay-only assertions measure the intended topology rather than
racing a successful direct connection. The setting accepts only `0` or `1` and
defaults to `1` for normal node operation. The test still requires exclusively
relayed connections, durable signed bindings and the exact decrypted digest.

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
