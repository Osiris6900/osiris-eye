# Nine Workstreams — Evidence Gate

Issues #363–#371 track dashboard, account consistency, P2P telemetry, network acceptance, receipt-to-order integration, market data, model/risk, paper realism and recovery/signoff.

`scripts/check_nine_workstreams_evidence.py` accepts a JSON object with `commit_sha` (40 lowercase hex) and `workstreams` containing **exactly** keys `dashboard`, `paper_account`, `p2p_telemetry`, `network_acceptance`, `receipt_to_order`, `market_data`, `model_risk`, `paper_realism`, `recovery_signoff`. Each value has `issue` (matching GitHub issue), `artifact` (relative local path), `sha256` (lowercase digest) and `reviewed` (boolean).

Run: `python scripts/check_nine_workstreams_evidence.py evidence-manifest.json --root evidence/`.

**This is only an artifact-presence/digest gate**, not a real acceptance or external-review attestation. It cannot prove a named reviewer performed a review, the commit matches the repository, data licensing, independent hosts, genuine test results or release readiness. An all-green result is `evidence_present_not_independently_verified`; live trading remains unauthorized. Integrate signed reviewer identity, verified CI artifacts, final commit binding and independent signoff before a release decision.
