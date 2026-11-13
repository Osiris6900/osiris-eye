# Admin control centre (issue #180)

Authenticated, read-only operations dashboard for the quant API plus two
bounded operational controls. **No live trading exists**: the dangerous
actions below are denied for every role, including `security_admin`.

## Architecture

```
apps/web/admin.html ──▶ src/admin.tsx ──▶ AdminCentre.tsx ──/api/*──▶ FastAPI /admin/*
   (token in memory only;                          ▲ admin_routes.py
    scrubForDisplay defense-in-depth)              │  ├─ admin_centre.py  (canonical RBAC, audit,
                                                   │  │                     secrets scan, snapshots)
                                                   │  ├─ authorized_feeds.py (adapter suspension)
                                                   │  ├─ durable_paper_ledger.py (read-only snapshot)
                                                   │  └─ p2p_ingestion ENABLE_ENV (kill switch)
                                    scripts/check_operator_permissions.py
                                    (offline guard; imports the SAME tables)
```

`scripts/check_operator_permissions.py` re-exports `PERMISSIONS`, `DANGEROUS`
and `authorize` from `osiris_api.admin_centre`, so the offline contract guard
and the live API can never drift apart.

## RBAC contract

| Role | Permissions |
|---|---|
| `viewer` | `read_health`, `read_audit` |
| `analyst` | + `read_research` |
| `operator` | + `ack_alert` |
| `risk_officer` | + `activate_kill_switch` |
| `security_admin` | + `revoke_peer` (no kill switch) |

`DANGEROUS = {place_live_order, export_private_key, disable_risk_gate,
erase_audit}` is denied for **every** role, authenticated or not.
`authorize()` fails closed: unauthenticated requests, unknown roles and
unknown actions all deny.

## Authentication

Admin endpoints use per-role bearer tokens from one environment variable
(the outer `OSIRIS_API_TOKEN` middleware exempts `/admin/`; the admin router
is the single fail-closed gate):

```bash
export OSIRIS_ADMIN_TOKENS='{"viewer":"…","operator":"…","risk_officer":"…"}'
```

Malformed JSON, unknown roles, tokens shorter than 8 chars or a token shared
between roles all fail closed: every `/admin/*` route then answers
`503 admin_not_configured`. Missing/unknown token → `401
admin_authentication_required`; authenticated role without the permission →
`403 admin_permission_denied`. Every decision is written to the audit log.

## Endpoints (all response schemas versioned)

| Route | Permission | Schema |
|---|---|---|
| `GET /admin/overview` | read_health | `osiris.admin_overview.v1` |
| `GET /admin/monitoring` | read_health; paper block also requires read_research | `osiris.admin_monitoring.v1` |
| `GET /admin/network/status` | read_health | `osiris.admin_network_status.v1` |
| `GET /admin/feeds/status` | read_health | `osiris.admin_feeds_status.v1` |
| `GET /admin/paper/status` | read_research | `osiris.admin_paper_status.v1` |
| `GET /admin/risk/status` | read_research | `osiris.admin_risk_status.v1` |
| `GET /admin/alerts` | read_health | `osiris.admin_alert_list.v1` |
| `POST /admin/alerts/{id}/ack` | ack_alert | `osiris.admin_alert.v1` |
| `POST /admin/network/kill-switch` `{enabled: bool}` | activate_kill_switch | `osiris.admin_kill_switch.v1` |
| `POST /admin/feeds/{adapter}/revoke` | revoke_peer | `osiris.admin_feed_revocation.v1` |
| `GET /admin/audit` | read_audit | `osiris.admin_audit_log.v1` |

## Operational controls (real effects, audited)

- **Kill switch** flips `OSIRIS_P2P_INGEST_ENABLED` in-process — the same gate
  consulted by `p2p_ingestion.validate_p2p_observation` and
  `ObservationOutbox.drain`.
