# Connected market-to-paper acceptance

The connected path is Coinbase public one-minute candles → deterministic momentum
proposal → independent pretrade risk → durable paper order/fill → cash, positions,
fee-inclusive realized/unrealized P&L → reconciliation. No broker credentials or
exchange order endpoints are used. The pipeline supports USD-quoted Coinbase
symbols, long-only fully funded positions, and deterministic simulated fills at the latest closed candle's close by
default. The optional `slippage_bps` request field (decimal string, 0–1000;
default 0) applies fixed adverse cost to each paper fill: BUY pays above the
close, SELL receives below the close; account marks use the unadjusted close.
Independent risk checks and the durable ledger use the adjusted price. A replay
of the same candle with different slippage parameters fails closed. This is a
stress/sensitivity assumption, not calibrated venue execution, depth-of-book
liquidity, a real market-impact model, or margin trading.

Run the executable acceptance harness from the repository root after installing
`services/quant-api[dev]`:

```sh
python scripts/run_market_paper_acceptance.py --output /tmp/market-paper-offline.json
python scripts/run_market_paper_acceptance.py --live-data --output /tmp/market-paper-public.json
```

Both commands always create an isolated temporary paper account and restore the
previous `OSIRIS_PAPER_EXECUTION_DB` setting. They never initialize or modify an
operator's configured account. Each writes an outcome JSON report and SHA-256
sidecar, including failures, actual market inputs, normalized candle digest,
strategy/risk decisions, order outcomes, reconciliation, base commit and dirty
working-tree flag plus SHA-256 hashes of the exercised source files. A failed assertion or fetch returns a nonzero exit status.
CI runs the offline command and retains both artifacts.

The offline run replaces only the public HTTP response bytes. Real adapter
parsing, chronological and completeness checks, strategy, risk gate, order
execution and Decimal accounting all execute. It checks an exact two-unit
round trip including both fees, replay without duplicate fills, kill switch,
notional limit, malformed feed rejection, reconciliation and recovery in a
separate Python process. Synthetic prices are fixed; UTC minute labels follow
the current runner clock, with a library-only test clock for the second minute.

The public-data run performs an actual read-only Coinbase HTTP request. Its
strategy outcome is retained honestly: SELL with no existing inventory or HOLD
may result in no fill. A passing connected-data check therefore proves an
observed evaluation and reconciled paper ledger, while its recorded `fill_count`
and order status establish whether a paper fill occurred on that run. It does
not force a trade or substitute generated data when public data is unavailable.

For operator use, configure `OSIRIS_PAPER_EXECUTION_DB`, initialize the account
through `/paper/execution/initialize`, and POST `/paper/pipeline/run` with decimal
strings (for example `{"symbol":"BTC-USD","quantity":"0.001"}`). The HTTP
request cannot supply prices, signals, risk approval, limits or a clock.
`/paper/pipeline/status` reads the execution ledger used by the connected path.
Retries use a stable strategy/symbol/closed-candle decision ID; changing the
inputs for the same decision raises a conflict instead of creating another fill.

These checks establish local paper pipeline behavior. They do not accept the
separate Rust/P2P cross-host release gates, provide independent signoff, validate
strategy profitability or authorize live execution.
