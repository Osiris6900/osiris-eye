# T09 — Market data licence and redistribution rights

**T09 remains Remaining. No vendor licence or redistribution rights have been independently verified.**

The existing `authorized_feeds.py` adapter registry contains a synthetic fixture only; a code-level admission flag is not a vendor contract.

## Required evidence before real vendor admission

- Identify legal contracting entity, vendor, contract reference, start and expiration dates, authorized markets/instruments, geographic/user limits, and permitted research/paper/live uses.
- Separate internal analysis, derived-data publication and third-party redistribution rights. Do not infer redistribution permission from API accessibility.
- Retain the original vendor agreement privately with its SHA-256, provenance and named legal/compliance reviewer. Record amendment and revocation dates and the vendor's written confirmation.
- Verify licence authenticity and current entitlements independently, then update the code-reviewed registry and enforce suspension on expiry or revocation.
- No credentials or confidential contract documents should be committed to the public repository.

`python scripts/check_vendor_entitlement_evidence.py path/to/record.json --as-of 2026-11-11` checks *metadata shape and dates only*. It never fetches contracts, authenticates vendors, validates a signature, admits feeds or accepts T09. Operator review and explicit signoff are still required. **Live trading remains disabled.**
