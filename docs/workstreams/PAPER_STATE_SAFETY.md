# Paper-ledger resource and refusal boundaries

Two remaining defects were reproduced against integration commit
`eb7c7ffea0d769166c1fbda2bb7ed0e170b8f4f2` and corrected:

1. A compact decimal such as `1e-999999999` passed ledger numeric validation.
   Canonical fixed-point formatting could then expand it into an enormous
   string. The canonical and legacy input parsers now share resource bounds,
   and canonical formatting validates derived/stored decimals before expansion.
2. Rejecting a live request called the ordinary SQLite initialization path for
   its refusal audit. It created a missing database/parent directory and could
   migrate existing legacy state before refusing the request. Refusal logging
   now opens only an existing database with SQLite `mode=rw`; it never creates
   schema or runs migrations. Logging failures do not mask the original
   `live_execution_disabled` refusal. Valid accounts still retain refusal events
   in their verified audit chain.

`decimal_safety.py` limits incoming decimal text/coefficient size to 256
characters/digits and absolute decimal exponent to 1,000. Oversized integers are
rejected before string conversion. These are resource bounds, not order limits
or capital/risk budgets. Values are not rounded or silently truncated. Canonical
ledger float rejection and the legacy ledger's existing float support are
preserved. Existing stored values outside these bounds require investigation;
they are never automatically rewritten to fit.

The regression suite exercises account initialization, orders, partial fills,
marks, reconciliation and HTTP failures. It checks unchanged cash/positions/
orders/audit on malformed input, missing/uninitialized/legacy/corrupt SQLite
files, URI-sensitive file names, valid refusal audit recording, sub-float exact
decimals and the legacy float contract. It uses only disposable local state and
does not submit broker orders.

```bash
python -m pytest -q services/quant-api/tests/test_paper_resource_boundaries.py
python -m pytest -q services/quant-api/tests tests/test_ultra_fast_feed.py
```

This change affects W02/W16 local execution/accounting reliability and W18 state
preservation. It does not accept an external release gate, enable live trading,
add a live broker connector or change source rights or financial risk limits.

The dependency audit also confirmed that `paste 1.0.15` is pulled through
`netlink-packet-core 0.8.2` / `if-watch` in the current libp2p graph. The published
0.8 line has no newer compatible patch; 0.9 requires parent dependency changes.
No unreviewed fork, dependency removal or advisory suppression is introduced.
T01 remains partial, with independent supply-chain review still required.
