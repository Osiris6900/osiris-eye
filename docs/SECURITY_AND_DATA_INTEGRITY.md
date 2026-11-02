# API security and immutable research data

## API token

Set `OSIRIS_API_TOKEN` to a high-entropy secret and send it on every API request:

```http
Authorization: Bearer <OSIRIS_API_TOKEN>
```

When `OSIRIS_ENV=production` (or `prod`), protected routes fail closed with HTTP 503 if the token is missing. The liveness endpoint `/health` remains public. In development, authentication is disabled when no token is configured; do not expose that mode to the public internet.

Store the token in your deployment secret manager, never in source control, frontend code, logs, or a committed `.env` file. Rotate it if exposed. This single-token gate is a deployment baseline, not a replacement for per-user identity, RBAC, OAuth, or managed secret rotation.

## Immutable observations

The SQLite observations table rejects updates and deletes. Re-submitting the same `(dataset, source, record_key)` returns a validation error instead of overwriting historical data. Corrections must be ingested as a new record with a new `record_key`, retaining the original event and availability timestamps. The store is still a local research database, not independently administered WORM storage; production deployments should export records and audit checkpoints to separately controlled immutable storage.

Every observation must satisfy `available_at >= event_time`. Point-in-time reads continue to filter on both timestamps.

## Scope

These controls harden the API boundary and local research store. They do not create broker connectivity, licensed market-data access, signed audit provenance, or live execution. Live trading must remain disabled until separately reviewed operational controls and venue/broker integrations exist.
