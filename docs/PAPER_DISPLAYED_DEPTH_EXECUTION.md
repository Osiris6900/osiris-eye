# Source-bound displayed-depth paper execution

`paper_depth_execution.execute_depth_fill` executes one eligible displayed level
of an existing working order in the canonical, long-only cash paper ledger.
It uses `DepthService.current_snapshot`: immutable Coinbase L2 history, declared
source reference, sequence monotonicity, receive-time freshness and clock fencing.
The financial ledger and the control-plane database must be distinct files.
Capture retains the best 1,000 levels per side after validating every raw level.
The HTTP response stays capped at 2 MiB and each raw side at 50,000 levels;
genuine BTC responses can exceed the former 20,000-level parser limit.

The caller fixes a participation fraction, minimum latency and maximum age.
The policy is journal-bound on the first snapshot for each instrument and cannot
change within that account. Defaults are 10% participation, 250 ms minimum age
and 5 s maximum receive age. These are explicit conservative assumptions; they
have not been calibrated against authenticated venue order/fill records. Local
receive time does not independently prove exchange publication time.

Fills use the best available displayed level inside the saved order limit.
Fees follow the existing order-fee allocation, and the ledger rechecks current
risk and its saved kill switch. Cash, position, financial fill, source snapshot
and liquidity consumption commit atomically. Stable plan IDs replay the saved
outcome without another fill or a new approval. A no-fill outcome is also final
for that plan; a later attempt uses a new ID.

Consumed virtual volume cannot be spent twice. An unchanged newer snapshot does
not restore it; only observed net increases in displayed size replenish capacity.
This is deliberately conservative and does not identify the actual provenance
of individual orders. Orders outside the displayed market receive no simulated
passive queue fill. Hidden liquidity, queue priority, borrowing, leveraged margin,
market impact calibration and live venue execution remain outside this model.

New accounts record `closing-cash-conservation-v1` in their initialization event.
When an instrument becomes flat, its closing fill settles cumulative fee-inclusive
cash flows less previously allocated realized P&L. This eliminates a repeating
average-cost rounding residue. Partial inventory retains the existing Decimal
average-cost display. Journals without this accounting marker preserve their
original allocations; historical economics are not rewritten.

The operator loss view now uses the same realized-plus-adverse-unrealized policy
as execution, within a single reconciled read-only snapshot. It cannot report
clear while a required holding lacks a fresh mark. A known realized breach is
still displayed when remaining holdings cannot be marked.

## Genuine public-data exercise

From a clean committed checkout, with the Python deployment/development environment:

```bash
python scripts/run_depth_paper_acceptance.py \
  --output-dir /tmp/osiris-genuine-depth-evidence
```

The script freezes its candidate commit, source-file hashes and diagnostic plan
before fetching BTC-USD and ETH-USD books from fixed public Coinbase endpoints.
It retains original response bytes, byte hashes, URLs, request/receive times,
normalized books, durable ledgers, fills, fees, cash/P&L, simulated pre-commit crash
rollback and actual new-process idempotent replay. Output is private and outside
the checkout; an existing operator ledger is never selected for the exercise.
Unreachable/malformed sources, insufficient liquidity, stale books and refused
risk decisions produce a failed report. There is no fabricated substitute.

These are explicitly diagnostic buy/sell round trips using an assumed 10 bps fee,
independent of the research strategy's signal. They do not demonstrate profitable
alpha, authenticated broker reconciliation, licensed redistribution or independent
release acceptance. T15 and the other unaccepted release gates stay open.
