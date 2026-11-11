# Ghost → P2P → verification → consensus → paper/live routing

This is a **dual-mode interface prototype**, not a connected trading system.

```text
Ghost public collector (API-keyless)
  -> authenticated P2P (persistent identity keys + Noise)
  -> source/provenance verification [not yet implemented end-to-end]
  -> independent-source price consistency [offline prototype PR #354]
  -> GhostTradeRequest
       PAPER -> existing paper risk gate -> paper authorization only
       LIVE  -> blocked preview, no broker call
```

The new `ghost_trade_router.py` has no broker imports, keys, network calls or order side effects. The `paper_authorized` result is a *simulation permission*, not a persisted order or fill. All source/peer/provenance booleans are caller claims and cannot be treated as independent verification until a trusted Rust P2P receive-path adapter is built.

**Live orders remain disabled regardless of inputs.** Production execution needs independent cryptography review, vendor licensing, validated data provenance, real broker permission, risk limits and kill switch, reconciliation, dry runs, and explicit release-owner signoff. Do not change the current paper-only release policy on the strength of this interface.
