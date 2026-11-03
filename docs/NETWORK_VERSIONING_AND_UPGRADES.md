# OSIRIS EYE Network Versioning and Upgrade Policy

## Purpose

The network must be changeable without requiring a full platform rewrite. Network protocol, node software, API contracts, configuration schema, and UI releases are versioned independently. A change to one does not automatically require changing every other component.

## Version identifiers

The P2P protocol uses semantic versions: `MAJOR.MINOR.PATCH`.

- **MAJOR**: incompatible wire format, identity, authentication, or consensus/security semantics. Requires an explicit migration plan.
- **MINOR**: backward-compatible capability addition.
- **PATCH**: compatible bug or security fix that does not change the protocol contract.

The enrollment identity protocol currently identifies itself as `osiris-eye:p2p:enroll:v1`. It is an enrollment protocol identifier, not evidence that transport or peer discovery is live.

Every peer handshake must eventually advertise:
- protocol family and version;
- supported version range;
- node software version;
- supported capabilities;
- network/environment identifier.

A peer must not silently downgrade to an insecure protocol. Unknown major versions are rejected by default. Capability negotiation must use an explicit allowlist.

## Upgrade lifecycle

1. **Prepare** — publish a signed release manifest, checksums, compatibility range, migration notes, and rollback target.
2. **Validate** — verify signature and checksum; run unit, integration, protocol-compatibility, security, and migration tests.
3. **Canary** — deploy to an owner-controlled test node or isolated staging network first.
4. **Roll out** — upgrade a small trusted-node cohort, observe health and protocol errors, then expand in controlled batches.
5. **Commit** — mark the release stable only after health gates pass.
6. **Rollback** — stop rollout and restore the last known-good binary/configuration if health gates fail. Data migrations must be backward-compatible or have a tested restore path.

Automatic upgrades are disabled by default. Public peers cannot approve releases or alter owner policy. The Owner-Controlled Core retains the authority to approve trusted-node upgrades.

## Configuration rules

- Keep non-secret defaults in version-controlled example files such as `config/network.example.yaml`.
- Keep credentials, private keys, signing keys, and deployment secrets in a secret manager or protected runtime environment, never in Git.
- Validate configuration against a versioned schema before startup.
- Reject unknown security-critical fields rather than guessing their meaning.
- Keep a copy of the last-known-good configuration and record every applied change in an audit log.
- Configuration changes cannot enable live trading or bypass the independent risk controls.

## Compatibility policy

- Support the current major protocol and a documented migration window for the immediately previous major only when a security review explicitly approves it.
- Never bridge incompatible protocol versions by silently translating identity, permissions, signatures, or risk policy.
- Mixed-version operation must be tested with real compatibility fixtures before rollout.
- Emergency security revocation can shorten a compatibility window; document the reason and affected versions.

## Repository boundaries

Current code remains in its existing repository layout. Do not move or rename working modules solely to make the tree look cleaner. New modules should be introduced behind stable interfaces:

| Concern | Current / intended home |
|---|---|
| Web dashboard | `apps/web/` |
| Python API and current P2P identity foundation | `services/quant-api/` |
| Rust core components | `crates/` |
| Example runtime configuration | `config/` |
| Architecture and operator procedures | `docs/` |
| Automated tests | `tests/` and service-local test directories |

A future dedicated P2P node service or additional language runtime should be introduced as a separate module only when its operational need and deployment model are established. Avoid a premature rewrite or unnecessary multi-language split.

## Safety and current implementation status

This document defines the intended upgrade policy; it does not implement a release orchestrator, signed updater, rollback controller, live transport, or distributed registry. The current P2P work is still an identity/enrollment foundation. Transport, discovery, shared durable storage, sandboxed compute, and production upgrade automation remain future milestones.

Real-money live execution remains disabled. No network peer may receive broker credentials, exchange secrets, private keys, owner-control privileges, or permission to bypass risk checks.
