#[path = "../src/e2ee.rs"]
mod e2ee;
#[path = "../src/peer_auth.rs"]
mod peer_auth;
#[path = "../src/revocation.rs"]
mod revocation;

use ed25519_dalek::{Signer, SigningKey};
use e2ee::{create_key_binding, decrypt_for_recipient, encrypt_for_recipient, verify_key_binding, TransportKeyPair};
use libp2p::{identity, PeerId};
use peer_auth::{deny_peer, is_peer_allowed, run_if_peer_allowed};
use revocation::{apply_synced_revocation, RevocationRegistry, RevocationSyncMessage, SignedRevocationRecord, SyncedRevocation};
use serde::Serialize;
use std::{collections::HashSet, time::{SystemTime, UNIX_EPOCH}};

const SCHEMA: &str = "osiris.revocation.v1";

#[derive(Serialize)]
struct SigningPayload<'a> {
    schema: &'a str,
    authority_key: &'a str,
    peer_id: &'a str,
    sequence: u64,
    issued_at: u64,
    reason_code: &'a str,
}

fn encode_hex(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

fn signed_revocation(key: &SigningKey, peer: PeerId, sequence: u64) -> SignedRevocationRecord {
    let authority_key = encode_hex(key.verifying_key().as_bytes());
    let mut record = SignedRevocationRecord {
        schema: SCHEMA.into(),
        authority_key: authority_key.clone(),
        peer_id: peer.to_string(),
        sequence,
        issued_at: SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs(),
        reason_code: "operator_compromise".into(),
        signature: String::new(),
    };
    let payload = serde_json::to_vec(&SigningPayload {
        schema: &record.schema,
        authority_key: &record.authority_key,
        peer_id: &record.peer_id,
        sequence: record.sequence,
        issued_at: record.issued_at,
        reason_code: &record.reason_code,
    }).unwrap();
    record.signature = encode_hex(&key.sign(&payload).to_bytes());
    record
}

fn temp_registry_path(label: &str) -> std::path::PathBuf {
    let nonce = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_nanos();
    std::env::temp_dir().join(format!("osiris-{label}-{}-{nonce}.json", std::process::id()))
}

#[test]
fn signed_revocation_persistently_blocks_allowlisted_peer_at_encrypted_message_gate() {
    let authority = SigningKey::from_bytes(&[73u8; 32]);
    let peer = PeerId::random();
    let trusted = Some(HashSet::from([peer]));
    let mut revoked: Option<HashSet<PeerId>> = None;
    let path = temp_registry_path("revocation-e2ee-policy");
    let authority_hex = encode_hex(authority.verifying_key().as_bytes());

    // Before revocation, this peer is trusted and passes the same policy gate
    // used by the encrypted-message handler.
    assert!(is_peer_allowed(&peer, &trusted, &revoked));

    {
        let mut registry = RevocationRegistry::open(path.clone(), authority_hex.clone()).unwrap();
        let record = signed_revocation(&authority, peer, 1);
        assert!(registry.apply(record, true).unwrap());

        // Mirror the production revocation event path: after the signed record
        // is durably accepted, its PeerId is inserted into runtime deny policy.
        for revoked_peer in registry.revoked_peer_ids().unwrap() {
            revoked.get_or_insert_with(HashSet::new).insert(revoked_peer);
        }
    }

    // A revoked peer remains blocked even if it is still in the trusted list.
    // The encrypted-message handler checks this policy before key lookup/decrypt.
    assert!(!is_peer_allowed(&peer, &trusted, &revoked));

    // Restart from the durable registry and reconstruct the runtime deny set.
    let restarted = RevocationRegistry::open(path.clone(), authority_hex).unwrap();
    let mut restarted_revoked = Some(HashSet::new());
    for revoked_peer in restarted.revoked_peer_ids().unwrap() {
        restarted_revoked.as_mut().unwrap().insert(revoked_peer);
    }
    assert!(!is_peer_allowed(&peer, &trusted, &restarted_revoked));

    let _ = std::fs::remove_file(path);
}

#[test]
fn forged_revocation_cannot_enter_runtime_deny_policy() {
    let authority = SigningKey::from_bytes(&[74u8; 32]);
    let attacker = SigningKey::from_bytes(&[75u8; 32]);
    let peer = PeerId::random();
    let trusted = Some(HashSet::from([peer]));
    let mut revoked: Option<HashSet<PeerId>> = None;
    let path = temp_registry_path("revocation-forgery");
    let authority_hex = encode_hex(authority.verifying_key().as_bytes());

    let forged = signed_revocation(&attacker, peer, 1);
    let mut registry = RevocationRegistry::open(path.clone(), authority_hex).unwrap();
    assert!(registry.apply(forged, true).is_err());
    assert!(registry.revoked_peer_ids().unwrap().is_empty());
    assert!(is_peer_allowed(&peer, &trusted, &revoked));

    let _ = std::fs::remove_file(path);
}

#[test]
fn synced_revocation_blocks_decrypt_of_a_still_valid_sender_key() {
    let sender = identity::Keypair::generate_ed25519();
    let sender_transport = TransportKeyPair::generate();
    let recipient = identity::Keypair::generate_ed25519();
    let recipient_transport = TransportKeyPair::generate();
    let sender_peer = PeerId::from(sender.public());
    let recipient_peer = PeerId::from(recipient.public());
    let now = 1_700_000_000u64;
    let binding_record = create_key_binding(
        &sender, &sender_transport.public_key, 1, now - 5, now + 300,
    ).unwrap();
    let binding = verify_key_binding(&binding_record, now, 1).unwrap();
    let envelope = encrypt_for_recipient(
        &sender, 1, &recipient_peer.to_string(), &recipient_transport.public_key,
        1, b"revoked-sender-must-not-surface", now, now + 120,
    ).unwrap();
    let trusted = Some(HashSet::from([sender_peer]));
    let mut revoked = None;
    let mut decrypts = 0u32;
    let recipient_secret = x25519_dalek::StaticSecret::from(recipient_transport.private_bytes());
    let admitted = run_if_peer_allowed(&sender_peer, &trusted, &revoked, || {
        decrypts += 1;
        decrypt_for_recipient(
            &envelope, &binding, &recipient_peer, 1, &recipient_secret, now,
        ).unwrap()
    }).unwrap();
    assert_eq!(admitted, b"revoked-sender-must-not-surface");
    assert_eq!(decrypts, 1);

    let authority = SigningKey::from_bytes(&[91u8; 32]);
    let path = temp_registry_path("revocation-blocks-decrypt");
    let mut registry = RevocationRegistry::open(
        path.clone(),
        encode_hex(authority.verifying_key().as_bytes()),
    ).unwrap();
    let record = signed_revocation(&authority, sender_peer, 1);
    let bytes = serde_json::to_vec(&RevocationSyncMessage::Record(record)).unwrap();
    let RevocationSyncMessage::Record(decoded) = serde_json::from_slice(&bytes).unwrap() else {
        panic!("revocation record did not round-trip");
    };
    assert_eq!(
        apply_synced_revocation(&mut registry, decoded, true).unwrap(),
        SyncedRevocation::Applied(sender_peer),
    );
    deny_peer(&mut revoked, sender_peer);
    let mut entered = false;
    assert!(run_if_peer_allowed(&sender_peer, &trusted, &revoked, || {
        entered = true;
        decrypt_for_recipient(
            &envelope, &binding, &recipient_peer, 1, &recipient_secret, now,
        ).unwrap()
    }).is_err());
    assert!(!entered);
    assert!(!is_peer_allowed(&sender_peer, &trusted, &revoked));
    let _ = std::fs::remove_file(path);
}
