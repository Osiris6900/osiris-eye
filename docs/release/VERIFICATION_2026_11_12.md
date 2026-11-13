# Release implementation evidence verified on 2026-11-12

The implementation baseline is `5b43e61c8c974ef4e52b22c32dbd9cba17ccc62c`
(tree `302618d502fe0ca5103b1d040a78e6546610bf55`). The release still has
**3 evidenced, 14 partial and 3 remaining gates**. Implementation verification
does not supply the independent approvals and operational evidence listed in
[the seventeen-gate execution ledger](../workstreams/RELEASE_GATE_EXECUTION_2026_11_12.md).

## Downloaded evidence

[The machine-readable record](VERIFIED_ARTIFACTS_2026_11_12.json) contains the
actual downloaded ZIP and member-file hashes, expiration dates, exact baseline,
run attempts, successful job conclusions, performed checks and scope limits.
All five ZIP hashes were recomputed from downloaded bytes and matched GitHub's
reported artifact digests. These are historical baseline artifacts, not claims
that a later verification-tool commit was tested by those runs.

| Artifact | Successful run / attempt | Independently recomputed ZIP SHA-256 |
|---|---|---|
| `dependency-sbom-43312608076-1` | [43312608076](https://github.com/Osiris6900/osiris-eye/actions/runs/43312608076) / 1 | `aa79bf3aa2a6ef7601b32d78305e36b8e2bcb90cda0f4d6e59320f02c999ec94` |
| `receipt-paper-43312608086-1` | [43312608086](https://github.com/Osiris6900/osiris-eye/actions/runs/43312608086) / 1 | `80466496cfeccc554d08e17f558e6a46432d7e44f69121b3dd45816d103f2165` |
| `market-paper-acceptance-43312608086-1` | [43312608086](https://github.com/Osiris6900/osiris-eye/actions/runs/43312608086) / 1 | `cabe244d7deec6576e0a0db2207a9678c87b9b8c34281333eacdbe5c073171c6` |
| `platform20-browser-43312608086-1` | [43312608086](https://github.com/Osiris6900/osiris-eye/actions/runs/43312608086) / 1 | `3985c0fffb03e2e2237a35b54a5aaea51db9c1c0fa8e6af738b50e5e0840a967` |
| `synthetic-model-research-43312607876-1` | [43312607876](https://github.com/Osiris6900/osiris-eye/actions/runs/43312607876) / 1 | `50381ae9d5b79663f8a339a511b10996d91d5ba083a091bfc4afea3f812dd0d6` |

The dependency artifact reproduces byte-for-byte from all six committed inputs,
the committed crate metadata and Python licence snapshot: **613 components**.
The receipt report's **792 source-file hashes**, complete worktree digest,
checkout/tree, digest sidecar and disabled-live/independent-acceptance flags were
checked. Its real two-local-node receipt, risk, fill, accounting and process-loss
checks passed. This is local-process evidence, not independent-host acceptance.
The market report's eight declared source hashes and digest sidecar also match.

The research archive passes the existing `verify_bundle` lineage verifier,
including its manifest and every file in `sha256sum.txt`. Its synthetic results
and pending independent reviews remain explicit. The nine browser checks are
associated with the verified CI run; that report has no internal commit binding,
so its scope is stated accordingly. No production or licensed-feed capture is
inferred from these fixtures. Retention ends on 2026-11-26; hashes alone cannot
recover expired artifact contents.

## Implemented SBOM verification

`scripts/verify_dependency_sbom.py` closes the gap between generating a complete
SBOM and verifying the retained output. It reads a downloaded ZIP without
extracting it, checks a caller-supplied archive digest, then independently reads
the explicitly selected commit's Git objects and reconstructs the full locked
graph offline. Recomputing a forged SBOM's own hash does not make an omitted
Rust root, Python dependency, changed licence or changed package checksum pass.

It also checks the source/tree, generator/metadata/input hashes, CI run/attempt,
digest sidecar, component count and explicit false authorization flags. Missing,
extra or duplicate members, symlinks, oversized files, duplicate JSON keys,
nonfinite JSON values and moving refs fail closed. Existing result reports are
not overwritten. The checker authenticates neither CI nor reviewer identity;
expected source/run/archive values must come from an independently trusted
caller. A PR merge checkout is recorded separately from its declared head and
their complete-tree equality is reported rather than assumed.

```bash
python scripts/test_verify_dependency_sbom.py
python scripts/verify_dependency_sbom.py \
  --archive /path/to/downloaded-dependency-sbom.zip \
  --archive-sha256 aa79bf3aa2a6ef7601b32d78305e36b8e2bcb90cda0f4d6e59320f02c999ec94 \
  --commit 5b43e61c8c974ef4e52b22c32dbd9cba17ccc62c \
  --run-id 43312608076 --run-attempt 1
```

Dependency-security CI now runs the verifier's regression suite and checks its
freshly generated evidence directory before uploading the three SBOM files.
Both inventories clear stale open-PR references from the old implementation
stack. No gate is promoted by that cleanup or this verification tooling.

T01 still requires an accepted final candidate audit/provenance decision and
reviewed `paste` maintenance disposition. T04/T18/T19 require approved sustained
independent-host network/recovery evidence. T06/T09/T20 require actual external
crypto, licensing and release decisions; the remaining data/model/risk/execution
gates retain their genuine-source, calibration and independent acceptance
requirements. No such external evidence was available from the connected
tracker or this task's environment. PAPER-ONLY and fail-closed remain enforced.
