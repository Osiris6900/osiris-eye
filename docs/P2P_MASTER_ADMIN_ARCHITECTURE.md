# OSIRIS EYE — extensible Founder Admin architecture (design contract)

The existing `apps/web` is a trading dashboard, **not** an authenticated P2P administration console. Keep the P2P admin application separately deployable. Never connect a visual-only prototype to real node controls.

## Preview the prototype

From the repository root, serve the standalone page locally:

```bash
python -m http.server 8081 --bind 127.0.0.1 --directory apps/p2p-admin
```

Open `http://127.0.0.1:8081/`. The page makes no network API calls. Navigation
reveals prototype modules; only Feature Manager and System Settings edit a
draft kept in page memory. Those edits survive module navigation, but refresh
restores the defaults. Export draft JSON downloads the current version, brand
and feature flags; Reset draft restores defaults. Exported JSON is a design
sample and is not an accepted node configuration or an authorization token.

## Modules and future changes

Use a versioned module registry: `id`, `title`, `version`, `required_permission`, `feature_flag`, `read_only_by_default`. Initial modules: overview, owned-nodes, peer-health, cryptography-policy, releases, security-audit, feature-flags, billing, settings. New modules must be independently removable; version and validate all config changes. Reject unknown privileged operations by default.

## Authority boundary

Founder controls only organization-owned nodes, software releases, commercial services, and policy proposals. There is no global kill switch, universal decryption key, or authority to force third-party P2P nodes offline. Protocol upgrades are voluntary for independent operators.

## Production authorization

Server-side authentication is mandatory before adding any mutating API. Use hardware-backed MFA for founders, scoped RBAC permissions, immutable audit trails, CSRF defenses, rate limits, change previews, staged rollouts and rollback. Do not store keys or access tokens in browser local storage. Never let a feature flag bypass authorization.

## Safe configuration update

1. Authenticate and authorize the action server-side.
2. Validate proposed schema version and permission.
3. Preview diff and require confirmation for destructive operations.
4. Commit revision with actor, timestamp and reason to append-only audit log.
5. Stage to test nodes; check health; promote only on success.
6. Support rollback to previous signed revision.

## Initial milestone

Create an isolated visual-only admin UI with module navigation, feature flags and editable **local draft** settings. Label it clearly as not connected to a live network. Do not imply functional remote node controls until authenticated APIs exist.
