# I4 dashboard backend integration

Issue #363. This is a single-host engineering check. It does not close the issue, tick a release gate, or authorize live trading.

## What the workspace shows

| Surface | Source | When the source is missing |
| --- | --- | --- |
| Paper cash, P&L, positions, fills | Authenticated `GET /control-plane/dashboard` canonical account | `Unavailable` or “Verified paper accounting unavailable.” A denied role sees “Access denied for this role.” |
| Risk flags and exposure | Same dashboard snapshot | Exposure stays `Unavailable` without a reconciled mark. |
| Price chart | Authenticated `GET /control-plane/market/bars` | “No price history loaded.” A returned fixture is labelled with its source mode, including `SYNTHETIC`. |
| Options and news | Authenticated `GET /control-plane/market/views/{options,news}` | “Options chain unavailable.” / “News and sentiment unavailable.” No sample quotes are substituted. |
| Research signal | Pipeline block on the same dashboard snapshot | “No recorded research signal available.” |
| Live crypto tape | Binance public websocket, separate from the backend ledger | An em dash and “Waiting for fresh tick.” No last-known price is kept. |
| Research sentiment panel | `GET /api/v1/intelligence/dashboard/intelligence-overview` | An empty buffer is “Research sentiment unavailable.” A zero is not rendered as a reading. The panel is not mounted in the workspace because that overview is process-local and not role-gated. |
| Live Trading | Browser draft only (`docs/LIVE_TRADING_SETUP_DRAFT.md`) | Activation stays disabled. The form does not submit an order. |

## Browser check

`scripts/run_platform20_browser.sh` now also:

- opens Dashboard, AI Signals, Paper Trading, Options Chain, News & Sentiment, and Live Trading before any token and rejects known demo figures and canonical balances;
- connects a `viewer` token, expects research access to be denied, and checks that disconnect removes that denial;
- keeps “Activate live trading” disabled.

The fixture, API, Vite server, and Chromium all run on one host. The chart, option, and news rows in that run are labelled synthetic offline fixtures. They are not vendor entitlement evidence.

## BLOCKED (external/human)

- Owner-approved vendor credentials and redistribution rights for production charts, options, and news. Unblock with the licence and a non-synthetic source record.
- Independent reviewer acceptance of the browser run on a machine they control. Unblock with that reviewer’s retained artifact, not this agent’s local log.
- Role-gated sentiment with a durable source. Unblock by requiring the same dashboard authorization on the intelligence overview before the panel is placed in the workspace.
