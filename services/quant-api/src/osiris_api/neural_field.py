"""Bounded research-only sentiment aggregation; no market forecasts."""
from collections import deque
from datetime import datetime, timezone
from math import isfinite


class SentimentResearch:
    def __init__(self, capacity: int = 1000):
        if not 1 <= capacity <= 10000:
            raise ValueError("invalid capacity")
        self._signals = deque(maxlen=capacity)

    def ingest(self, source: str, asset: str, sentiment: float,
               intensity: float, confidence: float) -> dict:
        if not source or not asset or len(source) > 100 or len(asset) > 40:
            raise ValueError("invalid source or asset")
        if not all(isfinite(v) for v in (sentiment, intensity, confidence)):
            raise ValueError("non-finite signal")
        if not (-1 <= sentiment <= 1 and 0 <= intensity <= 1 and 0 <= confidence <= 1):
            raise ValueError("signal outside valid range")
        self._signals.append((asset.upper(), sentiment, intensity * confidence))
        return {"accepted": True, "mode": "research_only", "execution_enabled": False}

    def summary(self) -> dict:
        total_weight = sum(weight for _, _, weight in self._signals)
        weighted_sentiment = (
            sum(sentiment * weight for _, sentiment, weight in self._signals) / total_weight
            if total_weight else 0.0
        )
        return {
            "mode": "research_only", "source": "user_submitted_signals",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "buffered_signals": len(self._signals),
            "weighted_sentiment": round(weighted_sentiment, 4),
            "predictive_confidence": None, "forecast_verified": False,
            "execution_enabled": False,
        }
