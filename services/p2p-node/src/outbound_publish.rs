//! One shared encrypted-publication path for stdin and the opt-in unsent queue.
//!
//! Only `NoPeersSubscribedToTopic` is a retryable GossipSub publish error. In
//! particular, `AllQueuesFull` already inserted the envelope into its cache and
//! may allow later gossip delivery; treating it as unsent could mint duplicates.

use super::{
    e2ee::{encrypt_for_recipient, verify_key_binding},
    is_peer_allowed, parse_observation, Behaviour, PeerKeyStore,
};
use libp2p::{gossipsub, identity, PeerId, Swarm};
use osiris_p2p_node::{delivery_receipt::PendingReceipts, peer_metrics::PeerMetrics};
use sha2::{Digest, Sha256};
use std::{
    collections::HashSet,
    env,
    error::Error,
    fs::OpenOptions,
    io::{Read, Write},
    time::{Instant, SystemTime, UNIX_EPOCH},
};

#[derive(Debug, Eq, PartialEq)]
pub enum SendResult {
    Published,
    Unsent(&'static str),
    Rejected(&'static str),
    Ambiguous,
}

pub fn recipients() -> Result<Vec<PeerId>, &'static str> {
    let raw = if let Some(path) = env::var_os("OSIRIS_E2EE_RECIPIENTS_FILE") {
        let mut options = OpenOptions::new();
        options.read(true);
        #[cfg(target_os = "linux")]
        {
            use std::os::unix::fs::OpenOptionsExt;
            // Never block the swarm while opening a substituted FIFO, or
            // follow a final-component symlink to a different configuration.
            options.custom_flags(0x800 | 0x20000); // O_NONBLOCK | O_NOFOLLOW
        }
        #[cfg(not(target_os = "linux"))]
        if !std::fs::symlink_metadata(&path)
            .map_err(|_| "recipient_file_read_error")?
            .is_file()
        {
            return Err("recipient_file_read_error");
        }
        let file = options
            .open(path)
            .map_err(|_| "recipient_file_read_error")?;
        if !file
            .metadata()
            .map_err(|_| "recipient_file_read_error")?
            .is_file()
        {
            return Err("recipient_file_read_error");
        }
        let mut bytes = Vec::new();
        file.take(16_385)
            .read_to_end(&mut bytes)
            .map_err(|_| "recipient_file_read_error")?;
        if bytes.len() > 16_384 {
            return Err("recipient_configuration_too_large");
        }
        String::from_utf8(bytes).map_err(|_| "invalid_e2ee_recipient")?
    } else {
        match env::var("OSIRIS_E2EE_RECIPIENTS") {
            Ok(value) => value,
            Err(env::VarError::NotPresent) => String::new(),
            Err(_) => return Err("invalid_e2ee_recipient"),
        }
    };
    parse_recipients(&raw)
}

fn parse_recipients(raw: &str) -> Result<Vec<PeerId>, &'static str> {
    if raw.len() > 16_384 {
        return Err("recipient_configuration_too_large");
    }
    let mut seen = HashSet::new();
    let mut peers = Vec::new();
    for value in raw
        .split(',')
        .map(str::trim)
        .filter(|value| !value.is_empty())
    {
        if value.len() > 128 {
            return Err("invalid_e2ee_recipient");
        }
        let peer: PeerId = value.parse().map_err(|_| "invalid_e2ee_recipient")?;
        if peer.to_string() != value {
            return Err("invalid_e2ee_recipient");
        }
        if seen.insert(peer) {
            peers.push(peer);
        }
        if peers.len() > 128 {
            return Err("e2ee_recipient_count_out_of_range");
        }
    }
    if peers.is_empty() {
        return Err("e2ee_recipient_count_out_of_range");
    }
    Ok(peers)
}

pub struct Publisher<'a> {
    pub swarm: &'a mut Swarm<Behaviour>,
    pub topic: &'a gossipsub::IdentTopic,
    pub identity: &'a identity::Keypair,
    pub transport_key_epoch: u64,
    pub key_store: &'a PeerKeyStore,
    pub trusted: &'a Option<HashSet<PeerId>>,
    pub revoked: &'a Option<HashSet<PeerId>>,
    pub max_observation_age: Option<u64>,
    pub queue_enqueued_at: Option<u64>,
    pub queue_storage: Option<&'a osiris_p2p_node::outbound_queue::OutboundQueue>,
    pub receipts_enabled: bool,
    pub pending_receipts: &'a mut PendingReceipts,
    pub peer_metrics: &'a mut PeerMetrics,
}

fn publish_failure(error: &gossipsub::PublishError) -> SendResult {
    match error {
        gossipsub::PublishError::NoPeersSubscribedToTopic => {
            SendResult::Unsent("no_subscribed_peers")
        }
        // Be conservative for every other error, including cache insertion,
        // signing/serialization errors, and future library error variants.
        _ => SendResult::Ambiguous,
    }
}

