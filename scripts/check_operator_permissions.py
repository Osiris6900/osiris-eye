"""Minimal fail-closed operator authorization contract for control-centre design.

Offline validation only; production authentication must verify identity and
roles server-side. A frontend check alone is not authorization.
"""
import argparse
import json
from pathlib import Path

PERMISSIONS = {
    "viewer": frozenset({"read_health", "read_audit"}),
    "analyst": frozenset({"read_health", "read_audit", "read_research"}),
    "operator": frozenset({"read_health", "read_audit", "read_research", "ack_alert"}),
    "risk_officer": frozenset({"read_health", "read_audit", "read_research", "ack_alert", "activate_kill_switch"}),
    "security_admin": frozenset({"read_health", "read_audit", "read_research", "ack_alert", "revoke_peer"}),
}
DANGEROUS = frozenset({"place_live_order", "export_private_key", "disable_risk_gate", "erase_audit"})

def authorize(role, action, authenticated=False):
    return bool(authenticated is True and role in PERMISSIONS and action not in DANGEROUS and action in PERMISSIONS[role])

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
