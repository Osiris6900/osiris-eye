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


def test_concurrent_enrollment_consumes_challenge_exactly_once(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    import threading

    database = str(tmp_path / "nodes.sqlite3")
    first, second = P2PIdentityRegistry(database), P2PIdentityRegistry(database)
    identity = generate_node_identity()
    challenge = first.create_challenge()
    signature = sign_enrollment_challenge(identity["private_key"], challenge["challenge_id"], challenge["nonce"], identity["node_id"])
    barrier = threading.Barrier(2)

    def attempt(registry):
        barrier.wait(timeout=5)
        try:
            return registry.enroll(identity["public_key"], signature, challenge["challenge_id"])["enrolled"]
        except ValueError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as workers:
        outcomes = list(workers.map(attempt, (first, second)))
    assert outcomes.count(True) == 1
    assert outcomes.count("challenge_missing_or_already_used") == 1
    assert first.status()["node_count"] == 1


def test_challenge_storage_bounded_and_expired_slots_reclaimed(tmp_path, monkeypatch):
    import osiris_api.p2p_identity as identity_module
    from types import SimpleNamespace

    clock = [1_000.0]
    monkeypatch.setattr(identity_module, "time", SimpleNamespace(time=lambda: clock[0]))
    monkeypatch.setattr(identity_module, "MAX_ACTIVE_CHALLENGES", 2)
    registry = P2PIdentityRegistry(str(tmp_path / "nodes.sqlite3"))
    registry.create_challenge()
    registry.create_challenge()
    with pytest.raises(ValueError, match="challenge_capacity_exceeded"):
        registry.create_challenge()
    clock[0] += identity_module.CHALLENGE_TTL_SECONDS + 1
    registry.create_challenge()
    with registry._connect() as database:
        assert database.execute("SELECT COUNT(*) FROM p2p_challenges").fetchone()[0] == 1


def test_node_capacity_allows_existing_node_refresh(tmp_path, monkeypatch):
    import osiris_api.p2p_identity as identity_module

    monkeypatch.setattr(identity_module, "MAX_ENROLLED_NODES", 1)
    registry = P2PIdentityRegistry(str(tmp_path / "nodes.sqlite3"))
    identity = generate_node_identity()
    assert enroll(registry, identity)["enrolled"]
    with pytest.raises(ValueError, match="node_registry_capacity_exceeded"):
        enroll(registry, generate_node_identity())
    assert enroll(registry, identity)["enrolled"]


@pytest.mark.parametrize("age", [-1.0, 301.0, float("inf")])
def test_future_expired_and_nonfinite_challenges_rejected(tmp_path, age):
    import time

    registry = P2PIdentityRegistry(str(tmp_path / "nodes.sqlite3"))
    identity = generate_node_identity()
    challenge = registry.create_challenge()
    with registry._connect() as database:
        database.execute("UPDATE p2p_challenges SET created_at=? WHERE challenge_id=?", (time.time() - age, challenge["challenge_id"]))
    signature = sign_enrollment_challenge(identity["private_key"], challenge["challenge_id"], challenge["nonce"], identity["node_id"])
    with pytest.raises(ValueError, match="challenge_expired"):
        registry.enroll(identity["public_key"], signature, challenge["challenge_id"])


def test_key_decoding_rejects_ignored_nonbase64_characters(tmp_path):
    registry = P2PIdentityRegistry(str(tmp_path / "nodes.sqlite3"))
    identity = generate_node_identity()
    with pytest.raises(ValueError, match="invalid_base64_value"):
        registry.enroll(identity["public_key"] + "!", "invalid", "missing")


def test_trusted_public_key_padding_cannot_change_enrollment_identity(tmp_path):
    identity = generate_node_identity()
    registry = P2PIdentityRegistry(str(tmp_path / "nodes.sqlite3"), {identity["public_key"] + "="})
    assert enroll(registry, identity)["trust_level"] == "trusted"


def test_identity_registry_requires_persistent_database():
    with pytest.raises(ValueError, match="identity_registry_requires_persistent_database"):
        P2PIdentityRegistry(":memory:")


def test_malformed_signature_is_not_accepted():
    from osiris_api.p2p_identity import _verify

    assert _verify("%%%", "%%%", "challenge", "nonce", "node") is False
