//! Authenticated recipient-key binding primitives for OSIRIS P2P.
//!
//! Recipient-addressed application encryption for the live GossipSub path.
//! A key announcement is not a trust grant: callers must apply local
//! enrollment and revocation policy after cryptographic verification.

use base64::{engine::general_purpose::STANDARD_NO_PAD, Engine as _};
use chacha20poly1305::{aead::{Aead, Payload}, ChaCha20Poly1305, KeyInit, Nonce};
use hkdf::Hkdf;
use libp2p::{identity, PeerId};
use rand_core::{OsRng, RngCore};
use sha2::Sha256;
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

    pub fn from_private_bytes(bytes: [u8; 32]) -> Self {
        let private_key = StaticSecret::from(bytes);
        let public_key = x25519_dalek::PublicKey::from(&private_key).to_bytes();
        Self { private_key, public_key }
    }

    pub fn private_bytes(&self) -> [u8; 32] {
        self.private_key.to_bytes()
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
    fn recipient_envelope_round_trips_and_rejects_wrong_recipient() {
        let sender = identity::Keypair::generate_ed25519();
        let sender_transport = TransportKeyPair::generate();
        let recipient = identity::Keypair::generate_ed25519();
        let recipient_transport = TransportKeyPair::generate();
        let sender_record = create_key_binding(&sender, &sender_transport.public_key, 1, 100, 200).unwrap();
        let sender_binding = verify_key_binding(&sender_record, 150, 1).unwrap();
        let recipient_peer = PeerId::from(recipient.public());
        let envelope = encrypt_for_recipient(&sender, 1, &recipient_peer.to_string(), &recipient_transport.public_key, 1, b"observation", 150, 180).unwrap();
        let plaintext = decrypt_for_recipient(&envelope, &sender_binding, &recipient_peer, 1, recipient_transport.private_key(), 160).unwrap();
        assert_eq!(plaintext, b"observation");
        let wrong_peer = PeerId::random();
        assert_eq!(decrypt_for_recipient(&envelope, &sender_binding, &wrong_peer, 1, recipient_transport.private_key(), 160).err().unwrap(), "envelope_identity_or_time_check_failed");
    }

    #[test]
    fn rejects_tampered_ciphertext_before_decryption() {
        let sender = identity::Keypair::generate_ed25519();
        let sender_transport = TransportKeyPair::generate();
        let recipient = identity::Keypair::generate_ed25519();
        let recipient_transport = TransportKeyPair::generate();
        let sender_record = create_key_binding(&sender, &sender_transport.public_key, 1, 100, 200).unwrap();
        let sender_binding = verify_key_binding(&sender_record, 150, 1).unwrap();
        let recipient_peer = PeerId::from(recipient.public());
        let mut envelope = encrypt_for_recipient(&sender, 1, &recipient_peer.to_string(), &recipient_transport.public_key, 1, b"observation", 150, 180).unwrap();
        envelope.ciphertext = STANDARD_NO_PAD.encode(b"tampered");
        assert_eq!(decrypt_for_recipient(&envelope, &sender_binding, &recipient_peer, 1, recipient_transport.private_key(), 160).err().unwrap(), "envelope_signature_invalid");
    }

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


const ENCRYPTED_ENVELOPE_SCHEMA: &str = "osiris.p2p.encrypted-envelope.v1";
const MAX_ENVELOPE_LIFETIME_SECS: u64 = 24 * 60 * 60;

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct EncryptedEnvelope {
    pub schema: String,
    pub sender_peer_id: String,
    pub recipient_peer_id: String,
    pub sender_key_epoch: u64,
    pub recipient_key_epoch: u64,
    pub ephemeral_public_key: String,
    pub salt: String,
    pub nonce: String,
    pub message_id: String,
    pub created_at: u64,
    pub expires_at: u64,
    pub ciphertext: String,
    pub signature: String,
}

#[derive(Serialize)]
struct EnvelopeAad<'a> {
    schema: &'a str,
    sender_peer_id: &'a str,
    recipient_peer_id: &'a str,
    sender_key_epoch: u64,
    recipient_key_epoch: u64,
    ephemeral_public_key: &'a str,
    salt: &'a str,
    nonce: &'a str,
    message_id: &'a str,
    created_at: u64,
    expires_at: u64,
}

