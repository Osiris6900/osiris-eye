# Ghost, keyless data and optional paper broker

Settings and Live Trading now expose **Ghost / keyless** and **Optional paper
broker** choices backed by the authenticated control plane.

Ghost is the default: no broker is selected and saving/loading preferences
never contacts a broker. Choose Coinbase public crypto, Binance public crypto,
Kraken USD crypto, or Stooq daily equity data without vendor API credentials. The saved provider
becomes the default for newly opened market charts, with native symbols and a
30-day window for daily Stooq history. Public source availability and permitted
use still apply. There is no invented coverage or automatic fallback to
synthetic prices.

See [keyless forward research](KEYLESS_FORWARD_RESEARCH.md) for actual public
paper-pipeline exercises, the explicitly selected Kraken USD path and a frozen,
reproducible genuine quote archive with fitted-model and cost-sensitivity checks.

Ghost is a no-broker application profile, not a promise of anonymous Internet
access. Public HTTPS providers can observe requests. Existing P2P node identity,
encryption, authentication and relay deployment controls remain necessary;
this setting never removes identity keys or reconfigures running nodes.

## Optional Alpaca PAPER access

An operator or security administrator uses the existing authenticator flow
to start an MFA session, loads connection status, chooses Optional paper broker,
and applies the preferences. Selection alone does not connect, initialize an
account, change risk budgets, submit orders or activate a trading strategy.

Server operators may inject these separate **paper-account** environment
variables through their normal secret manager:

- `OSIRIS_ALPACA_PAPER_ENABLED=1`
- `OSIRIS_ALPACA_PAPER_API_KEY`
- `OSIRIS_ALPACA_PAPER_API_SECRET`

Do not put secret values in Git, browser storage, setup drafts or chat.
Readiness is explicitly disabled, credentials-required or configured-unverified.
It is never inferred to mean connected. Existing market-data credentials are
not silently reused for brokerage access.

**Test paper broker connection** performs only an authenticated
`GET https://paper-api.alpaca.markets/v2/account`. It requires an ACTIVE,
unblocked paper account and returns a timestamped, read-only result. It exposes
no account ID, account number, balances, upstream body or credentials. Redirects
are refused by the existing bounded HTTPS transport. It never calls an order,
cancel or transfer endpoint. The UI expires its confirmation after 60 seconds
and clears it on changed choices, identity, failure or reload. No broker or
licensed feed credentials were available in the implementation environment;
actual owner-account connectivity remains unverified until this explicit test.

The existing Alpaca paper order/reconciliation adapter remains separately
controlled. This connection test is not execution activation and does not
route the public-data paper pipeline into a brokerage account. Future live
broker/risk preferences remain a separate browser-only draft.

## API and persistence

- `GET /control-plane/connections`: authorized status/catalog read, no upstream calls.
- `PUT /control-plane/connections`: MFA operations role; exactly
  `{"preferences":{"mode":"ghost","data_provider":"coinbase-public","broker":"none"},"expected_version":0}`.
  Read the current version before applying a change.
- `POST /control-plane/connections/test`: MFA operations role; exactly
  `{"expected_version":1}`. Requires saved broker mode, enabled server configuration
  and the matching version. Only the fixed paper account endpoint is contacted.

Preferences are persisted in the existing control database and survive process
restart. Optimistic version checks reject concurrent stale writes/probes with
409. The bounded broker probe holds the database transaction until completion,
so a concurrent switch to Ghost cannot complete while that probe is active.
Response bodies are scrubbed and marked `Cache-Control: no-store`.
Unknown/live brokers, custom URLs, keyed data providers, supplied credentials
and order fields are rejected. Bearer reads do not authorize changes; a revoked
or expired session cannot probe a broker. Viewer/analyst MFA does not grant
operator privileges.

All order paths keep their independent risk controls. No release acceptance
gate is closed, no source licence is certified, and live execution remains
disabled by this feature.

## Verification

```bash
.venv/bin/python -m pytest -q services/quant-api/tests/test_connections.py
npm --prefix apps/web test
npm --prefix apps/web run build
OSIRIS_TEST_PYTHON="$PWD/.venv/bin/python" bash scripts/run_platform20_browser.sh
```

Backend regressions cover fixed-origin GET-only probes, keyless defaults,
restart persistence, stale versions, concurrent switching, MFA/RBAC, upstream
redaction, unavailable accounts and rejected live/secret/URL payloads. UI tests
cover actual save/test requests, no automatic broker access, stale/expired
results, identity changes and saved feed defaults. Chromium exercises real
temporary HTTPS/SQLite services and MFA, using offline data and no brokerage
credentials; it does not certify independent hosts, feeds or an actual broker
account.
