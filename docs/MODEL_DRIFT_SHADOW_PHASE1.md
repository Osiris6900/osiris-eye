# Model drift and shadow monitoring: initial phase

Tracks #393 and #394. The bounded PSI histogram comparison in `scripts/model_drift_shadow_guard.py` flags distribution shift from caller-supplied reference and current histograms. Run `python -m unittest discover -s scripts -p 'test_model_drift_shadow_guard.py'`.

This is **offline shadow-only advisory**. No live data feed, model inference, quarantine action, execution, historical calibration, authenticated histogram bin schema or independent data validation is included. Next steps are authenticated feature histogram capture, identical bin boundaries, alert routing, shadow model lifecycle and risk-controlled paper reconciliation. PSI thresholds are heuristic and must be calibrated.
