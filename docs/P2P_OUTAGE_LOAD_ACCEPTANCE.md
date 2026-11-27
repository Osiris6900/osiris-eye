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

## Agent shutdown and process evidence

An agent reports `finished` only after it has terminated and reaped its node
processes and drained their output readers. Shutdown closes node stdin before
SIGTERM, uses shared cohort deadlines, and prevents repeat TERM/HUP signals from
interrupting parent waits. An undrained reader or unreaped node fails cleanup.
The controller allows parent-managed cleanup before a bounded group-kill fallback.

`scripts/test_p2p_soak_cleanup.py` checks the real process contract with a small
local run. Its Linux subreaper records any adopted orphan as a failure and reaps
it for test hygiene; it does not turn a cleanup failure into a pass. This test
and the ordinary lifecycle unit tests supplement the retained measurement.
They do not establish independent-host capacity or release acceptance.

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

The declared topology has 25 peers on each of four independent runners: 99
encrypted publishers and one recipient/bootstrap on host B. Publishers admit
only the recipient PeerId so automatic discovery cannot silently expand the
declared star into a full mesh. This is not an open-mesh capacity test.
The direct-only experiment disables automatic relay reservations, seed fetches
and cached-peer rejoin dials, and uses one established connection per peer.
Default production connection limits are unchanged. Every publisher must deliver an encrypted
warmup before measurement. Workload messages rotate through all 99 publishers;
about three quarters cross host boundaries, and the remainder exercise B-to-B
paths. Each
retained delivery record identifies its source/recipient host and digest.
This is not a 100-host test or a claim that every message crosses machines.

At the midpoint, previously submitted workload is drained with a bounded
30-second wait. A drain failure fails the measurement rather than hiding loss.
The recipient/bootstrap then stops for five seconds and restarts
using the same identity, replay and key state. Stale subscriptions are cleared
and new encrypted-topic subscriptions are observed before recovery warmup. Cross-host probes measure loss
and recovery; scheduled workload pauses until every publisher reconnects, subscribes and
delivers a fresh encrypted recovery warmup. This tests
recovery, not in-flight durability or continued delivery through an outage.
Probes and warmup are excluded from workload counts and latency percentiles.
Outstanding probes remain observable after the next sampling tick; a delayed
valid post-restart delivery can still establish sampled recovery.

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

## Scale remediation under review

The initial two-runner/100-peer attempt timed out before workload traffic, with
about 3.5–3.9 GiB aggregate RSS per host. The candidate now coalesces key-binding
announcements across subscription bursts, announces only for key-topic
subscriptions, and avoids rewriting byte-identical verified key bindings.
Signature, epoch, rollback and corrupt-index checks remain enforced. Distribution
to four runners changes the declared topology, not the numerical policy. Exact
source/binary hashes and the new run result are still required before promotion.


## Traffic-on outage diagnostic

The default soak deliberately drains delivery before restarting the sink and
pauses its scheduled workload until every publisher recovers. Historical results
retain that mode. They do not measure delivery of traffic submitted during an outage.

For a new controlled run, `run_p2p_soak.py --traffic-during-outage` continues its
scheduled workload while the recipient is stopped and reconnecting. It does not
drain before the stop and the controller never resubmits a workload observation.
With the separate `--publisher-outbound-queue` option, a publisher may retry only
attempts that definitely were not sent. Every workload record retains its
controller monotonic publish time, observed delivery time/count, loss at the
observation cutoff, duplicate deliveries observed before that cutoff and the
number of definitely-unsent retry-processing claims. Retry events are accepted
only for a tracked canonical observation digest on its original source host;
accepted or ambiguous sends are never reissued by the queue. A retry claim is not
proof of network transmission. Aggregate and per-record retry totals must match. The controller stops consuming events after every agent
reports finished; later or undrained events are not claimed. Post-restart delivery
of outage publications is counted separately from ordinary post-restart traffic.
The scheduler is best effort: stop acknowledgement and event processing can delay
its nominal cadence. Elapsed duration alone does not establish uniform publication
cadence; the retained per-message times must be assessed separately. Only intended
Host B decryption events for a tracked digest can count as a delivery. The report binds publication counts to the actual sink-stop, restart and full
publisher-recovery windows. An aborted exercise retains pending-at-abort records
rather than declaring their permanent loss.

Offline consistency checks reject workload delivery timestamps inside confirmed
sink downtime, allowing one millisecond at clock boundaries for truncation. They
also apply the unchanged 30-second recovery limit to the declared recovery of
every publisher; one fast probe cannot substitute for recovery of the other peers.

