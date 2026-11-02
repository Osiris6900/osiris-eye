# OSIRIS EYE — Data Contracts and Validation Plan

## Canonical observation envelope

Every market, macro, news, weather or alternative-data observation should preserve:
- source_id, source_name, license_id
- instrument_id or entity_id and venue/region
- event_time_utc, published_time_utc, available_time_utc, ingested_time_utc
- source_timezone and original_timestamp
- value, unit, currency, field_name
- revision/vintage, sequence number and source record ID
- quality flags: valid, stale, estimated, corrected, missing, delayed
- raw object reference and normalized schema version

Do not overwrite raw source records. Corrections/revisions create new versioned observations. Research features must be joined on available_time, not merely event_time, to prevent look-ahead leakage.

## Feed quality gates

- Timestamp parse, timezone and daylight-saving validation.
- Duplicate/out-of-order sequence detection.
- Missing interval and stale quote checks by venue/session.
- Bid/ask sanity, crossed/locked market flags and zero/negative price checks where not valid.
- Corporate-action, futures-roll and contract-specification checks.
- Unit/currency normalization and explicit FX conversion timestamps.
- Source entitlement/license and retention checks.
- Reconciliation between independent sources where available.

Critical feed failure returns UNKNOWN and blocks new simulated risk. Do not fill missing prices with zero or forward-fill through market closures without an explicit, reviewed policy.

## Model and experiment record

Record experiment_id, hypothesis, owner, code commit, environment/package lock, data snapshot IDs, point-in-time universe, feature definitions, train/validation/test boundaries, random seed, model parameters, benchmark, costs, metrics, calibration, subgroup/regime results, approval status and reviewer.

## Alternative data controls

Weather and geospatial observations require location mapping, resolution, issue/publication time, forecast-vs-observation distinction and revision history. Lunar/solar observations require a documented astronomical/space-weather source and exact event timestamp. Keep these features research-only until they show incremental out-of-sample value after multiple-testing controls and realistic costs.

## Security and permissions

- Separate read-only research credentials from any future execution credentials.
- No secrets in Git, notebooks, logs or model prompts.
- Agent tools use explicit allowlists and least privilege.
- Human approval for model promotion, risk-limit changes and any future execution enablement.
- Audit all data access, model changes, approvals, simulated orders and risk rejections.
