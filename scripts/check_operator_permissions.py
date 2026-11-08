"""Minimal fail-closed operator authorization contract for control-centre design.

Offline validation only; production authentication must verify identity and
roles server-side. A frontend check alone is not authorization.

The RBAC tables are canonical in ``osiris_api.admin_centre`` (issue #180) and
re-exported here so this offline contract and the live admin API can never
drift apart.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "services" / "quant-api" / "src"))

from osiris_api.admin_centre import DANGEROUS, PERMISSIONS, authorize  # noqa: E402,F401


def check_requests(requests):
    if not isinstance(requests, list):
        return {"passed": False, "errors": ["requests must be an array"]}
    decisions = []
    for item in requests:
        if not isinstance(item, dict):
            decisions.append(False); continue
        decisions.append(authorize(item.get("role"), item.get("action"), item.get("authenticated")))
    return {"passed": True, "allowed": decisions, "denied_count": decisions.count(False)}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("requests", type=Path)
    a = p.parse_args()
    print(json.dumps(check_requests(json.loads(a.requests.read_text()))))
