//! Bounded receipt-event framing. Only call after PendingReceipts::accept succeeds.
//! Transport must be a supervisor-controlled Unix socket; sending is best effort,
//! not an acknowledgement of durable Python consumption.
use serde::Serialize;
use sha2::{Digest, Sha256};
use crate::delivery_receipt::DeliveryReceipt;

const SCHEMA: &str = "osiris.p2p.verified-receipt-event.v1";
const MAX_FRAME_BYTES: usize = 8192;

#[derive(Serialize)]
struct VerifiedEvent<'a> {
    schema: &'static str,
    event_id: String,
    sender_peer_id: &'a str,
    recipient_peer_id: &'a str,
    message_id: &'a str,
    envelope_sha256: &'a str,
    accepted_at: u64,
}

pub fn encode(receipt: &DeliveryReceipt) -> Result<Vec<u8>, &'static str> {
    // Stable ID across retransmission of an identical signed receipt.
    // Domain separation avoids reusing a digest from unrelated messages.
    let mut hash = Sha256::new();
    hash.update(b"OSIRIS-VERIFIED-RECEIPT-EVENT-v1\\0");
    hash.update(serde_json::to_vec(receipt).map_err(|_| "receipt_event_encode_failed")?);
    let event = VerifiedEvent {
        schema: SCHEMA,
        event_id: format!("{:x}", hash.finalize()),
        sender_peer_id: &receipt.sender_peer_id,
        recipient_peer_id: &receipt.recipient_peer_id,
        message_id: &receipt.message_id,
        envelope_sha256: &receipt.envelope_sha256,
        accepted_at: receipt.accepted_at,
    };
    let payload = serde_json::to_vec(&event).map_err(|_| "receipt_event_encode_failed")?;
    if payload.is_empty() || payload.len() > MAX_FRAME_BYTES {
        return Err("receipt_event_too_large");
    }
    let mut framed = Vec::with_capacity(4 + payload.len());
    framed.extend_from_slice(&(payload.len() as u32).to_be_bytes());
    framed.extend_from_slice(&payload);
    Ok(framed)
}

#[cfg(unix)]
pub fn send_to_socket(path: &std::path::Path, receipt: &DeliveryReceipt) -> Result<(), Box<dyn std::error::Error>> {
    use std::{io::Write, os::unix::net::UnixStream, time::Duration};
    let bytes = encode(receipt)?;
    let mut stream = UnixStream::connect(path)?;
    stream.set_write_timeout(Some(Duration::from_secs(2)))?;
    stream.write_all(&bytes)?;
    // No durable acknowledgement protocol yet. Do not mark delivery on write.
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn deterministic_bounded_frame() {
        let receipt = DeliveryReceipt {
            schema: "osiris.p2p.delivery-receipt.v1".into(),
            sender_peer_id: "sender".into(),
            recipient_peer_id: "recipient".into(),
            message_id: "message".into(),
            envelope_sha256: "a".repeat(64),
            accepted_at: 1000,
            signature: "signed".into(),
        };
        let first = encode(&receipt).unwrap();
        assert_eq!(first, encode(&receipt).unwrap());
        let length = u32::from_be_bytes(first[..4].try_into().unwrap()) as usize;
        assert_eq!(length, first.len() - 4);
        let json: serde_json::Value = serde_json::from_slice(&first[4..]).unwrap();
        assert_eq!(json["schema"], SCHEMA);
        assert_eq!(json["sender_peer_id"], "sender");
    }
}
