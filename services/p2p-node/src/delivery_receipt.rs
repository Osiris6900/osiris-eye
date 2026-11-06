//! Signed evidence that an intended E2EE recipient admitted one encrypted envelope.
//!
//! Receipts identify only existing envelope/routing metadata. They do not carry
//! observation identifiers or payload digests, and do not attest to downstream
//! research storage or processing. Pending confirmations are bounded and local
//! to this process; expiration and restart leave delivery unconfirmed.

use std::{collections::HashMap, time::{Duration, Instant}};

use base64::{engine::general_purpose::STANDARD_NO_PAD, Engine as _};
use libp2p::{identity, PeerId};
use serde::{Deserialize, Serialize};

pub const RECEIPT_TOPIC: &str = "osiris/e2ee/receipts/1";
pub const MAX_RECEIPT_BYTES: usize = 4096;
const RECEIPT_SCHEMA: &str = "osiris.p2p.delivery-receipt.v1";
const SIGNING_DOMAIN: &[u8] = b"OSIRIS-EYE-P2P-DELIVERY-RECEIPT-v1\0";
const MAX_PENDING_RECEIPTS: usize = 4096;
const PENDING_RECEIPT_TTL: Duration = Duration::from_secs(120);
const MAX_RECEIPT_AGE_SECS: u64 = 120;
const MAX_FUTURE_SKEW_SECS: u64 = 30;

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DeliveryReceipt {
    pub schema: String,
    /// Original encrypted-envelope publisher, to whom this receipt is addressed.
    pub sender_peer_id: String,
    /// Intended encrypted-envelope recipient, who signs this receipt.
    pub recipient_peer_id: String,
    pub message_id: String,
    /// SHA-256 of the exact encrypted-envelope wire bytes, never the plaintext.
    pub envelope_sha256: String,
    pub accepted_at: u64,
    pub signature: String,
}

#[derive(Serialize)]
struct UnsignedReceipt<'a> {
    schema: &'a str,
    sender_peer_id: &'a str,
    recipient_peer_id: &'a str,
    message_id: &'a str,
    envelope_sha256: &'a str,
    accepted_at: u64,
}

fn signing_bytes(receipt: &DeliveryReceipt) -> Result<Vec<u8>, &'static str> {
    let unsigned = UnsignedReceipt {
        schema: &receipt.schema,
        sender_peer_id: &receipt.sender_peer_id,
        recipient_peer_id: &receipt.recipient_peer_id,
        message_id: &receipt.message_id,
        envelope_sha256: &receipt.envelope_sha256,
        accepted_at: receipt.accepted_at,
    };
    let encoded = serde_json::to_vec(&unsigned)
        .map_err(|_| "receipt_serialization_failed")?;
    let mut bytes = Vec::with_capacity(SIGNING_DOMAIN.len() + encoded.len());
    bytes.extend_from_slice(SIGNING_DOMAIN);
    bytes.extend_from_slice(&encoded);
    Ok(bytes)
}

fn validate_message_reference(message_id: &str, digest: &str) -> Result<(), &'static str> {
    if message_id.len() != 22 {
        return Err("receipt_message_id_invalid");
    }
    let decoded = STANDARD_NO_PAD.decode(message_id)
        .map_err(|_| "receipt_message_id_invalid")?;
    if decoded.len() != 16 || STANDARD_NO_PAD.encode(&decoded) != message_id {
        return Err("receipt_message_id_invalid");
    }
    if digest.len() != 64
        || !digest.bytes().all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    {
        return Err("receipt_envelope_digest_invalid");
    }
    Ok(())
}

fn parse_peer(value: &str) -> Result<PeerId, &'static str> {
    if value.is_empty() || value.len() > 128 {
        return Err("receipt_peer_id_invalid");
    }
    let peer: PeerId = value.parse().map_err(|_| "receipt_peer_id_invalid")?;
    if peer.to_string() != value {
        return Err("receipt_peer_id_invalid");
    }
    Ok(peer)
}

