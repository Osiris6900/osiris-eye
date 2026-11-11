# Advanced risk and signal advisory foundations

Tracks issues #388 (portfolio stress testing) and #391 (signal conflict resolver). The module `scripts/advanced_risk_signal_advisors.py` provides a bounded deterministic scenario P&L calculation using externally supplied position notionals and basis-point shocks, plus a conservative conflict-blocking policy for opposing signals.

These are **offline illustrative advisories**, not calibrated stress distributions, cross-asset covariance, validated signals, actual portfolio reconciliation or execution authorization. Inputs are caller-supplied and not independently verified. It never places orders or enables live trading.

Tests: `python -m unittest discover -s scripts -p 'test_advanced_risk_signal_advisors.py'`.

Next: connect canonical paper ledger, instrument mapping, conservative scenario catalog, independent risk budget and auditable signal provenance.
