import copy

import pytest

from osiris_api.p2p_e2ee import (
    ReplayGuard,
    SQLiteReplayGuard,
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
    first_character = "A" if tampered["ciphertext"][0] != "A" else "B"
    tampered["ciphertext"] = first_character + tampered["ciphertext"][1:]
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
    with pytest.raises(RuntimeError, match="replay_store_capacity_exceeded"):
        guard.check_and_record("c")
    assert not guard.check_and_record("a")


@pytest.mark.parametrize("guard_type", [ReplayGuard, SQLiteReplayGuard])
def test_failed_decryption_does_not_consume_replay_slot(tmp_path, guard_type):
    _, _, transport, envelope, args = make_pair()
    args["replay_guard"] = guard_type(capacity=1) if guard_type is ReplayGuard else guard_type(tmp_path / "replay.sqlite3", capacity=1)
    args["recipient_transport_private_key"] = generate_transport_identity()["private_key"]
    with pytest.raises(ValueError, match="message_authentication_failed"):
        decrypt_from_peer(envelope, **args)
    args["recipient_transport_private_key"] = transport["private_key"]
    assert decrypt_from_peer(envelope, **args) == b"private payload"


@pytest.mark.parametrize("now", [float("nan"), float("inf"), True, -1])
def test_rejects_invalid_clock_instead_of_bypassing_expiry(now):
    _, _, _, envelope, args = make_pair()
    with pytest.raises(ValueError, match="invalid_current_timestamp"):
        decrypt_from_peer(envelope, **args, now=now)


@pytest.mark.parametrize("capacity", [True, 1.5, float("nan"), float("inf")])
def test_replay_capacity_requires_integer(tmp_path, capacity):
    with pytest.raises(ValueError, match="capacity_must_be_positive_integer"):
        ReplayGuard(capacity)
    with pytest.raises(ValueError, match="capacity_must_be_positive_integer"):
        SQLiteReplayGuard(tmp_path / "bad.sqlite3", capacity)


def test_oversized_ciphertext_rejected_before_signature_verification():
    from osiris_api.p2p_e2ee import MAX_CIPHERTEXT_ENCODED_LENGTH
    _, _, _, envelope, args = make_pair()
    envelope["ciphertext"] = "A" * (MAX_CIPHERTEXT_ENCODED_LENGTH + 1)
    with pytest.raises(ValueError, match="invalid_envelope_field_size"):
        decrypt_from_peer(envelope, **args)


def test_legacy_durable_replay_entries_remain_rejected(tmp_path):
    _, _, _, envelope, args = make_pair()
    args["replay_guard"] = SQLiteReplayGuard(tmp_path / "legacy.sqlite3")
    assert args["replay_guard"].check_and_record(envelope["message_id"])
    with pytest.raises(ValueError, match="replayed_message"):
        decrypt_from_peer(envelope, **args)


def test_sender_cannot_reserve_another_senders_message_id(monkeypatch):
    import osiris_api.p2p_e2ee as encryption_module

    monkeypatch.setattr(encryption_module.secrets, "token_bytes", lambda size: bytes(size))
    _, _, _, first_envelope, first_args = make_pair()
    _, _, _, second_envelope, second_args = make_pair()
    assert first_envelope["message_id"] == second_envelope["message_id"]
    shared_guard = ReplayGuard()
    first_args["replay_guard"] = second_args["replay_guard"] = shared_guard
    assert decrypt_from_peer(first_envelope, **first_args) == b"private payload"
    assert decrypt_from_peer(second_envelope, **second_args) == b"private payload"


def test_memory_replay_guard_admission_is_atomic_across_threads():
    from concurrent.futures import ThreadPoolExecutor

    guard = ReplayGuard(capacity=1)
    with ThreadPoolExecutor(max_workers=8) as workers:
        accepted = list(workers.map(guard.check_and_record, ["same-message"] * 64))
    assert sum(accepted) == 1



def test_sqlite_replay_guard_survives_restart(tmp_path):
    database = tmp_path / "replay.sqlite3"
    first_process = SQLiteReplayGuard(database)
    assert first_process.check_and_record("message-1")
    assert not first_process.check_and_record("message-1")

    restarted_process = SQLiteReplayGuard(database)
    assert not restarted_process.check_and_record("message-1")
    assert restarted_process.check_and_record("message-2")


def test_sqlite_replay_guard_fails_closed_at_capacity(tmp_path):
    guard = SQLiteReplayGuard(tmp_path / "bounded.sqlite3", capacity=1)
    assert guard.check_and_record("message-1")
    with pytest.raises(RuntimeError, match="replay_store_capacity_exceeded"):
        guard.check_and_record("message-2")

    restarted = SQLiteReplayGuard(tmp_path / "bounded.sqlite3", capacity=1)
    assert not restarted.check_and_record("message-1")
    with pytest.raises(RuntimeError, match="replay_store_capacity_exceeded"):
        restarted.check_and_record("message-2")


def test_sqlite_replay_guard_is_atomic_across_instances(tmp_path):
    database = tmp_path / "concurrent.sqlite3"
    first = SQLiteReplayGuard(database)
    second = SQLiteReplayGuard(database)
    assert first.check_and_record("one-shot")
    assert not second.check_and_record("one-shot")