/// Create only after successful recipient decryption, observation validation and
/// durable replay admission. Local enrollment/revocation policy remains mandatory.
pub fn create_receipt(
    key: &identity::Keypair,
    sender: &PeerId,
    message_id: &str,
    envelope_sha256: &str,
    accepted_at: u64,
) -> Result<DeliveryReceipt, &'static str> {
    validate_message_reference(message_id, envelope_sha256)?;
    let mut receipt = DeliveryReceipt {
        schema: RECEIPT_SCHEMA.to_owned(),
        sender_peer_id: sender.to_string(),
        recipient_peer_id: PeerId::from_public_key(&key.public()).to_string(),
        message_id: message_id.to_owned(),
        envelope_sha256: envelope_sha256.to_owned(),
        accepted_at,
        signature: String::new(),
    };
    let signature = key.sign(&signing_bytes(&receipt)?)
        .map_err(|_| "receipt_sign_failed")?;
    if signature.len() != 64 {
        return Err("receipt_signature_invalid");
    }
    receipt.signature = STANDARD_NO_PAD.encode(signature);
    Ok(receipt)
}

struct PendingReceipt {
    envelope_sha256: String,
    published_at: Instant,
}

/// Successful publication attempts awaiting a single authenticated confirmation.
/// Callers must check capacity before publishing and track only publication
/// successes. Entries are never silently evicted to make room for new traffic.
pub struct PendingReceipts {
    pending: HashMap<(PeerId, String), PendingReceipt>,
}

impl PendingReceipts {
    pub fn new() -> Self {
        Self { pending: HashMap::new() }
    }

    pub fn has_capacity(&self) -> bool {
        self.pending.len() < MAX_PENDING_RECEIPTS
    }

