# Execution of the seventeen open release gates

This candidate builds on #427. It closes a local cross-module implementation
gap and makes every gate's remaining requirement explicit. It does not supply
missing independent approvals or change any acceptance status. The inventory
continues to record 3 evidenced, 14 partial and 3 remaining gates.

## Receipt to canonical paper execution

`osiris_api.paper_receipt_execution.execute_receipted_observation` is an
explicitly invoked local integration. It requires
`OSIRIS_RECEIPT_PAPER_ENABLED=1`, the enabled receipt scheduler and P2P ingestion,
a confirmed receipt, current recipient/provider/feed policy and an initialized
canonical paper account. Risk-service availability defaults to false. There is
no HTTP route, background trader, broker transport or account initialization.

The Rust receiver verifies recipient admission. The sender's Rust node verifies
the signed receipt and sends its event through the existing PID/UID-pinned Unix
channel. The Python consumer now persists sender and kernel process provenance
atomically with acknowledgement. A different sender cannot reuse that event as
a duplicate. Legacy events lack this stronger provenance until authenticated
redelivery; their historical delivery state remains intact.

The paper path binds the acknowledged event to the scheduler's exact stored
observation, checks its payload hash, availability and current declared rights,
and independently evaluates the research proposal against canonical risk.
Money parsing uses the existing resource bounds and exact float bridge contract.
Source mode comes from the invoking configuration, never a peer's claim.

A stable strategy/observation key and immutable economic fingerprint prevent
another recipient/message, changed inputs or process restart from creating a
second fill. The fingerprint is committed into the order's canonical audit
event, binding the receipt trace to the actual risk decision and fill. Existing
unbound orders cannot be relabelled as receipt-driven execution. The ordinary
HTTP order route cannot set the internal origin reference. Saved results are
historical outcomes; replay reports current closed risk without authorizing a
new execution, and always reconciles actual accounting.

The invoking process and local authority databases are trusted, as in the
existing receipt consumer. PID/UID evidence concerns the recorded local process,
not independent host identity or reviewer independence. The feed policy is an
operator declaration, not an authenticated vendor contract. This integration
does not approve its synthetic proposal as a trading model.

## Real process acceptance and retained evidence

```bash
python scripts/run_receipt_paper_acceptance.py --output /tmp/receipt-paper.json
python scripts/verify_all.py --profile engineering --execute \
  --report /tmp/osiris-release-gate-engineering.json
```

Build the P2P binary first, or select it with `OSIRIS_P2P_TEST_BINARY`. Use the
project Python environment and permit local Unix/TCP sockets.

The new harness runs two actual local Rust E2EE nodes and independently launched
Python processes. It verifies refusal before acknowledgement, kernel-pinned
receipt persistence, proposal/risk/fill linkage, fee-inclusive round-trip cash
and P&L, canonical Admin Centre consistency, replay/conflict checks and current
kill-switch/recipient revocation. A child actually exits after a fill commits
but before its integration result is archived; a fresh child must recover that
same fill once. All accounts, identities and synthetic market inputs are
disposable. It retains the before/after source binding, binary digest, receipt
identifiers, order/risk IDs, account audit and explicit limitations. Existing
output files are not overwritten; failures return nonzero and a failed report.

The engineering profile now has 25 checks, including complete offline SBOM
generation with retained source/input/output digests, maps local coverage to T01–T20 as
well as W01–W20, and lists the seventeen still-open release gates. Missing
`receipt-paper` coverage fails the relevant local groups. A green report never
sets `release_accepted`, `merge_authorized` or `live_trading_authorized` true.
CI runs the actual process flow in its Rust job and retains JSON plus digest
sidecar for fourteen days. The GitHub checkout can be a PR merge ref; compare
the complete tested tree and retained source hashes before citing that result.

## Gate-by-gate completion requirements

| Gate | Engineering delivered/exercised | Still needed for acceptance |
|---|---|---|
| T01 | Hashed deployment lock, resolved Python SBOM, license/provenance CI and immutable workflow refs | Accepted final-candidate audit/SBOM/provenance; reviewed residual paste maintenance disposition |
| T04 | Real node/receipt/regression harnesses; existing short capacity artifact | Sustained approved independent-host NAT/relay/open-mesh and traffic-on outage evidence |
| T06 | Independent-review intake validator and commissioning checklist | Actual appointed external reviewer, authenticated signed findings/retest and decision |
| T07 | Alpaca/OANDA/Polygon/Twelve Data bar adapters and instrument imports | Authorized credentials/captures, genuine instrument/corporate-action coverage and source acceptance |
| T08 | Quality/freshness/availability gates, source-governed views and offline native capture | Genuine sustained vendor streams/corporate actions and independently verified time/source artifacts |
| T09 | Entitlement intake, expiry/revocation and current policy enforcement | Original licences, permitted use/redistribution and rights-owner approval |
| T10 | Governed news/event intake, provenance storage and deduplication | Authorized real macro/event feeds, retained captures and independent provenance review |
| T11 | Point-in-time features, actual baseline fitting, immutable reproduction and synthetic archive | Frozen genuine candidate data/model/features and lifecycle acceptance |
| T12 | Chronological/cost-adjusted walk-forward and reproducibility | Genuine blinded holdout, independent leakage review and calibrated costs/impact/borrow sensitivities |
| T13 | Exact current risk checks, kill switch and receipt-bound canonical decisions | Independently owned/approved limits, budgets, concentration/drawdown and exception governance |
| T14 | Persisted Alpaca paper intent/ack/reconciliation and canonical receipt-to-paper OMS | Actual authorized broker-paper captures, operational reconciliation and broader FIX session |
| T15 | Cash/positions, partial fills, fees, realized/unrealized P&L and adverse slippage | Calibrated venue liquidity/queues/impact, margin/borrow models and multi-asset comparison |
| T16 | Fault regressions, actual process loss/recovery and current feed/risk/kill-switch refusal | Independent service-outage exercise and operational/risk review on final candidate |
| T17 | Canonical API-backed views, actual Rust metrics and Chromium/MFA boundaries | Genuine source connections and sustained authenticated monitoring/alert operations acceptance |
| T18 | Actual temporary backup/restore/continuation, portability checks and authority fencing | Approved independent-host service DR, measured RTO/RPO, custody/storage and operator decision |
| T19 | Actual Rust receipt → Python ledger → risk → paper order/fill → P&L → process loss/recovery | Sustained independent-host capacity/failover, broader DR and final operational acceptance |
| T20 | Paper-only signoff worksheet and external-evidence intakes | Actual security/model-risk/licensing decisions and authorized operator paper-only signoff |

The blocker text in both release inventories is updated to distinguish current
implementation from these missing requirements. Historical evidence references
stay historical; none is silently promoted to evidence for the new candidate.
The T20 description is paper-only. No live pilot is authorized.

Independent reports, source-owner licence approvals and approved external test
targets are not configured in this task's environment. Their absence cannot be
replaced with local fixtures, generated signatures or self-approval. Expanded
products tracked in #413–#416 remain separate from these acceptance gates.
