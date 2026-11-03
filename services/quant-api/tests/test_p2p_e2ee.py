import copy

import pytest

from osiris_api.p2p_e2ee import (
    ReplayGuard,
    decrypt_from_peer,
    encrypt_for_peer,
    generate_transport_identity,
)
from osiris_api.p2p_identity import generate_node_identity


def make_pair():
    sender = generate_node_identity()
    recipient = generate_node_identity()
    transport = generate_transport_identity()
    envelope = encrypt_for_peer(
        b"private payload",
        sender_node_id=sender["node_id"],
        sender_signing_private_key=sender["private_key"],
        sender_signing_public_key=sender["public_key"],
        recipient_node_id=recipient["node_id"],
        recipient_transport_public_key=transport["public_key"],
    )
    args = dict(
        recipient_node_id=recipient["node_id"],
        recipient_transport_private_key=transport["private_key"],
        expected_sender_node_id=sender["node_id"],
        trusted_sender_signing_public_key=sender["public_key"],
        replay_guard=ReplayGuard(),
    )
    return sender, recipient, transport, envelope, args


def test_envelope_round_trip_only_recipient_can_decrypt():
    _, _, _, envelope, args = make_pair()
    assert decrypt_from_peer(envelope, **args) == b"private payload"


def test_rejects_wrong_sender_identity():
    _, _, _, envelope, args = make_pair()
    args["expected_sender_node_id"] = "0" * 64
    with pytest.raises(ValueError, match="peer_identity_mismatch"):
        decrypt_from_peer(envelope, **args)


def test_rejects_tampered_ciphertext_signature():
    _, _, _, envelope, args = make_pair()
    tampered = copy.deepcopy(envelope)
    tampered["ciphertext"] = tampered["ciphertext"][:-2] + "AA"
    with pytest.raises(ValueError, match="sender_signature_invalid"):
        decrypt_from_peer(tampered, **args)


def test_rejects_replayed_message():
    _, _, _, envelope, args = make_pair()
    guard = args["replay_guard"]
    assert decrypt_from_peer(envelope, **args) == b"private payload"
    with pytest.raises(ValueError, match="replayed_message"):
        decrypt_from_peer(envelope, **args)


def test_rejects_expired_message():
    _, _, _, envelope, args = make_pair()
    with pytest.raises(ValueError, match="message_expired_or_clock_skew"):
        decrypt_from_peer(envelope, **args, now=envelope["issued_at"] + 301)


def test_rejects_wrong_recipient_private_key():
    _, _, _, envelope, args = make_pair()
    args["recipient_transport_private_key"] = generate_transport_identity()["private_key"]
    with pytest.raises(ValueError, match="message_authentication_failed"):
        decrypt_from_peer(envelope, **args)


def test_replay_guard_is_bounded():
    guard = ReplayGuard(capacity=2)
    assert guard.check_and_record("a")
    assert guard.check_and_record("b")
    assert not guard.check_and_record("a")
    assert guard.check_and_record("c")
    assert guard.check_and_record("a")