fn envelope_aad(envelope: &EncryptedEnvelope) -> Result<Vec<u8>, &'static str> {
    serde_json::to_vec(&EnvelopeAad {
        schema: &envelope.schema,
        sender_peer_id: &envelope.sender_peer_id,
        recipient_peer_id: &envelope.recipient_peer_id,
        sender_key_epoch: envelope.sender_key_epoch,
        recipient_key_epoch: envelope.recipient_key_epoch,
        ephemeral_public_key: &envelope.ephemeral_public_key,
        salt: &envelope.salt,
        nonce: &envelope.nonce,
        message_id: &envelope.message_id,
        created_at: envelope.created_at,
        expires_at: envelope.expires_at,
    }).map_err(|_| "envelope_aad_serialization_failed")
}

fn derive_message_key(
    shared_secret: &[u8; 32],
    salt: &[u8],
    sender_peer_id: &str,
    recipient_peer_id: &str,
    sender_epoch: u64,
    recipient_epoch: u64,
    message_id: &str,
) -> Result<[u8; 32], &'static str> {
    if shared_secret.iter().all(|byte| *byte == 0) {
        return Err("invalid_low_order_shared_secret");
    }
    let hkdf = Hkdf::<Sha256>::new(Some(salt), shared_secret);
    let info = format!(
        "OSIRIS-EYE-P2P-E2EE-v1|{sender_peer_id}|{recipient_peer_id}|{sender_epoch}|{recipient_epoch}|{message_id}"
    );
    let mut key = [0u8; 32];
    hkdf.expand(info.as_bytes(), &mut key).map_err(|_| "hkdf_expand_failed")?;
    Ok(key)
}

/// Encrypt one payload for exactly one recipient. Call once per recipient; do not reuse envelopes.
pub fn encrypt_for_recipient(
    sender_identity: &identity::Keypair,
    sender_key_epoch: u64,
    recipient_peer_id: &str,
    recipient_transport_public_key: &[u8; 32],
    recipient_key_epoch: u64,
    plaintext: &[u8],
    created_at: u64,
    expires_at: u64,
) -> Result<EncryptedEnvelope, &'static str> {
    if plaintext.len() > 1_048_576
        || sender_key_epoch == 0
        || recipient_key_epoch == 0
        || expires_at <= created_at
        || expires_at - created_at > MAX_ENVELOPE_LIFETIME_SECS
    {
        return Err("invalid_envelope_input");
    }

    let sender_peer_id = PeerId::from_public_key(&sender_identity.public()).to_string();
    let ephemeral_secret = StaticSecret::random_from_rng(OsRng);
    let ephemeral_public = x25519_dalek::PublicKey::from(&ephemeral_secret);
    let recipient_public = x25519_dalek::PublicKey::from(*recipient_transport_public_key);
    let shared = ephemeral_secret.diffie_hellman(&recipient_public);
    let mut salt = [0u8; 32];
    let mut nonce = [0u8; 12];
    let mut random_id = [0u8; 16];
    OsRng.fill_bytes(&mut salt);
    OsRng.fill_bytes(&mut nonce);
    OsRng.fill_bytes(&mut random_id);

    let mut envelope = EncryptedEnvelope {
        schema: ENCRYPTED_ENVELOPE_SCHEMA.to_owned(),
        sender_peer_id,
        recipient_peer_id: recipient_peer_id.to_owned(),
        sender_key_epoch,
        recipient_key_epoch,
        ephemeral_public_key: STANDARD_NO_PAD.encode(ephemeral_public.to_bytes()),
        salt: STANDARD_NO_PAD.encode(salt),
        nonce: STANDARD_NO_PAD.encode(nonce),
        message_id: STANDARD_NO_PAD.encode(random_id),
        created_at,
        expires_at,
        ciphertext: String::new(),
        signature: String::new(),
    };
    let aad = envelope_aad(&envelope)?;
    let key = derive_message_key(
        shared.as_bytes(), &salt, &envelope.sender_peer_id, recipient_peer_id,
        sender_key_epoch, recipient_key_epoch, &envelope.message_id,
    )?;
    let cipher = ChaCha20Poly1305::new_from_slice(&key).map_err(|_| "invalid_aead_key")?;
    let ciphertext = cipher.encrypt(Nonce::from_slice(&nonce), Payload { msg: plaintext, aad: &aad })
        .map_err(|_| "envelope_encryption_failed")?;
    envelope.ciphertext = STANDARD_NO_PAD.encode(ciphertext);
    let mut signed = aad;
    signed.extend_from_slice(&STANDARD_NO_PAD.decode(&envelope.ciphertext).map_err(|_| "ciphertext_encoding_failed")?);
    let signature = sender_identity.sign(&signed).map_err(|_| "envelope_sign_failed")?;
    envelope.signature = STANDARD_NO_PAD.encode(signature);
    Ok(envelope)
}

