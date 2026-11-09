# PR #265 — Broker sandbox integration: implementation and acceptance

## Implemented
- `paper_execution_gate.py` combines feed authentication/entitlement, approved model, timestamp freshness, emergency kill-switch and existing `risk.check_order` simulation limits.
- `test_paper_execution_gate.py` covers approval and independent fail-closed rejections.
- Existing `phase3_systems.paper_order_lifecycle` is a pure simulation helper, not a broker adapter.

## Remaining code
1. Build sandbox-only broker adapter interface with capability restrictions and explicit paper account configuration; no live credentials or routing.
2. Implement persisted order state machine (created, risk_approved, submitted_paper, acknowledged, partially_filled, filled, cancelled, rejected), guarded transitions and idempotent client order IDs.
3. Journal immutable request/response/fill events with correlation IDs and recovery/replay.
4. Reconcile broker sandbox order state, positions, balances, fees, partial fills and duplicate callbacks after disconnect/restart.
5. Use authenticated market observation output from PR #264, strategy/model approval, independent risk budget and halt policy; never trust client-supplied booleans as sole authorization.
6. Expose RBAC-protected Admin read-only status, auditable emergency halt and alerts.
7. Add E2E integration tests for disconnects, late fills, duplicate fills, risk-limit breach, stale feed, kill-switch and recovery.

## Acceptance
- Paper orders never bypass independent risk checks.
- Duplicate events and retries do not double-fill or double-spend.
- Unknown state or stale data blocks new simulated orders.
- Journals reconcile deterministically after restart.
- No production order submission or exchange credentials.