- **Feed revocation** suspends a registered adapter in `authorized_feeds`;
  `ingest_adapter_ticks` then raises `adapter_suspended` **before** `fetch()`.
  The set is process-local and resets on restart (`"durable": false` in the
  response); durable cross-node revocation remains P2P-reliability scope.

## Secrets hygiene

Defense in depth, both layers tested:

1. **API**: every admin response passes `scan_payload` before leaving the
   process. Any key matching a credential marker (`token`, `secret`,
   `password`, `private_key`, `authorization`, `credential`, `api_key`,
   `session` …) at any depth, or any string containing a configured admin
   token, turns the response into `500 admin_response_redacted` and records a
   denial in the audit log.
2. **Web**: the token lives only in React state (never localStorage/cookies/
   URL), the input is `type="password" autoComplete="off"`, and
   `scrubForDisplay` drops credential-shaped keys before rendering.

## Alerts

`evaluate_alerts` mirrors current risk flags (ingestion disabled, stale feed
quality review, outbox backlog > 100, suspended adapter, uninitialized paper
ledger) into an idempotent in-memory registry (`kind:key`). `operator` and
above can `ack`; acknowledgements record role and timestamp.

## Honesty boundaries

- Peer health reads the local identity registry (`OSIRIS_P2P_DB_PATH`, otherwise
  `./data/osiris-p2p.sqlite3`) only when that file already exists. A missing file
  stays `peer_health.status: unavailable` with `peer_count: null` and is not created.
  When the `p2p_nodes` table can be read, status is `enrolled_registry`: counts and
  node ids only (no public keys, no filesystem path). `liveness` is `not_observed`.
  `last_seen_at` is shown as the last enrollment or re-enrollment, not a live session.
  There is still no transport peer table. `peer_telemetry` stays
  `unavailable_in_quant_api_process` until that registry read succeeds.
- Outbox message status, when a database file already exists, reports `pending`,
  `rejected` and `delivered` counts. `delivered` means local node handoff, not a
  remote receipt. Rejection reasons that are not short codes are returned as
  `unclassified` so raw text cannot leak. A missing outbox stays `configured: false`
  and does not invent zeros.
- Paper status adds `recent_fills` (symbol, side, quantity, price, fee, P&L,
  time; idempotency keys omitted), `cost_basis_gross` from ledger positions, and
  `market_value` only when every position has a mark. `open_orders` is `null`
  with reason `not_stored`.
- Alert history uses the local SQLite store when it is readable: `storage: sqlite`
  and `durable: true`. A missing file is an empty history and is not created
  until an alert is recorded. An unreadable or foreign file fails closed
  (`storage: unavailable`). The dashboard prints only those known storage
  states. Acknowledgement stays on the POST route; the dashboard does not send it.
- `GET /admin/monitoring` is a read-only operational panel. `read_health`
  returns the peer-health summary and one local node status. The paper block
  is included only when the same caller also has `read_research`; otherwise
  its status is `permission_denied` and it contains no balances, symbols, or
  order ids. A missing registry or an unreadable local node stays null and is
  not replaced with zeros. The panel does not acknowledge alerts, flip the
  kill switch, revoke feeds, or place orders.
- The React dashboard is read-only: it does not call kill-switch, feed revoke,
  or alert ack. Those POST routes remain server-side and RBAC-gated.
- Unrealized P&L is `null` without external marks (`marks_available: false`).
- Paper data comes from the durable SQLite ledger (`synthetic-paper-only`).
- Audit is bounded in-memory evidence, not a compliance store. Alert history
  is the local SQLite file described above, not a compliance archive.

## Reproduce

```bash
pip install -e "services/quant-api[dev]"
cd services/quant-api && pytest -q tests/test_admin_centre_180.py tests/test_admin_api_180.py
python scripts/test_check_operator_permissions.py   # offline contract guard
cd apps/web && npm ci && npm test && npm run build
```
