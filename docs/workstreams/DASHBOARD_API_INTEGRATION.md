# Dashboard API integration

The research workspace uses backend snapshots instead of illustrative prices,
signals, performance curves, headlines, P&L and unconditional Ready/Connected
labels. This work builds on integration PR #408 and addresses dashboard work
in #409/#412. It does not accept any release gate or replace independent review.

## Data and identity

- `GET /control-plane/dashboard` accepts existing configured role tokens or MFA
  sessions. It reuses the Admin Centre's canonical paper accounting, risk,
  registered-feed and network projections, the paper pipeline's historical
  decision, and the existing stored Rust peer-metrics collection. A viewer can
  read health, but the server never reads or returns paper/research projections
  for that role. The endpoint does not execute a strategy, collect peer metrics,
  initialize a paper account or place an order. Existing control-plane identity
  storage and request-observability behavior still apply.
- `GET /control-plane/market/bars` requires research read permission and reuses
  the existing allowlisted market gateway. Requests have an explicit provider,
  native symbol, asset class and bounded window (at most 366 days). This endpoint
  supports the same public/synthetic/sandbox modes as the gateway, with no new
  adapter, URL, vendor credential, entitlement or live-execution permission.
  Both responses use `Cache-Control: no-store` and the existing secret-hygiene
  scanner, including the supplied session token.
- Canonical cash, positions and fills require successful reconciliation. Marked
  equity, unrealized P&L and market value additionally require fresh marks.
  Legacy balances are never silently substituted for the execution account.
  Values retain decimal precision and are labelled paper accounting.
- UI authorization stays in memory. Requests are aborted on identity changes or
  unmount; old replies cannot populate a new session. Dashboard polling is
  sequential, timed out at 12 seconds, and refreshed after 30 seconds. Values
  disappear on refresh/error/disconnect; snapshot timestamps are checked and
  expired data is withheld. No numeric fallback or simulated value replaces an
  unavailable source.
- Historical candles are fetched on explicit request, labelled with source,
  mode, entitlement limitation and UTC observation/availability times, and
  validated for OHLC, finite numbers, symbol, window and ordering. The chart
  uses the latest 300 returned bars and the table the latest 20. It does not
  invent a sampling interval or claim that a historical close is a live quote.
  Changing query or identity clears the old chart. The existing direct Binance
  WebSocket tape remains a separate, freshness-gated source on Live Markets.
- Peer enrollment, one local node's connected count and stored per-peer receipt
  counters have distinct labels. Per-peer metrics older than 60 seconds are
  withheld. These local reports do not prove independent-host provenance.

## Navigation and remaining source dependencies

Dashboard, Live Markets, Global Markets, AI Signals, Paper Trading and Risk
Management have specific API-backed views. Strategy Lab, Backtesting, Reports,
MFA Settings and the disabled live-setup draft retain the PR #408 implementation.
The existing caller-input Market Mechanics Lab remains accessible in Strategy
Lab, with its separate legacy API-token control and explicit synthetic examples.
The Admin Control Centre is linked explicitly.

Options Chain, Market Regime and News & Sentiment have explicit unavailable
states. The current backend has no connected chain snapshots with verified
expiry/strike quotes, timestamped validated regime classification, or licensed
headline/sentiment feed for these views. Adapter capability is not proof of
such data. No chain/IV surface, profitable strategy claim, headline or sentiment
score is invented. Connecting and accepting these sources remains work.

## Reproducible checks

```bash
python -m pytest -q services/quant-api/tests/test_control_plane_dashboard.py
python -m pytest -q services/quant-api/tests tests/test_ultra_fast_feed.py
cd apps/web && npm test && npm run build
# From the repository root, with the installed project interpreter:
OSIRIS_TEST_PYTHON=/path/to/project/.venv/bin/python bash scripts/run_platform20_browser.sh
```

The browser fixture uses disposable TLS identities, an offline synthetic candle
adapter and a genuine temporary canonical paper ledger. It tests cash/marked
P&L, source-labelled chart data, dedicated navigation, corrupted-ledger
withholding, MFA, independent-role research review and a separate HTTPS worker.
Only sanitized result JSON and a screenshot are retained; no real provider or
broker is needed. This is local engineering evidence, not external independent
acceptance, vendor licensing, production DR or authorization to merge #408.

Live trading remains disabled. Vendor rights, independent security/model/risk
review, sustained independent-host P2P/HA/DR and owner release signoff remain
outstanding. Release-gate inventory and checkboxes are unchanged.
