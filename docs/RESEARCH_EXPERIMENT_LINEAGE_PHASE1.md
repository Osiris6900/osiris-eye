# Research experiment lineage — phase 1

Tracks issues #392 (feature lineage) and #400 (experiment registry).

`scripts/research_experiment_lineage.py` computes deterministic SHA-256 fingerprints for caller-supplied experiment identifiers, source commit hashes, dataset hashes, parameter maps and time windows. It rejects duplicate IDs, identical experiments and malformed metadata. Run `python -m unittest discover -s scripts -p 'test_research_experiment_lineage.py'`.

**Not yet implemented:** persistent registry, signed data provenance, point-in-time joins, training/serving parity, actual experiment execution, verified source commits, model review or risk authorization. No trading path is connected. This initial step is deliberately in-memory and fail-closed on malformed metadata.
