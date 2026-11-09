from osiris_api.receipt_replay_gate import check_receipt

ID = "a" * 64

def test_valid_receipt_is_only_eligible_for_atomic_commit():
    assert check_receipt(ID, signature_verified=True, revoked=False, previously_accepted=set()).accepted

def test_unverified_revoked_and_replayed_are_blocked():
    for kw in (dict(signature_verified=False, revoked=False, previously_accepted=set()),
               dict(signature_verified=True, revoked=True, previously_accepted=set()),
               dict(signature_verified=True, revoked=False, previously_accepted={ID})):
        assert not check_receipt(ID, **kw).accepted

def test_malformed_receipt_blocked():
    assert not check_receipt("invalid", signature_verified=True, revoked=False, previously_accepted=set()).accepted
