//! Exercise the real receipt verifier and spool. This is not a second verifier
//! and it has no path that marks an event durable without `PendingReceipts::verify`
//! or a later flush of rows that verify already spooled.
//!
//! `flush` resends unacked spool rows after a restart. It does not re-check the
//! original signature; the spool file is trusted local state of this service user.

use std::{
    env,
    process::ExitCode,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

use base64::{engine::general_purpose::STANDARD_NO_PAD, Engine};
use libp2p::{identity, PeerId};
use osiris_p2p_node::delivery_receipt::{
    create_receipt, AdmissionPolicy, DeliveryReceipt, PendingReceipts, VerifiedReceiptEvent,
};
use osiris_p2p_node::verified_receipt_bridge::VerifiedReceiptBridge;
use serde::Serialize;

#[derive(Serialize)]
struct Report {
    result: String,
    reason: Option<String>,
    event_id: Option<String>,
    sender_peer_id: String,
    recipient_peer_id: String,
    message_id: String,
    envelope_sha256: String,
    unacked: i64,
    disposition: Option<String>,
    second_disposition: Option<String>,
}

struct Fixture {
    sender: PeerId,
    recipient_key: identity::Keypair,
    recipient: PeerId,
    message_id: String,
    digest: String,
}

fn fixture() -> Result<Fixture, String> {
    let sender_key = identity::Keypair::ed25519_from_bytes([3u8; 32])
        .map_err(|_| "sender_key_invalid".to_string())?;
    let recipient_key = identity::Keypair::ed25519_from_bytes([9u8; 32])
        .map_err(|_| "recipient_key_invalid".to_string())?;
    let sender = PeerId::from_public_key(&sender_key.public());
    let recipient = PeerId::from_public_key(&recipient_key.public());
    Ok(Fixture {
        sender,
        recipient_key,
        recipient,
        message_id: STANDARD_NO_PAD.encode([7u8; 16]),
        digest: "ab".repeat(32),
    })
}

fn args() -> Result<(String, Option<String>, Option<String>, Option<i32>), String> {
    let mut scenario = None;
    let mut socket = None;
    let mut spool = None;
    let mut listener_pid = None;
    let mut values = env::args().skip(1);
    while let Some(flag) = values.next() {
        let value = values
            .next()
            .ok_or_else(|| format!("missing value for {flag}"))?;
        match flag.as_str() {
            "--scenario" => scenario = Some(value),
            "--socket" => socket = Some(value),
            "--spool" => spool = Some(value),
            "--listener-pid" => {
                if !value.bytes().all(|byte| byte.is_ascii_digit()) || value == "0" {
                    return Err("invalid_receipt_listener_pid".into());
                }
                listener_pid = Some(
                    value
                        .parse::<i32>()
                        .map_err(|_| "invalid_receipt_listener_pid".to_string())?,
                );
            }
            _ => return Err("unknown_argument".into()),
        }
    }
    Ok((
        scenario.ok_or("scenario_required")?,
        socket,
        spool,
        listener_pid,
    ))
}

fn now_secs() -> Result<u64, String> {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs())
        .map_err(|_| "clock_invalid".to_string())
}

fn signed(fixture: &Fixture, digest: &str, accepted_at: u64) -> Result<DeliveryReceipt, String> {
    create_receipt(
        &fixture.recipient_key,
        &fixture.sender,
        &fixture.message_id,
        digest,
        accepted_at,
    )
    .map_err(|error| error.to_string())
}

fn verify(
    pending: &PendingReceipts,
    fixture: &Fixture,
    receipt: &DeliveryReceipt,
    source: &PeerId,
    identity: &identity::PublicKey,
    accepted_at: u64,
    policy: AdmissionPolicy,
) -> Result<VerifiedReceiptEvent, String> {
    pending
        .verify(
            receipt,
            source,
            &fixture.sender,
            identity,
            accepted_at,
            Instant::now(),
            policy,
        )
        .map_err(|error| error.to_string())
}

