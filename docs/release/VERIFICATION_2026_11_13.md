# Seventeen-gate verification checkpoint — 2026-11-13

At exact main baseline `43157b1bad5a93c8bc441a21a04b8572a9e736f5`
(tree `098c8989badd4ff77e99f606bc7e2af6fecf52cb`), **0 of the seventeen
previously unaccepted gates have independent acceptance**. The complete
inventory remains **3 Implemented with evidence / 14 Partial / 3 Remaining**.
The detailed requirements for all seventeen are retained in the
[JSON checkpoint](VERIFIED_ARTIFACTS_2026_11_13.json) and
[execution ledger](../workstreams/RELEASE_GATE_EXECUTION_2026_11_12.md).

## Verified baseline evidence

GitHub reports five successful workflows / eleven successful jobs: core CI,
Quant API CI, dependency security, short Tailscale observation exchange and
three-peer mesh. Their exact run IDs, attempts and job URLs are in the JSON.
The latter two tests do not establish sustained capacity/failover or independent
operational acceptance: cross-host artifacts are short-lived coordination
markers, and the one-runner mesh has no retained success artifact.

The following ZIP hashes were recomputed from downloaded bytes and matched
GitHub's reported digests. Every member hash and expiry is retained in the JSON.

| Artifact | Run / attempt | Downloaded ZIP SHA-256 |
| --- | --- | --- |
| `dependency-sbom-43326094957-1` | [43326094957](https://github.com/Osiris6900/osiris-eye/actions/runs/43326094957) / 1 | `b92152e8986e29d1736b8b943fd38d8e8bd4a6ade9f0929e39f319b716081080` |
| `receipt-paper-43326095035-1` | [43326095035](https://github.com/Osiris6900/osiris-eye/actions/runs/43326095035) / 1 | `6a7fe5b1eaa682ea4f4a09c451660f58282fe8284b4112cb5d3f8b47ee5f46f8` |
| `market-paper-acceptance-43326095035-1` | [43326095035](https://github.com/Osiris6900/osiris-eye/actions/runs/43326095035) / 1 | `c8f502ae2e40c908ab5da386b3ecc5e76f031541e06f08f1b1e6e53533419009` |

The SBOM's **613 components** reproduce byte-for-byte from the complete locked
graph. Receipt evidence matches **all 799 regular committed source files**, its
complete worktree digest and exact checkout/tree. Its ten declared local checks,
two recovery/fill traces, ten audit rows and risk/decision/fill identifiers are
consistent. The market artifact's eight source hashes, sidecar, eleven audit
rows, nine declared checks and restart cash/P&L were also checked. Both paper
exercises use isolated synthetic inputs; they do not supply actual vendor or
broker-paper captures. These are historical baseline artifacts, not tests of
the subsequent verifier implementation. Retention ends on 2026-11-26; copy
original bytes into approved evidence custody before expiry. Hashes alone do
not preserve artifacts.

## Repeatable receipt evidence verification

`scripts/verify_receipt_paper_artifact.py` replaces manual receipt-report
consistency checking. It requires an exact checkout SHA and either a bounded
two-file evidence directory or a downloaded ZIP plus its trusted expected
SHA-256. It reads committed Git objects rather than current worktree contents;
omitted/added/changed source files fail even when the report's own digest and
sidecar have been recalculated. Missing/duplicate checks, changed accounting,
broken audit chains, unbound risk decisions/fills, missing process provenance
and claims of independent approval fail closed. The shared archive reader
rejects unexpected/duplicate names, traversal entries, symlinks and oversized
members without extracting them. Existing output reports are not overwritten.

This checks **report consistency**, not the truth of reported runtime events.
It neither rebuilds the binary nor re-verifies the Rust receipt signature or
runtime origin fingerprint, whose complete inputs are absent from this report.
It does not authenticate CI, independent hosts, reviewer identity or licensing.
Those limitations are explicit false fields in its output. It never accepts a
release gate or authorizes live trading.

```bash
python scripts/test_verify_receipt_paper_artifact.py
python scripts/verify_receipt_paper_artifact.py \
  --archive /path/to/receipt-paper-43326095035-1.zip \
  --archive-sha256 6a7fe5b1eaa682ea4f4a09c451660f58282fe8284b4112cb5d3f8b47ee5f46f8 \
  --commit 43157b1bad5a93c8bc441a21a04b8572a9e736f5
```

CI runs its offline regression suite and verifies fresh real-process evidence
before uploading it. The engineering profile now includes this verification
as its **26th check**, required for local T13–T16 and T19 coverage. Run the full
profile from a clean committed checkout with the documented toolchains; dirty
or incomplete source evidence cannot satisfy this exact-commit check.

## Remaining acceptance requirements

- **T01:** reviewed residual `paste` maintenance disposition and accepted final-candidate audit/provenance.
- **T04/T18/T19:** approved sustained independent-host NAT/relay/open-mesh, outage and DR evidence, measured RTO/RPO and custody/operator review.
- **T06/T09/T20:** actual independent signed crypto/security findings/retest, original licence/permitted-use/redistribution approvals and final paper-only release decision.
- **T07/T08/T10:** genuine authorized multi-asset/streaming/corporate-action/macro/event captures and source-owner provenance acceptance.
- **T11/T12/T13:** frozen genuine model/data/features, independent leakage-adjusted OOS and cost sensitivity, and approved risk budgets/limits/governance.
- **T14/T15/T16/T17:** authorized broker-paper reconciliation, calibrated execution/margin/borrow comparison, independent outage checks and sustained authenticated operational monitoring.

No such external evidence was supplied through this task or the connected
release tracker. Both inventories record this checkpoint without changing any
gate status. PAPER-ONLY and the independent risk gate remain in force.