/// Verify sender binding/signature and recipient/epoch before decrypting.
pub fn decrypt_for_recipient(
    envelope: &EncryptedEnvelope,
    sender_binding: &VerifiedKeyBinding,
    recipient_peer_id: &PeerId,
    recipient_key_epoch: u64,
    recipient_private_key: &StaticSecret,
    now: u64,
) -> Result<Vec<u8>, &'static str> {
    if envelope.schema != ENCRYPTED_ENVELOPE_SCHEMA
        || envelope.sender_peer_id != sender_binding.peer_id.to_string()
        || envelope.sender_key_epoch != sender_binding.key_epoch
        || envelope.recipient_peer_id != recipient_peer_id.to_string()
        || envelope.recipient_key_epoch != recipient_key_epoch
        || envelope.created_at > now.saturating_add(MAX_CLOCK_SKEW_SECS)
        || envelope.expires_at < now
        || envelope.expires_at <= envelope.created_at
        || envelope.expires_at - envelope.created_at > MAX_ENVELOPE_LIFETIME_SECS
        || envelope.message_id.is_empty()
        || envelope.message_id.len() > 64
    {
        return Err("envelope_identity_or_time_check_failed");
    }

    let ephemeral_bytes = STANDARD_NO_PAD.decode(&envelope.ephemeral_public_key)
        .map_err(|_| "invalid_ephemeral_key_encoding")?;
    let ephemeral_bytes: [u8; 32] = ephemeral_bytes.try_into()
        .map_err(|_| "invalid_ephemeral_key_length")?;
    let salt = STANDARD_NO_PAD.decode(&envelope.salt).map_err(|_| "invalid_salt_encoding")?;
    if salt.len() != 32 { return Err("invalid_salt_length"); }
    let nonce = STANDARD_NO_PAD.decode(&envelope.nonce).map_err(|_| "invalid_nonce_encoding")?;
    let nonce: [u8; 12] = nonce.try_into().map_err(|_| "invalid_nonce_length")?;
    let ciphertext = STANDARD_NO_PAD.decode(&envelope.ciphertext)
        .map_err(|_| "invalid_ciphertext_encoding")?;
    if ciphertext.len() > 1_048_592 { return Err("ciphertext_too_large"); }

    let aad = envelope_aad(envelope)?;
    let signature = STANDARD_NO_PAD.decode(&envelope.signature)
        .map_err(|_| "invalid_envelope_signature_encoding")?;
    let mut signed = aad.clone();
    signed.extend_from_slice(&ciphertext);
    if !sender_binding.identity_public_key.verify(&signed, &signature) {
        return Err("envelope_signature_invalid");
    }

    let ephemeral_public = x25519_dalek::PublicKey::from(ephemeral_bytes);
    let shared = recipient_private_key.diffie_hellman(&ephemeral_public);
    let key = derive_message_key(
        shared.as_bytes(), &salt, &envelope.sender_peer_id, &envelope.recipient_peer_id,
        envelope.sender_key_epoch, envelope.recipient_key_epoch, &envelope.message_id,
    )?;
    let cipher = ChaCha20Poly1305::new((&key).into());
    cipher.decrypt(Nonce::from_slice(&nonce), Payload { msg: &ciphertext, aad: &aad })
        .map_err(|_| "envelope_decryption_failed")
}