fn main() -> ExitCode {
    match run() {
        Ok(report) => {
            println!(
                "{}",
                serde_json::to_string(&report).unwrap_or_else(|_| "{}".into())
            );
            ExitCode::SUCCESS
        }
        Err(reason) => {
            eprintln!("verified_receipt_bridge_error={reason}");
            println!("{{\"result\":\"error\",\"reason\":\"{reason}\"}}");
            ExitCode::from(1)
        }
    }
}

fn run() -> Result<Report, String> {
    let (scenario, socket, spool, listener_pid) = args()?;
    let fixture = fixture()?;
    if scenario == "print" {
        return Ok(report(&fixture, "printed", None, None, 0, None, None));
    }
    let socket = socket.ok_or("socket_required")?;
    let spool = spool.ok_or("spool_required")?;
    let listener_pid = listener_pid.ok_or("listener_pid_required")?;
    let mut bridge = VerifiedReceiptBridge::open(socket.as_ref(), spool.as_ref(), listener_pid)?;
    let accepted_at = now_secs()?;
    let mut pending = PendingReceipts::new();
    let timeout = Duration::from_secs(5);

    let outcome = match scenario.as_str() {
        "bad-signature" => {
            let mut receipt = signed(&fixture, &fixture.digest, accepted_at)?;
            receipt.signature = STANDARD_NO_PAD.encode([0u8; 64]);
            pending
                .track(
                    fixture.recipient,
                    fixture.message_id.clone(),
                    fixture.digest.clone(),
                    Instant::now(),
                )
                .map_err(|error| error.to_string())?;
            let reason = verify(
                &pending,
                &fixture,
                &receipt,
                &fixture.recipient,
                &fixture.recipient_key.public(),
                accepted_at,
                AdmissionPolicy::allow_local(),
            )
            .err()
            .ok_or("invalid_signature_was_accepted")?;
            ("rejected", Some(reason), None, None, None)
        }
        "wrong-peer" => {
            let intruder = identity::Keypair::ed25519_from_bytes([4u8; 32])
                .map_err(|_| "intruder_key_invalid".to_string())?;
            let intruder_peer = PeerId::from_public_key(&intruder.public());
            let receipt = signed(&fixture, &fixture.digest, accepted_at)?;
            pending
                .track(
                    fixture.recipient,
                    fixture.message_id.clone(),
                    fixture.digest.clone(),
                    Instant::now(),
                )
                .map_err(|error| error.to_string())?;
            let reason = verify(
                &pending,
                &fixture,
                &receipt,
                &intruder_peer,
                &intruder.public(),
                accepted_at,
                AdmissionPolicy::allow_local(),
            )
            .err()
            .ok_or("wrong_peer_was_accepted")?;
            ("rejected", Some(reason), None, None, None)
        }
        "wrong-digest" => {
            let receipt = signed(&fixture, &"cd".repeat(32), accepted_at)?;
            pending
                .track(
                    fixture.recipient,
                    fixture.message_id.clone(),
                    fixture.digest.clone(),
                    Instant::now(),
                )
                .map_err(|error| error.to_string())?;
            let reason = verify(
                &pending,
                &fixture,
                &receipt,
                &fixture.recipient,
                &fixture.recipient_key.public(),
                accepted_at,
                AdmissionPolicy::allow_local(),
            )
            .err()
            .ok_or("wrong_digest_was_accepted")?;
            ("rejected", Some(reason), None, None, None)
        }
        "policy-denied" => {
            let receipt = signed(&fixture, &fixture.digest, accepted_at)?;
            pending
                .track(
                    fixture.recipient,
                    fixture.message_id.clone(),
                    fixture.digest.clone(),
                    Instant::now(),
                )
                .map_err(|error| error.to_string())?;
            let reason = verify(
                &pending,
                &fixture,
                &receipt,
                &fixture.recipient,
                &fixture.recipient_key.public(),
                accepted_at,
                AdmissionPolicy {
                    authenticated_source_allowed: false,
                    propagation_source_allowed: true,
                },
            )
            .err()
            .ok_or("denied_policy_was_accepted")?;
            ("rejected", Some(reason), None, None, None)
        }
        "valid" | "prepare" | "channel-down" | "replay" => {
            let receipt = signed(&fixture, &fixture.digest, accepted_at)?;
            pending
                .track(
                    fixture.recipient,
                    fixture.message_id.clone(),
                    fixture.digest.clone(),
                    Instant::now(),
                )
                .map_err(|error| error.to_string())?;
            let event = verify(
                &pending,
                &fixture,
                &receipt,
                &fixture.recipient,
                &fixture.recipient_key.public(),
                accepted_at,
                AdmissionPolicy::allow_local(),
            )?;
            // Spool before consuming the in-memory attempt. A crash in between
            // retries the same deterministic event id.
            bridge.enqueue(&event)?;
            pending
                .commit_verified(&event, Instant::now())
                .map_err(|error| error.to_string())?;
            match scenario.as_str() {
                "prepare" => ("spooled", None, Some(event.event_id), None, None),
                "channel-down" => match bridge.transmit(&event, timeout) {
                    Err(reason) if reason.starts_with("receipt_channel_unavailable") => {
                        ("deferred", Some(reason), Some(event.event_id), None, None)
                    }
                    Ok(_) => return Err("channel_unexpectedly_accepted".into()),
                    Err(reason) => return Err(reason),
                },
                "replay" => {
                    let first = bridge.transmit(&event, timeout)?;
                    let replay = verify(
                        &pending,
                        &fixture,
                        &receipt,
                        &fixture.recipient,
                        &fixture.recipient_key.public(),
                        accepted_at,
                        AdmissionPolicy::allow_local(),
                    );
                    if replay.is_ok() {
                        return Err("replay_was_accepted".into());
                    }
                    let second = bridge.transmit(&event, timeout)?;
                    (
                        "replayed",
                        replay.err(),
                        Some(event.event_id),
                        Some(first),
                        Some(second),
                    )
                }
                _ => match bridge.transmit(&event, timeout) {
                    Ok(disposition) => (
                        "applied",
                        None,
                        Some(event.event_id),
                        Some(disposition),
                        None,
                    ),
                    // The verified event is already durable in the spool. A missing
                    // ack must not drop it or report ledger success.
                    Err(reason) => ("retained", Some(reason), Some(event.event_id), None, None),
                },
            }
        }
        "flush" => {
            let events = bridge.pending_events(8)?;
            if events.is_empty() {
                ("idle", None, None, None, None)
            } else if events.len() == 1 {
                let event = &events[0];
                let disposition = bridge.transmit(event, timeout)?;
                (
                    "flushed",
                    None,
                    Some(event.event_id.clone()),
                    Some(disposition),
                    None,
                )
            } else {
                return Err("unexpected_spool_batch".into());
            }
        }
        _ => return Err("unknown_scenario".into()),
    };
    let unacked = bridge.unacked_count()?;
    let (result, reason, event_id, disposition, second) = outcome;
    Ok(Report {
        result: result.to_owned(),
        reason,
        event_id,
        sender_peer_id: fixture.sender.to_string(),
        recipient_peer_id: fixture.recipient.to_string(),
        message_id: fixture.message_id,
        envelope_sha256: fixture.digest,
        unacked,
        disposition,
        second_disposition: second,
    })
}

fn report(
    fixture: &Fixture,
    result: &str,
    reason: Option<String>,
    event_id: Option<String>,
    unacked: i64,
    disposition: Option<String>,
    second: Option<String>,
) -> Report {
    Report {
        result: result.to_owned(),
        reason,
        event_id,
        sender_peer_id: fixture.sender.to_string(),
        recipient_peer_id: fixture.recipient.to_string(),
        message_id: fixture.message_id.clone(),
        envelope_sha256: fixture.digest.clone(),
        unacked,
        disposition,
        second_disposition: second,
    }
}
