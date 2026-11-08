"""Ensure release evidence references the exact candidate and independently attested hosts."""
import re


def check_candidate_binding(manifest, expected_commit):
    errors = []
    if not isinstance(expected_commit, str) or not re.fullmatch(r"[0-9a-f]{40}", expected_commit):
        errors.append("invalid_expected_commit")
    if not isinstance(manifest, dict) or manifest.get("candidate_commit") != expected_commit:
        errors.append("candidate_commit_mismatch")
    hosts = manifest.get("host_fingerprints") if isinstance(manifest, dict) else None
    if not isinstance(hosts, list) or len(hosts) < 2 or any(not isinstance(x, str) or not x.strip() for x in hosts) or len(set(hosts)) != len(hosts):
        errors.append("independent_hosts_not_attested")
    if not isinstance(manifest, dict) or manifest.get("live_execution_enabled") is not False:
        errors.append("live_execution_not_disabled")
    return {"eligible_for_further_review": not errors, "errors": errors}