impl Publisher<'_> {
    pub fn send(
        &mut self,
        recipient: PeerId,
        plaintext: &[u8],
    ) -> Result<SendResult, Box<dyn Error>> {
        let observation = match parse_observation(plaintext, self.max_observation_age) {
            Ok(observation) => observation,
            Err(reason) => {
                emit!("publish_rejected recipient={recipient} reason={reason}");
                return Ok(SendResult::Rejected(reason));
            }
        };
        if !is_peer_allowed(&recipient, self.trusted, self.revoked) {
            emit!("publish_rejected recipient={recipient} reason=recipient_not_allowed");
            return Ok(SendResult::Rejected("recipient_not_allowed"));
        }
        if self.receipts_enabled && !self.pending_receipts.has_capacity() {
            emit!("publish_deferred recipient={recipient} reason=receipt_tracking_capacity");
            return Ok(SendResult::Unsent("receipt_tracking_capacity"));
        }
        let Some(record) = self.key_store.get(&recipient.to_string())? else {
            emit!("publish_deferred recipient={recipient} reason=recipient_key_binding_missing");
            return Ok(SendResult::Unsent("recipient_key_binding_missing"));
        };
        let now = SystemTime::now().duration_since(UNIX_EPOCH)?.as_secs();
        let binding = match verify_key_binding(&record, now, record.key_epoch) {
            Ok(binding) => binding,
            Err(error) => {
                emit!("publish_rejected recipient={recipient} reason=recipient_key_binding_invalid error={error}");
                return Ok(SendResult::Rejected("recipient_key_binding_invalid"));
            }
        };
        let encrypted = match encrypt_for_recipient(
            self.identity,
            self.transport_key_epoch,
            &recipient.to_string(),
            &binding.transport_public_key,
            binding.key_epoch,
            plaintext,
            now,
            now + 3600,
        ) {
            Ok(encrypted) => encrypted,
            Err(error) => {
                emit!(
                    "publish_rejected recipient={recipient} reason=encryption_error error={error}"
                );
                return Ok(SendResult::Rejected("encryption_error"));
            }
        };
        let message_id = encrypted.message_id.clone();
        let bytes = serde_json::to_vec(&encrypted)?;
        let envelope_sha256 = format!("{:x}", Sha256::digest(&bytes));
        // SQLite/key I/O and crypto may take time. Freshness and queue age must
        // be checked at publication, not just against the batch's old clock.
        if let Err(reason) = observation.validate_for_network(self.max_observation_age) {
            return Ok(SendResult::Rejected(reason));
        }
        if let Some(enqueued) = self.queue_enqueued_at {
            let now = SystemTime::now().duration_since(UNIX_EPOCH)?.as_secs();
            if now < enqueued {
                return Ok(SendResult::Ambiguous);
            }
            if now - enqueued >= osiris_p2p_node::outbound_queue::MAX_QUEUE_AGE_SECONDS {
                return Ok(SendResult::Rejected("queue_expired"));
            }
        }
        if let Some(storage) = self.queue_storage {
            storage.check_storage()?;
        }
        match self
            .swarm
            .behaviour_mut()
            .gossipsub
            .publish(self.topic.clone(), bytes)
        {
            Ok(_) => {
                if self.receipts_enabled {
                    match self.pending_receipts.track(recipient, message_id.clone(), envelope_sha256.clone(), Instant::now()) {
                        Ok(()) => {
                            self.peer_metrics.sent(&recipient.to_string(), &message_id, Instant::now());
                            emit!("encrypted_observation_published recipient={recipient} message_id={message_id} envelope_sha256={envelope_sha256} receipts_tracked=true");
                        }
                        Err(reason) => emit!("observation_delivery_unconfirmed recipient={recipient} message_id={message_id} reason={reason}"),
                    }
                } else {
                    emit!("encrypted_observation_published recipient={recipient} message_id={message_id}");
                }
                Ok(SendResult::Published)
            }
            Err(error) => {
                emit!("encrypted_observation_deferred recipient={recipient} error={error}");
                Ok(publish_failure(&error))
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cached_or_ambiguous_failures_never_authorize_a_new_envelope() {
        assert_eq!(
            publish_failure(&gossipsub::PublishError::NoPeersSubscribedToTopic),
            SendResult::Unsent("no_subscribed_peers")
        );
        for error in [
            gossipsub::PublishError::AllQueuesFull(1),
            gossipsub::PublishError::Duplicate,
            gossipsub::PublishError::MessageTooLarge,
        ] {
            assert_eq!(publish_failure(&error), SendResult::Ambiguous);
        }
    }

    #[test]
    fn recipient_policy_is_bounded_canonical_and_deduplicated() {
        let peer = PeerId::random();
        assert_eq!(
            parse_recipients(&format!("{peer}, {peer}\n")).unwrap(),
            vec![peer]
        );
        assert!(parse_recipients("").is_err());
        assert!(parse_recipients(&format!("{peer},broken")).is_err());
        assert!(parse_recipients(&"a".repeat(16_385)).is_err());
        let many = (0..129)
            .map(|_| PeerId::random().to_string())
            .collect::<Vec<_>>()
            .join(",");
        assert!(parse_recipients(&many).is_err());
    }
}
