"""Fail-closed replay gate for signed receipt identifiers (offline primitive).

The caller must verify the receipt signature and persist the accepted identifiers
transactionally before using this as a cross-process anti-replay boundary.
"""
from dataclasses import dataclass
import re

DIGEST = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class ReceiptDecision:
    accepted: bool
    reason: str


def check_receipt(receipt_id: str, *, signature_verified: bool, revoked: bool,
                  previously_accepted: set[str]) -> ReceiptDecision:
    if not isinstance(receipt_id, str) or not DIGEST.fullmatch(receipt_id):
        return ReceiptDecision(False, "invalid_receipt_id")
    if signature_verified is not True:
        return ReceiptDecision(False, "signature_not_verified")
    if revoked is not False:
        return ReceiptDecision(False, "identity_revoked")
    if receipt_id in previously_accepted:
        return ReceiptDecision(False, "replayed_receipt")
    return ReceiptDecision(True, "eligible_for_atomic_commit")
