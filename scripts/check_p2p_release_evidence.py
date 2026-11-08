"""Fail-closed metadata gate for a final-candidate P2P release record.

Usage: python scripts/check_p2p_release_evidence.py evidence.json
No network access or credentials are needed. This validates evidence shape,
not the authenticity of an independent-host run or the contents of its logs.
"""
import argparse
import json
import re
from pathlib import Path

SHA = re.compile(r"^[0-9a-f]{40}$")
DIGEST = re.compile(r"^[0-9a-f]{64}$")

def check(record):
    errors = []
    def require(condition, message):
        if not condition:
            errors.append(message)
    require(isinstance(record, dict), "record must be an object")
    if not isinstance(record, dict):
        return errors
    require(bool(SHA.fullmatch(str(record.get("commit_sha", "")))), "valid commit_sha required")
    require(record.get("clean_checkout") is True, "clean_checkout must be true")
    require(record.get("topology") in ("two_independent_hosts", "three_independent_hosts"), "independent-host topology required")
    hosts = record.get("hosts")
    require(isinstance(hosts, list), "hosts must be a list")
    if isinstance(hosts, list):
        expected = 2 if record.get("topology") == "two_independent_hosts" else 3
        require(len(hosts) == expected, "host count does not match topology")
        ids = [h.get("host_id") for h in hosts if isinstance(h, dict)]
        require(len(ids) == len(hosts) and all(isinstance(i, str) and i for i in ids) and len(set(ids)) == len(ids), "host IDs must be nonempty and distinct")
        require(all(isinstance(h, dict) and DIGEST.fullmatch(str(h.get("binary_sha256", ""))) for h in hosts), "each host requires binary_sha256")
    require(bool(DIGEST.fullmatch(str(record.get("payload_sha256", "")))), "payload_sha256 required")
    require(record.get("recipient_digest_matched") is True, "recipient digest proof required")
    if record.get("topology") == "three_independent_hosts":
        require(record.get("nonrecipient_discarded") is True, "nonrecipient discard proof required")
    require(isinstance(record.get("workflow_url"), str) and record["workflow_url"].startswith("https://github.com/"), "GitHub workflow URL required")
    return errors

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("evidence", type=Path)
    args = parser.parse_args()
    errors = check(json.loads(args.evidence.read_text(encoding="utf-8")))
    print(json.dumps({"passed": not errors, "errors": errors}))
    return int(bool(errors))

if __name__ == "__main__":
    raise SystemExit(main())
