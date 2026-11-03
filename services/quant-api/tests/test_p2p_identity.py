import pytest
from osiris_api.p2p_identity import P2PIdentityRegistry, generate_node_identity, sign_enrollment_challenge

def enroll(registry, identity):
    challenge = registry.create_challenge()
    signature = sign_enrollment_challenge(identity["private_key"], challenge["challenge_id"], challenge["nonce"], identity["node_id"])
    return registry.enroll(identity["public_key"], signature, challenge["challenge_id"])

def test_new_node_is_untrusted_by_default(tmp_path):
    registry = P2PIdentityRegistry(str(tmp_path / "nodes.sqlite3"))
    identity = generate_node_identity()
    result = enroll(registry, identity)
    assert result["node_id"] == identity["node_id"]
    assert result["trust_level"] == "public_untrusted"
    assert result["permissions"] == ["peer_discovery"]
    assert registry.status()["node_count"] == 1

def test_allowlisted_node_gets_trusted_compute_scope(tmp_path):
    identity = generate_node_identity()
    registry = P2PIdentityRegistry(str(tmp_path / "nodes.sqlite3"), {identity["public_key"]})
    result = enroll(registry, identity)
    assert result["trust_level"] == "trusted"
    assert "approved_compute" in result["permissions"]

def test_challenge_is_one_time(tmp_path):
    registry = P2PIdentityRegistry(str(tmp_path / "nodes.sqlite3"))
    identity = generate_node_identity()
    challenge = registry.create_challenge()
    signature = sign_enrollment_challenge(identity["private_key"], challenge["challenge_id"], challenge["nonce"], identity["node_id"])
    registry.enroll(identity["public_key"], signature, challenge["challenge_id"])
    with pytest.raises(ValueError, match="challenge_missing_or_already_used"):
        registry.enroll(identity["public_key"], signature, challenge["challenge_id"])

def test_other_private_key_cannot_prove_identity(tmp_path):
    registry = P2PIdentityRegistry(str(tmp_path / "nodes.sqlite3"))
    identity, attacker = generate_node_identity(), generate_node_identity()
    challenge = registry.create_challenge()
    signature = sign_enrollment_challenge(attacker["private_key"], challenge["challenge_id"], challenge["nonce"], identity["node_id"])
    with pytest.raises(ValueError, match="invalid_node_signature"):
        registry.enroll(identity["public_key"], signature, challenge["challenge_id"])

def test_status_never_claims_network_or_trading_is_enabled(tmp_path):
    status = P2PIdentityRegistry(str(tmp_path / "nodes.sqlite3")).status()
    assert status["transport_enabled"] is False
    assert status["peer_discovery_enabled"] is False
    assert status["distributed_compute_enabled"] is False
    assert status["live_execution"] is False