    pub fn track(
        &mut self,
        recipient: PeerId,
        message_id: String,
        envelope_sha256: String,
        now: Instant,
    ) -> Result<(), &'static str> {
        validate_message_reference(&message_id, &envelope_sha256)?;
        if self.pending.contains_key(&(recipient, message_id.clone())) {
            return Err("receipt_attempt_already_pending");
        }
        if !self.has_capacity() {
            return Err("receipt_tracking_capacity_reached");
        }
        self.pending.insert((recipient, message_id), PendingReceipt {
            envelope_sha256,
            published_at: now,
        });
        Ok(())
    }

    /// Validate without consuming pending state on any failure. The supplied
    /// identity must come from a verified binding for the authenticated publisher;
    /// local peer enrollment/revocation policy is applied by the caller.
    pub fn accept(
        &mut self,
        receipt: &DeliveryReceipt,
        authenticated_source: &PeerId,
        local_sender: &PeerId,
        identity_key: &identity::PublicKey,
        now_secs: u64,
        now: Instant,
    ) -> Result<(), &'static str> {
        if receipt.schema != RECEIPT_SCHEMA {
            return Err("receipt_schema_invalid");
        }
        let sender = parse_peer(&receipt.sender_peer_id)?;
        let recipient = parse_peer(&receipt.recipient_peer_id)?;
        if sender != *local_sender {
            return Err("receipt_not_for_local_sender");
        }
        if recipient != *authenticated_source
            || PeerId::from_public_key(identity_key) != recipient
        {
            return Err("receipt_publisher_identity_mismatch");
        }
        validate_message_reference(&receipt.message_id, &receipt.envelope_sha256)?;
        if receipt.signature.len() != 86 {
            return Err("receipt_signature_invalid");
        }
        let signature = STANDARD_NO_PAD.decode(&receipt.signature)
            .map_err(|_| "receipt_signature_invalid")?;
        if signature.len() != 64 || STANDARD_NO_PAD.encode(&signature) != receipt.signature
            || !identity_key.verify(&signing_bytes(receipt)?, &signature)
        {
            return Err("receipt_signature_invalid");
        }
        if receipt.accepted_at > now_secs.saturating_add(MAX_FUTURE_SKEW_SECS)
            || now_secs.saturating_sub(receipt.accepted_at) > MAX_RECEIPT_AGE_SECS
        {
            return Err("receipt_time_invalid");
        }
        let pending_key = (recipient, receipt.message_id.clone());
        let pending = self.pending.get(&pending_key)
            .ok_or("receipt_no_pending_attempt")?;
        if now.saturating_duration_since(pending.published_at) >= PENDING_RECEIPT_TTL {
            return Err("receipt_attempt_expired");
        }
        if pending.envelope_sha256 != receipt.envelope_sha256 {
            return Err("receipt_envelope_digest_mismatch");
        }
        self.pending.remove(&pending_key);
        Ok(())
    }

    /// Timeout means unconfirmed delivery, not receiver rejection or message loss.
    pub fn expire(&mut self, now: Instant) -> Vec<(PeerId, String)> {
        let expired: Vec<_> = self.pending.iter()
            .filter(|(_, pending)| {
                now.saturating_duration_since(pending.published_at) >= PENDING_RECEIPT_TTL
            })
            .map(|(key, _)| key.clone())
            .collect();
        for key in &expired {
            self.pending.remove(key);
        }
        expired
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    struct Fixture {
        sender: PeerId,
        recipient_key: identity::Keypair,
        recipient: PeerId,
        message_id: String,
        digest: String,
        created: Instant,
    }

    impl Fixture {
        fn new() -> Self {
            let recipient_key = identity::Keypair::generate_ed25519();
            Self {
                sender: PeerId::random(),
                recipient: PeerId::from_public_key(&recipient_key.public()),
                recipient_key,
                message_id: STANDARD_NO_PAD.encode([7u8; 16]),
                digest: "a".repeat(64),
                created: Instant::now(),
            }
        }

        fn receipt(&self, accepted_at: u64) -> DeliveryReceipt {
            create_receipt(
                &self.recipient_key, &self.sender, &self.message_id,
                &self.digest, accepted_at,
            ).unwrap()
        }

        fn pending(&self) -> PendingReceipts {
            let mut pending = PendingReceipts::new();
            pending.track(
                self.recipient, self.message_id.clone(), self.digest.clone(), self.created,
            ).unwrap();
            pending
        }

        fn accept(
            &self, pending: &mut PendingReceipts, receipt: &DeliveryReceipt,
            now_secs: u64, elapsed_secs: u64,
        ) -> Result<(), &'static str> {
            pending.accept(
                receipt, &self.recipient, &self.sender, &self.recipient_key.public(),
                now_secs, self.created + Duration::from_secs(elapsed_secs),
            )
        }
    }

    #[test]
    fn authenticated_receipt_consumes_exact_attempt_once() {
        let fixture = Fixture::new();
        let receipt = fixture.receipt(1_000);
        let wire = serde_json::to_vec(&receipt).unwrap();
        assert!(wire.len() < MAX_RECEIPT_BYTES);
        let decoded: DeliveryReceipt = serde_json::from_slice(&wire).unwrap();
        let mut pending = fixture.pending();
        assert!(fixture.accept(&mut pending, &decoded, 1_001, 1).is_ok());
        assert_eq!(
            fixture.accept(&mut pending, &decoded, 1_001, 1),
            Err("receipt_no_pending_attempt"),
        );
    }

    #[test]
    fn wrong_source_identity_or_original_sender_preserves_pending() {
        let fixture = Fixture::new();
        let receipt = fixture.receipt(1_000);
        let attacker = identity::Keypair::generate_ed25519();
        let attacker_peer = PeerId::from_public_key(&attacker.public());
        let mut pending = fixture.pending();
        assert_eq!(pending.accept(
            &receipt, &attacker_peer, &fixture.sender, &fixture.recipient_key.public(),
            1_001, fixture.created,
        ), Err("receipt_publisher_identity_mismatch"));
        assert_eq!(pending.accept(
            &receipt, &fixture.recipient, &fixture.sender, &attacker.public(),
            1_001, fixture.created,
        ), Err("receipt_publisher_identity_mismatch"));
        assert_eq!(pending.accept(
            &receipt, &fixture.recipient, &PeerId::random(), &fixture.recipient_key.public(),
            1_001, fixture.created,
        ), Err("receipt_not_for_local_sender"));
        assert!(fixture.accept(&mut pending, &receipt, 1_001, 1).is_ok());
    }

    #[test]
    fn excluded_peer_cannot_acknowledge_public_ciphertext_metadata() {
        let fixture = Fixture::new();
        let excluded_key = identity::Keypair::generate_ed25519();
        let excluded_peer = PeerId::from_public_key(&excluded_key.public());
        // A GossipSub observer can see the envelope ID and ciphertext digest,
        // but its valid identity signature is not an intended-recipient receipt.
        let claim = create_receipt(
            &excluded_key, &fixture.sender, &fixture.message_id, &fixture.digest, 1_000,
        ).unwrap();
        let mut pending = fixture.pending();
        assert_eq!(pending.accept(
            &claim, &excluded_peer, &fixture.sender, &excluded_key.public(),
            1_001, fixture.created,
        ), Err("receipt_no_pending_attempt"));
        assert!(fixture.accept(&mut pending, &fixture.receipt(1_000), 1_001, 1).is_ok());
    }

    #[test]
    fn receipt_from_previous_process_has_no_confirmation_authority() {
        let fixture = Fixture::new();
        let mut restarted = PendingReceipts::new();
        assert_eq!(fixture.accept(&mut restarted, &fixture.receipt(1_000), 1_001, 1),
            Err("receipt_no_pending_attempt"));
        assert!(restarted.pending.is_empty());
    }

    #[test]
    fn correctly_signed_wrong_message_or_ciphertext_does_not_consume_attempt() {
        let fixture = Fixture::new();
        let mut pending = fixture.pending();
        let wrong_message = create_receipt(
            &fixture.recipient_key, &fixture.sender, &STANDARD_NO_PAD.encode([8u8; 16]),
            &fixture.digest, 1_000,
        ).unwrap();
        assert_eq!(fixture.accept(&mut pending, &wrong_message, 1_001, 1),
            Err("receipt_no_pending_attempt"));
        let wrong_digest = create_receipt(
            &fixture.recipient_key, &fixture.sender, &fixture.message_id,
            &"b".repeat(64), 1_000,
        ).unwrap();
        assert_eq!(fixture.accept(&mut pending, &wrong_digest, 1_001, 1),
            Err("receipt_envelope_digest_mismatch"));
        assert!(fixture.accept(&mut pending, &fixture.receipt(1_000), 1_001, 1).is_ok());
    }

    #[test]
    fn modified_signed_fields_and_bad_signatures_preserve_pending() {
        let fixture = Fixture::new();
        for field in ["sender", "recipient", "message", "digest", "time", "signature"] {
            let mut receipt = fixture.receipt(1_000);
            match field {
                "sender" => receipt.sender_peer_id = PeerId::random().to_string(),
                "recipient" => receipt.recipient_peer_id = PeerId::random().to_string(),
                "message" => receipt.message_id = STANDARD_NO_PAD.encode([9u8; 16]),
                "digest" => receipt.envelope_sha256 = "b".repeat(64),
                "time" => receipt.accepted_at = 1_001,
                _ => receipt.signature = STANDARD_NO_PAD.encode([0u8; 64]),
            }
            let mut pending = fixture.pending();
            assert!(fixture.accept(&mut pending, &receipt, 1_001, 1).is_err(), "{field}");
            assert!(fixture.accept(&mut pending, &fixture.receipt(1_000), 1_001, 1).is_ok());
        }
    }

    #[test]
    fn schema_lengths_encodings_and_unknown_fields_are_rejected() {
        let fixture = Fixture::new();
        for field in ["schema", "sender", "recipient", "message", "digest", "signature"] {
            let mut receipt = fixture.receipt(1_000);
            match field {
                "schema" => receipt.schema = "another.protocol".to_owned(),
                "sender" => receipt.sender_peer_id = "x".repeat(129),
                "recipient" => receipt.recipient_peer_id = "bad-peer".to_owned(),
                "message" => receipt.message_id = "AAAAAAAAAAAAAAAAAAAAAB".to_owned(),
                "digest" => receipt.envelope_sha256 = "A".repeat(64),
                _ => receipt.signature = "!".repeat(86),
            }
            let mut pending = fixture.pending();
            assert!(fixture.accept(&mut pending, &receipt, 1_001, 1).is_err(), "{field}");
            assert_eq!(pending.pending.len(), 1);
        }
        let mut wire = serde_json::to_value(fixture.receipt(1_000)).unwrap();
        wire["plaintext_payload"] = serde_json::json!("forbidden");
        assert!(serde_json::from_value::<DeliveryReceipt>(wire).is_err());
        assert!(create_receipt(
            &fixture.recipient_key, &fixture.sender, "short", &fixture.digest, 1_000,
        ).is_err());
    }

    #[test]
    fn receipt_timestamps_and_monotonic_expiry_fail_closed() {
        let fixture = Fixture::new();
        let mut pending = fixture.pending();
        assert_eq!(fixture.accept(&mut pending, &fixture.receipt(1_031), 1_000, 1),
            Err("receipt_time_invalid"));
        assert_eq!(fixture.accept(&mut pending, &fixture.receipt(879), 1_000, 1),
            Err("receipt_time_invalid"));
        assert_eq!(fixture.accept(&mut pending, &fixture.receipt(1_000), 1_001, 120),
            Err("receipt_attempt_expired"));
        assert_eq!(pending.pending.len(), 1);
        assert!(fixture.accept(&mut pending, &fixture.receipt(1_000), 1_001, 119).is_ok());
    }

    #[test]
    fn expiry_removes_only_due_attempts_and_does_not_mean_delivery_failure() {
        let fixture = Fixture::new();
        let mut pending = fixture.pending();
        let later_message = STANDARD_NO_PAD.encode([10u8; 16]);
        pending.track(
            fixture.recipient, later_message.clone(), fixture.digest.clone(),
            fixture.created + Duration::from_secs(1),
        ).unwrap();
        assert!(pending.expire(fixture.created + Duration::from_secs(119)).is_empty());
        assert_eq!(
            pending.expire(fixture.created + Duration::from_secs(120)),
            vec![(fixture.recipient, fixture.message_id.clone())],
        );
        assert_eq!(pending.pending.len(), 1);
        assert_eq!(
            pending.expire(fixture.created + Duration::from_secs(121)),
            vec![(fixture.recipient, later_message)],
        );
    }

    #[test]
    fn tracking_rejects_duplicates_and_capacity_without_evicting() {
        let fixture = Fixture::new();
        let mut pending = fixture.pending();
        assert_eq!(pending.track(
            fixture.recipient, fixture.message_id.clone(), fixture.digest.clone(), fixture.created,
        ), Err("receipt_attempt_already_pending"));
        for value in 1..MAX_PENDING_RECEIPTS {
            pending.track(
                fixture.recipient, STANDARD_NO_PAD.encode((value as u128).to_be_bytes()),
                fixture.digest.clone(), fixture.created,
            ).unwrap();
        }
        assert!(!pending.has_capacity());
        assert_eq!(pending.track(
            fixture.recipient, STANDARD_NO_PAD.encode([11u8; 16]),
            fixture.digest.clone(), fixture.created,
        ), Err("receipt_tracking_capacity_reached"));
        assert_eq!(pending.pending.len(), MAX_PENDING_RECEIPTS);
        assert!(fixture.accept(&mut pending, &fixture.receipt(1_000), 1_001, 1).is_ok());
        assert!(pending.has_capacity());
    }

    #[test]
    fn signature_domain_separates_receipts_from_unsigned_json() {
        let fixture = Fixture::new();
        let mut receipt = fixture.receipt(1_000);
        let bytes = signing_bytes(&receipt).unwrap();
        assert!(bytes.starts_with(SIGNING_DOMAIN));
        receipt.signature = STANDARD_NO_PAD.encode(
            fixture.recipient_key.sign(&bytes[SIGNING_DOMAIN.len()..]).unwrap(),
        );
        let mut pending = fixture.pending();
        assert_eq!(fixture.accept(&mut pending, &receipt, 1_001, 1),
            Err("receipt_signature_invalid"));
        assert_eq!(pending.pending.len(), 1);
    }
}
