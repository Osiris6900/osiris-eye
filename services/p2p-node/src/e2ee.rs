//! Authenticated recipient-key binding primitives for OSIRIS P2P.
//!
//! This module is deliberately not wired into the live GossipSub path yet.
//! A key announcement is not a trust grant: callers must apply local
//! enrollment and revocation policy after cryptographic verification.

use base64::{engine::general_purpose::STANDARD_NO_PAD, Engine as _};
use libp2p::{identity, PeerId};
use rand_core::OsRng;
use serde::{Deserialize, Serialize};
use x25519_dalek::StaticSecret;

const KEY_BINDING_SCHEMA: &str = "osiris.p2p.key-binding.v1";
const MAX_KEY_BINDING_LIFETIME_SECS: u64 = 30 * 24 * 60 * 60;
const MAX_CLOCK_SKEW_SECS: u64 = 300;

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct KeyBindingRecord {
    pub schema: String,
    pub peer_id: String,
    pub identity_public_key: String,
    pub transport_public_key: String,
    pub key_epoch: u64,
    pub issued_at: u64,
    pub expires_at: u64,
    pub signature: String,
}

#[derive(Serialize)]
struct UnsignedKeyBinding<'a> {
    schema: &'a str,
    peer_id: &'a str,
    identity_public_key: &'a str,
    transport_public_key: &'a str,
    key_epoch: u64,
    issued_at: u64,
    expires_at: u64,
}

/// Private X25519 key material remains local; only the public half is published.
pub struct TransportKeyPair {
    private_key: StaticSecret,
    pub public_key: [u8; 32],
}

impl TransportKeyPair {
    pub fn generate() -> Self {
        let private_key = StaticSecret::random_from_rng(OsRng);
        let public_key = x25519_dalek::PublicKey::from(&private_key).to_bytes();
        Self { private_key, public_key }
    }

    pub(crate) fn private_key(&self) -> &StaticSecret {
        &self.private_key
    }
}

pub struct VerifiedKeyBinding {
    pub peer_id: PeerId,
    pub transport_public_key: [u8; 32],
    pub key_epoch: u64,
    pub identity_public_key: identity::PublicKey,
}

fn unsigned_bytes(record: &KeyBindingRecord) -> Result<Vec<u8>, &'static str> {
    let unsigned = UnsignedKeyBinding {
        schema: &record.schema,
        peer_id: &record.peer_id,
        identity_public_key: &record.identity_public_key,
        transport_public_key: &record.transport_public_key,
        key_epoch: record.key_epoch,
        issued_at: record.issued_at,
        expires_at: record.expires_at,
    };
    serde_json::to_vec(&unsigned).map_err(|_| "key_binding_serialization_failed")
}

/// Sign a node's X25519 public key with its existing libp2p Ed25519 identity.
pub fn create_key_binding(
    identity_key: &identity::Keypair,
    transport_public_key: &[u8; 32],
    key_epoch: u64,
    issued_at: u64,
    expires_at: u64,
) -> Result<KeyBindingRecord, &'static str> {
    if key_epoch == 0 || expires_at <= issued_at
        || expires_at - issued_at > MAX_KEY_BINDING_LIFETIME_SECS
    {
        return Err("invalid_key_binding_epoch_or_lifetime");
    }

    let public_key = identity_key.public();
    let peer_id = PeerId::from_public_key(&public_key).to_string();
    let identity_public_key = STANDARD_NO_PAD.encode(public_key.encode_protobuf());
    let transport_public_key = STANDARD_NO_PAD.encode(transport_public_key);

    let mut record = KeyBindingRecord {
        schema: KEY_BINDING_SCHEMA.to_owned(),
        peer_id,
        identity_public_key,
        transport_public_key,
        key_epoch,
        issued_at,
        expires_at,
        signature: String::new(),
    };
    let signature = identity_key
        .sign(&unsigned_bytes(&record)?)
        .map_err(|_| "key_binding_sign_failed")?;
    record.signature = STANDARD_NO_PAD.encode(signature);
    Ok(record)
}

