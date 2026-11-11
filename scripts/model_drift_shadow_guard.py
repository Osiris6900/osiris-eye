"""Fail-closed, paper-only model drift advisory using reference/current histograms.

Counts must be externally validated as comparable, non-overlapping bins. This
is an offline diagnostic, not a model validation or execution control.
"""
from __future__ import annotations
import math

MAX_TOTAL_COUNT = 2**53 - 1


def population_stability_index(reference: object, current: object,
                               *, warning_threshold: float = 0.2) -> dict:
    try:
        valid_threshold = (type(warning_threshold) in (int, float)
                           and math.isfinite(warning_threshold) and warning_threshold > 0)
    except OverflowError:
        valid_threshold = False
    if (not isinstance(reference, list) or not isinstance(current, list)
            or not 2 <= len(reference) <= 100 or len(reference) != len(current)
            or not valid_threshold):
        return {"status": "rejected", "reason": "invalid_histogram_configuration",
                "live_trading_authorized": False}
    for bins in (reference, current):
        if (any(type(n) is not int or n < 0 for n in bins)
                or not 100 <= sum(bins) <= MAX_TOTAL_COUNT):
            return {"status": "rejected", "reason": "invalid_histogram_counts",
                    "live_trading_authorized": False}
    reference_total, current_total = sum(reference), sum(current)
    # Small additive smoothing prevents log(0); exact PSI depends on bin design.
    k = len(reference)
    psi = 0.0
    for a, b in zip(reference, current):
        p = (a + 0.5) / (reference_total + 0.5 * k)
        q = (b + 0.5) / (current_total + 0.5 * k)
        psi += (q - p) * math.log(q / p)
    return {"status": "drift_advisory", "psi": round(psi, 8),
            "warning": psi >= warning_threshold, "sample_size_reference": reference_total,
            "sample_size_current": current_total, "bins_verified": False,
            "model_quarantined": False, "shadow_only": True,
            "live_trading_authorized": False}
