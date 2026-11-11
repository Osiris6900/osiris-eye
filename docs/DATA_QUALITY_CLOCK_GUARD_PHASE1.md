# Data Quality and Clock Guard — Initial implementation

Issues #377 and #378. `scripts/market_data_quality_clock_guard.py` validates bounded offline observations, rejects malformed timestamps/prices, and flags duplicate IDs, stale timestamps and future clock skew. Time is supplied by the caller and **not independently synchronized**. Passing does not establish feed entitlement, provider authenticity, fair price, trading eligibility or independent time provenance.

This is a research-only first phase, not wired into the production Rust receiver or paper-order gate. Next: integrate authenticated ingestion, monotonic sequence tracking, real NTP/PTP monitoring, source-specific thresholds, cross-provider conflicts and fail-closed pipeline tests. Live trading stays disabled.

Run: `python -m unittest discover -s scripts -p 'test_market_data_quality_clock_guard.py'`.