/// Verify signature, PeerId binding, validity window, and minimum accepted epoch.
/// Local enrollment/revocation policy must still be checked by the caller.
pub fn verify_key_binding(
    record: &KeyBindingRecord,
    now: u64,
    minimum_epoch: u64,
) -> Result<VerifiedKeyBinding, &'static str> {
    if record.schema != KEY_BINDING_SCHEMA {
        return Err("unsupported_key_binding_schema");
    }
    if record.key_epoch == 0 || record.key_epoch < minimum_epoch {
        return Err("key_binding_epoch_rollback");
    }
    if record.expires_at <= record.issued_at
        || record.expires_at - record.issued_at > MAX_KEY_BINDING_LIFETIME_SECS
        || record.issued_at > now.saturating_add(MAX_CLOCK_SKEW_SECS)
        || record.expires_at < now
    {
        return Err("key_binding_expired_or_invalid_time");
    }

    let identity_bytes = STANDARD_NO_PAD
        .decode(&record.identity_public_key)
        .map_err(|_| "invalid_identity_public_key_encoding")?;
    let identity_public_key = identity::PublicKey::try_decode_protobuf(&identity_bytes)
        .map_err(|_| "invalid_identity_public_key")?;
    let peer_id = PeerId::from_public_key(&identity_public_key);
    if peer_id.to_string() != record.peer_id {
        return Err("key_binding_peer_id_mismatch");
    }

    let transport_bytes = STANDARD_NO_PAD
        .decode(&record.transport_public_key)
        .map_err(|_| "invalid_transport_public_key_encoding")?;
    let transport_public_key: [u8; 32] = transport_bytes
        .try_into()
        .map_err(|_| "invalid_transport_public_key_length")?;

    let signature = STANDARD_NO_PAD
        .decode(&record.signature)
        .map_err(|_| "invalid_key_binding_signature_encoding")?;
    if !identity_public_key.verify(&unsigned_bytes(record)?, &signature) {
        return Err("key_binding_signature_invalid");
    }

    Ok(VerifiedKeyBinding {
        peer_id,
        transport_public_key,
        key_epoch: record.key_epoch,
        identity_public_key,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn signed_key_binding_round_trips_and_binds_peer_identity() {
        let identity = identity::Keypair::generate_ed25519();
        let transport = TransportKeyPair::generate();
        let record = create_key_binding(&identity, &transport.public_key, 1, 100, 200).unwrap();
        let verified = verify_key_binding(&record, 150, 1).unwrap();
        assert_eq!(verified.peer_id, PeerId::from(identity.public()));
        assert_eq!(verified.transport_public_key, transport.public_key);
        assert_eq!(verified.key_epoch, 1);
    }

    #[test]
    fn rejects_tampered_transport_key() {
        let identity = identity::Keypair::generate_ed25519();
        let transport = TransportKeyPair::generate();
        let mut record = create_key_binding(&identity, &transport.public_key, 1, 100, 200).unwrap();
        record.transport_public_key = STANDARD_NO_PAD.encode([7_u8; 32]);
        assert_eq!(
            verify_key_binding(&record, 150, 1).err().unwrap(),
            "key_binding_signature_invalid"
        );
    }

    #[test]
    fn rejects_epoch_rollback_and_expired_binding() {
        let identity = identity::Keypair::generate_ed25519();
        let transport = TransportKeyPair::generate();
        let record = create_key_binding(&identity, &transport.public_key, 2, 100, 200).unwrap();
        assert_eq!(
            verify_key_binding(&record, 150, 3).err().unwrap(),
            "key_binding_epoch_rollback"
        );
        assert_eq!(
            verify_key_binding(&record, 201, 1).err().unwrap(),
            "key_binding_expired_or_invalid_time"
        );
    }

    #[test]
    fn rejects_peer_id_substitution() {
        let identity = identity::Keypair::generate_ed25519();
        let other = identity::Keypair::generate_ed25519();
        let transport = TransportKeyPair::generate();
        let mut record = create_key_binding(&identity, &transport.public_key, 1, 100, 200).unwrap();
        record.peer_id = PeerId::from(other.public()).to_string();
        assert_eq!(
            verify_key_binding(&record, 150, 1).err().unwrap(),
            "key_binding_peer_id_mismatch"
        );
    }
}
