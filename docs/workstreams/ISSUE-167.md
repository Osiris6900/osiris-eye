# Immutable CI action pinning

Tracking issue: #167

## Implementation scope
Inventory 59 mutable action references in eight workflows. Resolve each action to a reviewed immutable commit SHA, maintain human-readable version comments, and add a regression ratchet. Workflow edits require token with workflow permission.

## Acceptance evidence
Pinning inventory; no new mutable uses; workflow CI validated.

## Current status
Workstream branch initialized. This document is an execution plan, not evidence of completed implementation. No live trading is enabled.
