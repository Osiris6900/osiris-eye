"""Deterministic, offline research experiment lineage and duplicate detection.

No execution, backtesting, validation or model approval is performed here.
"""
from __future__ import annotations

import hashlib
import json


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def valid_parameters(parameters: dict) -> bool:
    """Require bounded JSON values without coercing non-string object keys."""
    pending = [(parameters, 0)]
    visited = 0
    while pending:
        value, depth = pending.pop()
        visited += 1
        if depth > 20 or visited > 10000:
            return False
        if isinstance(value, dict):
            if len(value) + visited + len(pending) > 10000:
                return False
            if any(not isinstance(key, str) for key in value):
                return False
            pending.extend((item, depth + 1) for item in value.values())
        elif isinstance(value, list):
            if len(value) + visited + len(pending) > 10000:
                return False
            pending.extend((item, depth + 1) for item in value)
        elif type(value) not in (str, int, float, bool, type(None)):
            return False
    return True


def register_experiments(experiments: object) -> dict:
    if not isinstance(experiments, list) or not 1 <= len(experiments) <= 500:
        return {"status": "rejected", "reason": "invalid_experiment_count"}
    seen_ids, seen_fingerprints, records = set(), set(), []
    required = {"experiment_id", "commit_sha", "dataset_sha256", "parameters",
                "window_start_ms", "window_end_ms"}
    for experiment in experiments:
        if not isinstance(experiment, dict) or set(experiment) != required:
            return {"status": "rejected", "reason": "invalid_experiment_shape"}
        eid, commit, dataset = (experiment[k] for k in
                                ("experiment_id", "commit_sha", "dataset_sha256"))
        if (not isinstance(eid, str) or not 1 <= len(eid) <= 128 or eid in seen_ids
                or any(not isinstance(s, str) or len(s) != 64 if k == "dataset" else
                       not isinstance(s, str) or len(s) != 40
                       for k, s in (("commit", commit), ("dataset", dataset)))
                or any(c not in "0123456789abcdef" for c in commit + dataset)):
            return {"status": "rejected", "reason": "invalid_experiment_identity"}
        start, end = experiment["window_start_ms"], experiment["window_end_ms"]
        if type(start) is not int or type(end) is not int or start < 0 or end <= start:
            return {"status": "rejected", "reason": "invalid_experiment_window"}
        parameters = experiment["parameters"]
        if (not isinstance(parameters, dict) or len(parameters) > 100
                or not valid_parameters(parameters)):
            return {"status": "rejected", "reason": "invalid_parameters"}
        try:
            encoded = canonical(parameters)
        except (TypeError, ValueError, OverflowError):
            return {"status": "rejected", "reason": "invalid_parameters"}
        if len(encoded) > 65536:
            return {"status": "rejected", "reason": "parameters_too_large"}
        fingerprint = hashlib.sha256(canonical({
            "commit_sha": commit, "dataset_sha256": dataset,
            "parameters": parameters, "window_start_ms": start,
            "window_end_ms": end})).hexdigest()
        if fingerprint in seen_fingerprints:
            return {"status": "rejected", "reason": "duplicate_experiment_fingerprint"}
        seen_ids.add(eid)
        seen_fingerprints.add(fingerprint)
        records.append({"experiment_id": eid, "fingerprint_sha256": fingerprint})
    return {"status": "registered_in_memory", "records": sorted(records, key=lambda r: r["experiment_id"]),
            "dataset_independently_verified": False, "experiment_executed": False,
            "model_approved": False, "live_trading_authorized": False}
