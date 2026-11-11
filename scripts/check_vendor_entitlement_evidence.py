#!/usr/bin/env python3
"""Offline fail-closed inventory of operator-provided market-data licence evidence.

Valid metadata does not establish vendor rights or authorize a live feed.
"""
from __future__ import annotations

import argparse
from datetime import date
import json
import re
from pathlib import Path
from urllib.parse import urlparse

SHA = re.compile(r"[0-9a-f]{64}\Z")
ALLOWED = {"internal_research", "paper_trading", "derived_data", "redistribution"}


def validate(record: object, *, as_of: date) -> dict:
    problems = []
    if not isinstance(record, dict) or set(record) != {
        "schema", "vendor", "contract_reference", "reviewer", "reviewed_on",
        "expires_on", "permitted_uses", "instruments", "evidence_url",
        "evidence_sha256", "vendor_countersignature_verified",
    }:
        return {"metadata_valid": False, "errors": ["invalid_shape"],
                "vendor_rights_verified": False, "t09_accepted": False}
    if record["schema"] != "osiris.vendor-entitlement-evidence.v1":
        problems.append("invalid_schema")
    for key in ("vendor", "contract_reference", "reviewer"):
        if not isinstance(record[key], str) or not record[key].strip() or len(record[key]) > 200:
            problems.append("invalid_" + key)
    try:
        reviewed = date.fromisoformat(record["reviewed_on"])
        expires = date.fromisoformat(record["expires_on"])
        if reviewed > as_of or expires < as_of or expires < reviewed:
            problems.append("expired_or_future_review")
    except (TypeError, ValueError):
        problems.append("invalid_dates")
    uses = record["permitted_uses"]
    if not isinstance(uses, list) or not uses or len(uses) != len(set(map(str, uses))) or any(
        not isinstance(x, str) or x not in ALLOWED for x in uses
    ):
        problems.append("invalid_permitted_uses")
    instruments = record["instruments"]
    if not isinstance(instruments, list) or not instruments or any(
        not isinstance(x, str) or not x.strip() or len(x) > 100 for x in instruments
    ):
        problems.append("invalid_instruments")
    url = record["evidence_url"]
    parsed = urlparse(url) if isinstance(url, str) else None
    if not parsed or parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        problems.append("invalid_evidence_url")
    digest = record["evidence_sha256"]
    if not isinstance(digest, str) or not SHA.fullmatch(digest):
        problems.append("invalid_evidence_sha256")
    if type(record["vendor_countersignature_verified"]) is not bool:
        problems.append("invalid_countersignature_flag")
    return {
        "metadata_valid": not problems,
        "errors": problems,
        "vendor_rights_verified": False,
        "contract_authenticity_verified": False,
        "live_feed_authorized": False,
        "redistribution_authorized": False,
        "t09_accepted": False,
        "boundary": "metadata_only_operator_must_verify_original_contract",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("record", type=Path)
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    args = parser.parse_args()
    try:
        result = validate(json.loads(args.record.read_text(encoding="utf-8")), as_of=args.as_of)
    except (OSError, ValueError) as exc:
        result = {"metadata_valid": False, "errors": [str(exc)], "t09_accepted": False}
    print(json.dumps(result, sort_keys=True))
    return 0 if result["metadata_valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
