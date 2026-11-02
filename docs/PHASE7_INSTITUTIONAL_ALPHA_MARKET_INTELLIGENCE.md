# Phase 7 — Institutional Alpha & Market Intelligence

Phase 7 adds six research-only prototype systems. All inputs are caller-supplied; no external vendor feeds, live market subscriptions, autonomous trading, policy training, or enforcement are enabled.

## Systems and API routes

| System | API | Scope |
|---|---|---|
| Cross-Asset Alpha Discovery | `POST /research/alpha/cross-asset-discovery` | Pairwise aligned-return correlation across lags; hypotheses only. |
| Options Flow & Volatility Intelligence | `POST /research/options/flow-intelligence` | Signed call/put premium and volume aggregation plus caller-provided unusual flags. |
| Liquidity Forecasting | `POST /research/liquidity/forecast` | Recent-window spread, volume and volatility heuristic. Not calibrated. |
| Real-Time Market Anomaly Detection | `POST /research/market/anomalies` | Robust median/MAD outlier flags on returns, volume and spreads. Request-driven, not a streaming service. |
| Reinforcement Learning Research Lab | `POST /research/rl/offline-evaluation` | Offline clipped importance-sampling evaluation from logged episodes. No policy training. |
| Trade Surveillance & Market Abuse Detection | `POST /surveillance/trades/analyze` | Cancel/submit and rapid-event-burst indicators for human review. No misconduct finding or enforcement. |

## Example requests

Cross-asset scan:
```json
{"returns":{"ASSET_A":[0.01,-0.02,0.03,0.01],"ASSET_B":[0.02,-0.01,0.02,0.0]},"max_lag":1}
```

Options flow:
```json
{"trades":[{"side":"buy","option_type":"call","premium":12000,"volume":50,"symbol":"EXAMPLE"}]}
```

Liquidity:
```json
{"spread_bps":[2,2.5,3],"volume":[100000,90000,80000],"volatility":[0.01,0.012,0.02],"horizon":1}
```

Anomaly detection:
```json
{"returns":[0.01,0.0,-0.01,0.02,0.2],"volumes":[100,110,95,105,900],"spread_bps":[2,2,2,2,15]}
```

Offline RL evaluation:
```json
{"episodes":[{"reward":1.2,"behavior_probability":0.5,"target_probability":0.6}]}
```

Surveillance:
```json
{"events":[{"order_id":"ord-1","action":"submit","timestamp_seconds":1.0,"quantity":100},{"order_id":"ord-1","action":"cancel","timestamp_seconds":1.2,"quantity":100}]}
```

## Important limitations and controls

- Cross-asset correlation and lag scans are vulnerable to common-factor confounding, non-stationarity, data snooping and multiple testing. Require timestamp alignment, point-in-time availability, robust out-of-sample validation and economic rationale before treating a relationship as a candidate.
- Options flow aggregation depends on accurate trade-side classification, contract metadata, venue coverage, corrections and entitlement. Premium flow alone is not a reliable directional signal. No options vendor feed is connected.
- Liquidity output is a recent-window heuristic, not a calibrated forecast. It omits order-book depth, queue priority, venue fragmentation, market impact calibration and regime-specific behavior.
- Anomaly detection is a request-time diagnostic, not a real-time streaming detector. Alerts can be false positives and must not automatically trigger trading halts or orders.
- Offline importance sampling requires logged-policy support and correct behavior propensities. Effective sample size and clipping do not remove estimator bias. No RL training, online exploration or deployment is performed.
- Surveillance indicators are not proof of spoofing, wash trading or other misconduct. Require full venue/order lifecycle context, identity controls, applicable rules and trained human review. No enforcement action is taken.
- Registry status `prototype` describes capability foundations, not production readiness. Keep independent deterministic risk checks and fail-closed data controls in force. Live broker connectivity and order routing remain disabled.
