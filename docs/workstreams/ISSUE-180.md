# Admin control centre

Tracking issue: #180

## Implementation scope

Build authenticated read-only peer health, feed freshness, paper-order state
and risk dashboard; add RBAC and audit tests.

## Status: implemented (evidence below)

- `services/quant-api/src/osiris_api/admin_centre.py` — canonical fail-closed
  RBAC (`PERMISSIONS`/`DANGEROUS`/`authorize`), per-role bearer tokens from
  `OSIRIS_ADMIN_TOKENS`, bounded in-memory audit log, `scan_payload` secrets
  hygiene, alert registry and read-only network/feeds/paper/risk snapshots.
- `services/quant-api/src/osiris_api/admin_routes.py` — `/admin/*` APIRouter;
  503/401/403 fail-closed semantics, every decision audited, every response
  redaction-scanned.
- `services/quant-api/src/osiris_api/authorized_feeds.py` — runtime adapter
  suspension enforced before `fetch()`.
- `services/quant-api/src/osiris_api/main.py` — router mounted; outer bearer
  middleware exempts `/admin/` so the admin RBAC is the single gate.
- `scripts/check_operator_permissions.py` — now a thin wrapper over the same
  canonical tables (offline guard and API cannot drift).
- `apps/web/admin.html` + `src/admin.tsx` + `src/AdminCentre.tsx` —
  authenticated dashboard; token in memory only, credential scrubbing,
  per-section 403 handling, ack/kill-switch/revoke controls.
- `docs/ADMIN_CONTROL_CENTRE.md` — architecture, contract, reproduce steps.

## Acceptance evidence

- No credentials in frontend: `AdminCentre.test.tsx` (token never persisted,
  secret-shaped fields scrubbed); API redaction test proves a planted token
  turns the response into `500 admin_response_redacted`.
- Access-denial tests: full role x endpoint matrix (5 roles x reads and
  controls) in `tests/test_admin_api_180.py`; dangerous actions denied for
  every role in `tests/test_admin_centre_180.py`.
- API contract: exact schema tags and response key sets asserted for every
  endpoint.
- 40 backend tests + 7 web tests; full quant-api suite and web suite green.

No live trading is enabled; kill-switch and revocation effects are real but
process-local (documented in `docs/ADMIN_CONTROL_CENTRE.md`).
