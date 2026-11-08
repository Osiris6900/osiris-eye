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

- `peer_telemetry: "unavailable_in_quant_api_process"` — the quant API has no
  live P2P peer table; nothing is fabricated.
- Unrealized P&L is `null` without external marks (`marks_available: false`).
- Paper data comes from the durable SQLite ledger (`synthetic-paper-only`).
- Audit and alerts are bounded in-memory evidence, not a compliance store.

## Reproduce

```bash
pip install -e "services/quant-api[dev]"
cd services/quant-api && pytest -q tests/test_admin_centre_180.py tests/test_admin_api_180.py
python scripts/test_check_operator_permissions.py   # offline contract guard
cd apps/web && npm ci && npm test && npm run build
```
