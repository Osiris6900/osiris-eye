# P2P outage/load acceptance

The outage/load release gate requires a retained measurement from a real soak.
The process-local benchmark and fault-injection harnesses are useful regression
checks, but cannot satisfy this gate.

## Declared numerical targets

The reviewed policy is
[`config/p2p_load_acceptance_policy.json`](../config/p2p_load_acceptance_policy.json).
Change it only in a reviewed commit **before** measurement; do not relax it after
seeing a failed run. The checker includes the policy's SHA-256 digest in its
result.

| Limit | Initial release target |
|---|---:|
| Declared workload | at least 100 peers, 10,000 published messages and 900 seconds |
| Sampled recovery after relay/bootstrap outage | at most 30,000 ms |
| Delivered-message latency | p95 at most 1,000 ms; p99 at most 2,000 ms |
| Final delivery accounting | zero lost and zero pending messages; at most 100 rejected |
| Peak RSS per host | at most 536,870,912 bytes (512 MiB) |
| CPU per host | at most 80 percent |
| Disk growth per host | at most 67,108,864 bytes (64 MiB) |

These are acceptance targets for one declared workload, not a claim of maximum
capacity, Internet-wide latency or sustained availability.

## Retained evidence contract

Supply one UTF-8 JSON object with schema `osiris.p2p_load_acceptance.v1` and:

- `measurement_class: "operator_attested_real_multi_host"`;
- the full lowercase candidate `commit_sha`, `clean_worktree`, retained HTTPS
  `workflow_url`, positive `attempt` and a bounded operator identifier;
- timezone-aware `started_at` and `ended_at`;
- `workload`: peers, published messages, fan-out and duration in seconds;
- `delivery`: mutually exclusive final counts for `sent`, `delivered`, `lost`,
  `rejected` and `pending`. A message retried after an outage and eventually
  delivered is counted only as delivered. The five outcomes must sum to `sent`;
- `latency_ms`: measured end-to-end delivery `count`, `p50`, `p95`, `p99` and
  `max`. Count must equal delivered messages and percentiles must be ordered;
- at least two uniquely identified `hosts`, each with its binary SHA-256,
  peak RSS bytes, CPU percentage and state-directory disk growth in bytes;
- `outage`: kind (`relay`, `bootstrap` or `relay_and_bootstrap`),
  `outage_start_ms`, and the strictly increasing probe samples accepted by
  `check_p2p_outage_recovery.py`.

Use sanitized host identifiers, not private addresses. Keep credentials,
private keys, peer-key state and other secrets out of the artifact. Do not clear
replay history or state directories to hide disk growth or make a failed run
pass.

## Check

From the repository root:

```bash
python scripts/check_p2p_load_acceptance.py /secure/path/to/sanitized-evidence.json
python -m unittest scripts/test_check_p2p_load_acceptance.py -v
```

An explicit policy path is available for reproducing a historical reviewed run:

```bash
python scripts/check_p2p_load_acceptance.py evidence.json \
  --policy config/p2p_load_acceptance_policy.json
```

The command exits zero only when every structural check and numerical threshold
passes. Missing, malformed, inconsistent, simulated, dirty-worktree or
single-host evidence cannot produce a passing result. Threshold failures are
listed in `failures`; malformed evidence returns a failure reason.

## Evidence boundary

A passing result means that the supplied artifact is internally consistent and
meets the declared thresholds. It remains operator-attested evidence: the checker
does not independently verify host separation, metric collection or artifact
authenticity. `independent_hosts_verified`, `continuous_availability_verified`
and `production_readiness_evidence` remain false. The release gate stays open
until an actual final-candidate run and its sanitized retained artifact exist;
no such run is created by this offline checker.

## Run the encrypted soak on GitHub runners

The existing two-host E2EE workflow has an opt-in `soak` input:

```bash
gh workflow run p2p-real-cross-host-e2ee.yml --repo Osiris6900/osiris-eye \
  --ref REVIEWED_CANDIDATE_BRANCH -f soak=true
```

`TS_AUTHKEY` and the existing SSH/Tailscale setup are reused. The candidate must
contain the collector on both clean checkouts. Both hosts build with `--locked`.
The workflow retains only sanitized `soak-evidence.json` and `soak-gate.json`,
including failed outcomes; private node state and SSH keys are never uploaded.
Inspect the artifact and exact revision before promotion.

The declared topology has 50 peers on each host: 99 encrypted publishers and
one recipient/bootstrap on host B. Every publisher must deliver an encrypted
warmup before measurement. Workload messages rotate through all 99 publishers;
about half traverse A-to-B, and the remainder exercise B-to-B paths. Each
retained delivery record identifies its source/recipient host and digest.
This is not a 100-host test or a claim that every message crosses machines.

At the midpoint, the recipient/bootstrap stops for five seconds and restarts
using the same identity, replay and key state. Cross-host probes measure loss
and recovery; scheduled workload pauses until delivery resumes. This tests
recovery, not in-flight durability or continued delivery through an outage.
Probes and warmup are excluded from workload counts and latency percentiles.

Latency uses the controller's single monotonic clock from write to observed
recipient decryption, including SSH event transport. It is an observation upper
bound, not host-clock-synchronized wire latency. One-second resource snapshots
aggregate node/agent RSS and CPU (plus the controller on A), normalize CPU to
logical host cores and retain peak state-file size before cleanup. OS, SSH
daemon and unrelated host processes are not included. Sampling can miss spikes.
Node exits, invalid event streams and missing measurements invalidate the run.
Binary hashes and exact source revisions are retained; local-agent smoke mode
is explicitly rejected by the real multi-host evidence checker.

For a local collector regression, with a built binary:

```bash
python scripts/run_p2p_soak.py --binary /absolute/path/to/osiris-p2p-node \
  --local-agents --peers 4 --messages 30 --duration 30 --port 19401 \
  --evidence /tmp/osiris-soak-smoke.json
python scripts/test_run_p2p_soak.py
```

A local smoke cannot close the release gate. The committed acceptance policy is
never relaxed by this runner; the full real-host evidence checker determines
whether the numerical gate passes.