The existing capacity workflow exposes this only as an explicit manual option:

```bash
gh workflow run p2p-capacity.yml --repo Osiris6900/osiris-eye --ref REVIEWED_BRANCH \
  -f sustained_100=true -f traffic_during_outage=true
```

To exercise the queue remediation and bounded runtime allocation together, add
`-f publisher_outbound_queue=true -f node_worker_threads=1`. These settings do
not relax the delivery, latency, recovery or memory policy and require a new run.

The command needs normal authorized GitHub Actions access and the already
configured private test-overlay secret. It does not provision independent
staging hosts. `traffic_during_outage=true` without `sustained_100=true` is refused.
The queue also requires traffic-on mode. The three short baseline sweeps retain
their drained-restart behavior.

The predeclared zero-loss and latency limits are unchanged. This diagnostic may
fail them: disconnected Gossipsub publication is not a durable resend mechanism.
Failures and partial measurements remain retained by the workflow. Passing
reference/schema checks cannot certify host independence, NAT traversal, open
mesh, production DR or T04/T19 acceptance; those decisions still need independent
evidence and review.


## Explicit node worker allocation

`run_p2p_soak.py --node-worker-threads 1` (also `2` or `4`) sets the standard
`TOKIO_WORKER_THREADS` environment variable on every spawned node, across all
local/SSH agents. Omitting the option leaves this variable unset and clears any
inherited value, preserving the historical default allocation by available CPU
cores. The locked Tokio 1.54.1 runtime reads this variable in
[`loom::sys::num_cpus`](https://github.com/tokio-rs/tokio/blob/tokio-1.54.1/tokio/src/loom/std/mod.rs);
the node's bare `#[tokio::main]` has no explicit worker-count override.

The workflow choice `node_worker_threads` accepts `default`, `1`, `2` or `4`
and applies to both the short baseline sweeps and optional sustained run:

```bash
gh workflow run p2p-capacity.yml --repo Osiris6900/osiris-eye --ref REVIEWED_BRANCH \
  -f sustained_100=true -f node_worker_threads=1
```

This changes real runtime allocation when many node processes share each host.
It does not relabel aggregate process RSS as PSS or increase the predeclared
512 MiB host budget. Requested settings and exact node environment configuration
are recorded per host and in the report; actual runtime thread counts are not
claimed independently measured. Different runtime settings require a new retained
run, with the same memory, latency, delivery and recovery policy. No memory-budget
pass or release acceptance is inferred from selecting one worker.

## Capacity handoff recovery and retained candidate failures

The capacity workflow now waits for downloaded, validated artifact bytes at
all coordination boundaries. A name appearing in the GitHub artifact listing
can precede storage availability; a single `actions/download-artifact` call
previously aborted paused run
[43593970718](https://github.com/Osiris6900/osiris-eye/actions/runs/43593970718)
before any sustained measurement when Host B's endpoint was listed but not
downloadable. Bounded retries stage downloads in a temporary directory and only
publish the expected same-run, same-attempt SSH public key, tailnet endpoint
with a valid Ed25519 host key, or controller-result payload after validation.
A missing or malformed payload still times out. Receiver cleanup waits for
actual controller-result bytes, including an explicit failure result. All
three capacity builds use their committed Cargo lockfile.

The previous candidate `84f307bbc7fafaceea88d5ee628a1151795c6243` also has an
actual completed traffic-on run
[43593983448](https://github.com/Osiris6900/osiris-eye/actions/runs/43593983448).
Its downloaded `capacity-report-43593983448-1.zip` has SHA-256
`3bc7fb591d239138396438691c62f51eb2254be1b6c18c6815101ceff69086a6`.
The raw `sustained-100.json` SHA-256 is
`191c25bebb0aca919aa72bf21c84b65f4809fe3b886267aedbc1af86c5783b40`.
It measured 9,819 delivered and **181 lost** of 10,000 workload messages over
957.404375 seconds, with zero observed duplicates. It recorded 55 publications
while the sink was stopped, 223 during the full outage/recovery window and 43
outage publications delivered after restart. Aggregate peak RSS was
550,522,880 bytes on A, 559,902,720 on B and 522,551,296 on C. The unchanged
536,870,912-byte per-host limit failed on A and B; the zero-loss limit failed.
These results remain failed measurements. Artifact handoff retries do not
establish durable message delivery or fix the measured memory limits, and a
new final-candidate operational run remains necessary.
