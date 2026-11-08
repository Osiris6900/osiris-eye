# Operator control centre and deployment acceptance

Tracks #180, #181 and #182. This is a target acceptance plan, not evidence of deployment.

- [ ] Authenticated read-only views for peers, relay health, data freshness, paper orders and risk.
- [ ] Role-based authorization for operator actions; never expose private keys or credentials.
- [ ] Audit every privileged action with actor, UTC timestamp, request ID and outcome.
- [ ] Add metrics, alerts, traces, backups, restore tests and documented incident response.
- [ ] Stage repeatable infrastructure configuration and verify secret handling and deployment rollback.
- [ ] Pass cross-module end-to-end acceptance, load tests and disaster-recovery exercise before release.

No public endpoint, production uptime or live trading is claimed.
