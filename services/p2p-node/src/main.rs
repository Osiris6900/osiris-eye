mod peer_auth;
mod revocation;
mod e2ee;
mod peer_key_store;
mod peer_cache;
mod observation_policy;

use revocation::{RevocationRegistry, RevocationSyncMessage};
use e2ee::{create_key_binding, decrypt_for_recipient, encrypt_for_recipient, verify_key_binding, EncryptedEnvelope, KeyBindingRecord, TransportKeyPair};
use peer_auth::{is_peer_allowed, parse_revoked_peers, parse_trusted_peers, read_peer_setting, run_if_peer_allowed};
use peer_cache::{CachedPeer, PeerCache};
use peer_key_store::PeerKeyStore;
use osiris_p2p_node::delivery_receipt::{
    create_receipt, AdmissionPolicy, DeliveryReceipt, PendingReceipts, MAX_RECEIPT_BYTES, RECEIPT_TOPIC,
};
use osiris_p2p_node::verified_receipt_bridge::VerifiedReceiptBridge;
use std::{collections::{HashMap, HashSet}, env, error::Error, fs::{self, OpenOptions}, io::{BufRead, BufReader as StdBufReader, Write}, net::SocketAddr, path::PathBuf, time::{Duration, Instant, SystemTime, UNIX_EPOCH}};

macro_rules! emit {
    ($($arg:tt)*) => {{
        println!($($arg)*);
        let _ = std::io::stdout().flush();
    }};
}

use futures::StreamExt;
use libp2p::{
    autonat, connection_limits, dcutr, gossipsub, identify, identity, relay, upnp,
    core::{transport::OrTransport, upgrade, Transport},
    kad::{self, store::MemoryStore},
    mdns, noise,
    swarm::{behaviour::toggle::Toggle, dial_opts::DialOpts, Config as SwarmConfig, NetworkBehaviour, SwarmEvent},
    tcp, yamux, Multiaddr, PeerId, StreamProtocol, Swarm,
};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use tokio::{io::{self, AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader}, net::{TcpListener, TcpStream}, sync::watch};

const PROTOCOL_VERSION: &str = "/osiris-eye/p2p/1.0.0";
// Dedicated DHT protocol so OSIRIS peers form their own routing table and never
// mix with the public IPFS Kademlia network.
const DHT_PROTOCOL_NAME: &str = "/osiris-eye/kad/1.0.0";
const TOPIC_NAME: &str = "osiris/observations/1";
const REVOCATION_TOPIC_NAME: &str = "osiris/revocations/1";
const KEY_BINDING_TOPIC_NAME: &str = "osiris/e2ee/key-bindings/1";
const ENCRYPTED_TOPIC_NAME: &str = "osiris/e2ee/messages/1";
const REVOCATION_SYNC_PAGE_SIZE: usize = 128;
const MAX_PENDING_REVOCATION_REQUESTS: usize = 256;
const REVOCATION_REQUEST_TTL: Duration = Duration::from_secs(120);

const MAX_REPLAY_CACHE_BYTES: u64 = 64 * 1024 * 1024;

/// A node-local, single-writer replay journal. Never prune IDs while their
/// messages could still be replayed. Concurrent writers must use separate paths.
#[derive(Debug)]
struct ReplayGuard {
    path: PathBuf,
    file: fs::File,
    expected_bytes: u64,
    seen: HashSet<String>,
}

impl ReplayGuard {
    fn open(path: PathBuf) -> Result<Self, Box<dyn Error>> {
        let mut options = OpenOptions::new();
        options.read(true).append(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let (file, created) = match options.open(&path) {
            Ok(file) => (file, true),
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {
                if fs::symlink_metadata(&path)?.file_type().is_symlink() {
                    return Err("replay cache must not be a symbolic link".into());
                }
                (OpenOptions::new().read(true).append(true).open(&path)?, false)
            }
            Err(error) => return Err(error.into()),
        };
        let metadata = file.metadata()?;
        if !metadata.is_file() {
            return Err("replay cache must be a regular file".into());
        }
        // Check BEFORE parsing; an oversized sparse/corrupt file must not be
        // scanned or allocated into memory as attacker-controlled records.
        let expected_bytes = metadata.len();
        if expected_bytes > MAX_REPLAY_CACHE_BYTES {
            return Err("replay cache exceeds 64 MiB; archive it safely before restart".into());
        }
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            file.set_permissions(fs::Permissions::from_mode(0o600))?;
        }
        if created {
            file.sync_all()?;
            #[cfg(unix)]
            {
                let parent = path.parent().filter(|parent| !parent.as_os_str().is_empty())
                    .unwrap_or_else(|| std::path::Path::new("."));
                fs::File::open(parent)?.sync_all()?;
            }
        }
        let mut seen = HashSet::new();
        let mut reader = StdBufReader::new(file.try_clone()?);
        loop {
            // Maximum wire record: 20 timestamp digits + space + 64 hex + LF.
            // A bounded reader prevents a corrupt line from allocating 64 MiB.
            let mut entry = Vec::with_capacity(86);
            let count = std::io::Read::take(&mut reader, 87).read_until(b'\n', &mut entry)?;
            if count == 0 { break; }
            if count > 86 || entry.last() != Some(&b'\n') {
                return Err("corrupt replay cache entry length; refusing to start".into());
            }
            let line = std::str::from_utf8(&entry[..entry.len() - 1])?;
            let (timestamp, id) = line.split_once(' ')
                .ok_or("corrupt replay cache entry; refusing to start")?;
            if timestamp.is_empty() || !timestamp.bytes().all(|byte| byte.is_ascii_digit()) {
                return Err("corrupt replay cache timestamp; refusing to start".into());
            }
            timestamp.parse::<u64>()
                .map_err(|_| "corrupt replay cache timestamp; refusing to start")?;
            if id.len() != 64 || !id.bytes().all(|byte| byte.is_ascii_hexdigit()) {
                return Err("corrupt replay cache message ID; refusing to start".into());
            }
            seen.insert(id.to_ascii_lowercase());
        }
        let guard = Self { path, file, expected_bytes, seen };
        guard.check_storage()?;
        Ok(guard)
    }

    fn message_id(envelope: &ObservationEnvelope) -> Result<String, Box<dyn Error>> {
        let canonical = serde_json::to_vec(&(
            &envelope.schema, &envelope.instrument, &envelope.observed_at,
            &envelope.provider, &envelope.payload_sha256,
        ))?;
        Ok(format!("{:x}", Sha256::digest(canonical)))
    }

    /// Detect journal truncation, replacement, or a second writer. Never treat
    /// filesystem errors as an empty journal or keep accepting after corruption.
    fn check_storage(&self) -> Result<(), Box<dyn Error>> {
        let metadata = self.file.metadata()?;
        let path_metadata = fs::symlink_metadata(&self.path)?;
        if !path_metadata.is_file() || metadata.len() != self.expected_bytes
            || path_metadata.len() != self.expected_bytes {
            return Err("replay cache changed outside this writer; refusing new messages".into());
        }
        #[cfg(unix)]
        {
            use std::os::unix::fs::MetadataExt;
            if metadata.dev() != path_metadata.dev() || metadata.ino() != path_metadata.ino() {
                return Err("replay cache file was replaced; refusing new messages".into());
            }
        }
        Ok(())
    }

    /// Persist before accepting. A crash after sync may reject a retry, never
    /// accept a duplicate. A failed or partial append poisons this writer until
    /// the operator repairs the journal; no IDs are evicted to recover capacity.
    fn record_digest_if_new(&mut self, id: String) -> Result<bool, Box<dyn Error>> {
        self.check_storage()?;
        if self.seen.contains(&id) { return Ok(false); }
        let timestamp = SystemTime::now().duration_since(UNIX_EPOCH)?.as_secs();
        let entry = format!("{timestamp} {id}\n");
        let next_bytes = self.expected_bytes.saturating_add(entry.len() as u64);
        if next_bytes > MAX_REPLAY_CACHE_BYTES {
            return Err("replay cache reached 64 MiB capacity; refusing new messages".into());
        }
        self.file.write_all(entry.as_bytes())?;
        self.file.sync_all()?;
        self.expected_bytes = next_bytes;
        self.seen.insert(id);
        Ok(true)
    }

    fn record_message_id_if_new(&mut self, message_id: &str) -> Result<bool, Box<dyn Error>> {
        if message_id.is_empty() || message_id.len() > 128
            || !message_id.bytes().all(|byte| byte.is_ascii_graphic()) {
            return Err("invalid replay message ID".into());
        }
        self.record_digest_if_new(format!("{:x}", Sha256::digest(message_id.as_bytes())))
    }

    fn record_if_new(&mut self, envelope: &ObservationEnvelope) -> Result<bool, Box<dyn Error>> {
        self.record_digest_if_new(Self::message_id(envelope)?)
    }
}

#[derive(Debug, Serialize, Deserialize)]
struct ObservationEnvelope {
    schema: String,
    instrument: String,
    observed_at: String,
    provider: String,
    payload_sha256: String,
    payload: String,
}

impl ObservationEnvelope {
    fn validate(&self) -> Result<(), &'static str> {
        if self.schema != "osiris.observation.v1" { return Err("unsupported_schema"); }
        if self.instrument.trim().is_empty() || self.instrument.len() > 64 { return Err("invalid_instrument"); }
        if self.provider.trim().is_empty() || self.provider.len() > 64 { return Err("invalid_provider"); }
        if self.observed_at.trim().is_empty() || self.observed_at.len() > 40 { return Err("invalid_observed_at"); }
        observation_policy::validate_timestamp(&self.observed_at, 0, None)?;
        if self.payload.len() > 1_048_576 { return Err("payload_too_large"); }
        let digest = self.payload_sha256.as_bytes();
        if digest.len() != 64 || !digest.iter().all(u8::is_ascii_hexdigit) { return Err("invalid_payload_sha256"); }
        let actual = format!("{:x}", Sha256::digest(self.payload.as_bytes()));
        if !actual.eq_ignore_ascii_case(&self.payload_sha256) { return Err("payload_hash_mismatch"); }
        Ok(())
    }

    fn validate_for_network(&self, max_age: Option<u64>) -> Result<(), &'static str> {
        self.validate()?;
        let now = SystemTime::now().duration_since(UNIX_EPOCH)
            .map_err(|_| "invalid_system_clock")?.as_secs();
        observation_policy::validate_timestamp(&self.observed_at, now, max_age)
    }
}

/// Parse and validate before any durable replay admission. Rejected market
/// observations must not consume the finite replay journal.
fn parse_observation(data: &[u8], max_age: Option<u64>) -> Result<ObservationEnvelope, &'static str> {
    if data.len() > 2 * 1024 * 1024 { return Err("observation_too_large"); }
    let observation: ObservationEnvelope = serde_json::from_slice(data)
        .map_err(|_| "invalid_json")?;
    observation.validate_for_network(max_age)?;
    Ok(observation)
}

enum EncryptedObservationError {
    Invalid(&'static str),
    Replay,
    Storage(Box<dyn Error>),
}

fn admit_decrypted_observation(
    plaintext: &[u8], replay_key: &str, max_age: Option<u64>, replay_guard: &mut ReplayGuard,
) -> Result<ObservationEnvelope, EncryptedObservationError> {
    let observation = parse_observation(plaintext, max_age)
        .map_err(EncryptedObservationError::Invalid)?;
    match replay_guard.record_message_id_if_new(replay_key)
        .map_err(EncryptedObservationError::Storage)? {
        true => Ok(observation),
        false => Err(EncryptedObservationError::Replay),
    }
}

fn local_listen_address(mut address: Multiaddr, peer_id: PeerId) -> Multiaddr {
    if !matches!(address.iter().last(), Some(libp2p::multiaddr::Protocol::P2p(peer)) if peer == peer_id) {
        address.push(libp2p::multiaddr::Protocol::P2p(peer_id));
    }
    address
}

#[cfg(test)]
mod tests {
    use super::{parse_external_addresses, peer_id_from_multiaddr, ObservationEnvelope};
    use libp2p::{identity, Multiaddr, PeerId};
    use sha2::{Digest, Sha256};
    fn valid() -> ObservationEnvelope {
        ObservationEnvelope { schema: "osiris.observation.v1".into(), instrument: "BTC-USD".into(),
            observed_at: "2026-11-03T00:00:00Z".into(), provider: "coinbase-public".into(),
            payload_sha256: format!("{:x}", Sha256::digest(b"test-payload")), payload: "test-payload".into() }
    }

    #[test]
    fn rejected_decrypted_observations_do_not_fill_replay_journal() {
        let path = replay_test_path("validated-admission");
        let mut guard = super::ReplayGuard::open(path.clone()).unwrap();
        let mut invalid_hash = valid();
        invalid_hash.payload.push_str("tampered");
        let mut stale = valid();
        stale.observed_at = "1970-01-01T00:00:00Z".into();
        for (data, max_age) in [
            (b"invalid-json".to_vec(), None),
            (serde_json::to_vec(&invalid_hash).unwrap(), None),
            (serde_json::to_vec(&stale).unwrap(), Some(300)),
        ] {
            assert!(matches!(super::admit_decrypted_observation(
                &data, "sender:message-1", max_age, &mut guard,
            ), Err(super::EncryptedObservationError::Invalid(_))));
            assert!(guard.seen.is_empty());
            assert_eq!(std::fs::metadata(&path).unwrap().len(), 0);
        }
        let data = serde_json::to_vec(&valid()).unwrap();
        assert!(super::admit_decrypted_observation(&data, "sender:message-1", None, &mut guard).is_ok());
        assert_eq!(guard.seen.len(), 1);
        assert!(matches!(super::admit_decrypted_observation(
            &data, "sender:message-1", None, &mut guard,
        ), Err(super::EncryptedObservationError::Replay)));
        drop(guard);
        let mut restarted = super::ReplayGuard::open(path.clone()).unwrap();
        assert!(matches!(super::admit_decrypted_observation(
            &data, "sender:message-1", None, &mut restarted,
        ), Err(super::EncryptedObservationError::Replay)));
        drop(restarted);
        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn observation_input_rejects_bad_json_and_validates_next_record() {
        assert_eq!(super::parse_observation(b"not-json", None).err(), Some("invalid_json"));
        let data = serde_json::to_vec(&valid()).unwrap();
        assert!(super::parse_observation(&data, None).is_ok());
    }

    #[test]
    fn listener_address_has_exactly_one_local_peer_suffix() {
        let local = PeerId::random();
        let relay = PeerId::random();
        let direct: Multiaddr = "/ip4/127.0.0.1/tcp/4001".parse().unwrap();
        assert_eq!(super::local_listen_address(direct.clone(), local).to_string(), format!("{direct}/p2p/{local}"));
        let circuit: Multiaddr = format!("{direct}/p2p/{relay}/p2p-circuit/p2p/{local}").parse().unwrap();
        assert_eq!(super::local_listen_address(circuit.clone(), local), circuit);
    }

    #[test] fn replay_guard_persists_seen_messages_across_restart() {
        let path = std::env::temp_dir().join(format!("osiris-replay-{}-{}.cache", std::process::id(),
            std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos()));
        let envelope = valid();
        {
            let mut guard = super::ReplayGuard::open(path.clone()).unwrap();
            assert!(guard.record_if_new(&envelope).unwrap());
            assert!(!guard.record_if_new(&envelope).unwrap());
        }
        {
            let mut restarted = super::ReplayGuard::open(path.clone()).unwrap();
            assert!(!restarted.record_if_new(&envelope).unwrap());
        }
        let _ = std::fs::remove_file(path);
    }
    #[test] fn encrypted_message_replay_ids_survive_restart() {
        let path = std::env::temp_dir().join(format!("osiris-e2ee-replay-{}-{}.cache", std::process::id(),
            std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos()));
        {
            let mut guard = super::ReplayGuard::open(path.clone()).unwrap();
            assert!(guard.record_message_id_if_new("encrypted-message-1").unwrap());
            assert!(!guard.record_message_id_if_new("encrypted-message-1").unwrap());
        }
        {
            let mut restarted = super::ReplayGuard::open(path.clone()).unwrap();
            assert!(!restarted.record_message_id_if_new("encrypted-message-1").unwrap());
            assert!(restarted.record_message_id_if_new("encrypted-message-2").unwrap());
        }
        let _ = std::fs::remove_file(path);
    }

    #[test] fn replay_guard_fails_closed_on_corrupt_cache() {
        let path = std::env::temp_dir().join(format!("osiris-replay-corrupt-{}.cache", std::process::id()));
        std::fs::write(&path, "not-a-valid-entry\\n").unwrap();
        assert!(super::ReplayGuard::open(path.clone()).is_err());
        let _ = std::fs::remove_file(path);
    }
    fn replay_test_path(label: &str) -> std::path::PathBuf {
        static NEXT: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
        std::env::temp_dir().join(format!("osiris-replay-{label}-{}-{}.cache",
            std::process::id(), NEXT.fetch_add(1, std::sync::atomic::Ordering::Relaxed)))
    }
    #[test] fn replay_guard_rejects_oversized_sparse_file_before_loading() {
        let path = replay_test_path("oversize");
        let file = std::fs::File::create(&path).unwrap();
        file.set_len(super::MAX_REPLAY_CACHE_BYTES + 1).unwrap();
        let error = super::ReplayGuard::open(path.clone()).unwrap_err().to_string();
        assert!(error.contains("exceeds 64 MiB"), "{error}");
        let _ = std::fs::remove_file(path);
    }
    #[test] fn replay_guard_rejects_unbounded_or_truncated_records() {
        let path = replay_test_path("malformed-record");
        for bytes in [vec![b'x'; 1024], format!("1 {}", "a".repeat(64)).into_bytes(),
            format!("+1 {}\n", "a".repeat(64)).into_bytes()] {
            std::fs::write(&path, bytes).unwrap();
            assert!(super::ReplayGuard::open(path.clone()).is_err());
        }
        let _ = std::fs::remove_file(path);
    }
    #[test] fn replay_guard_refuses_external_truncation_and_missing_path() {
        let path = replay_test_path("external-change");
        let mut guard = super::ReplayGuard::open(path.clone()).unwrap();
        assert!(guard.record_message_id_if_new("first").unwrap());
        std::fs::OpenOptions::new().write(true).open(&path).unwrap().set_len(0).unwrap();
        assert!(guard.record_message_id_if_new("second").is_err());
        std::fs::remove_file(&path).unwrap();
        assert!(guard.record_message_id_if_new("third").is_err());
        assert!(!path.exists(), "must not silently recreate a deleted replay cache");
    }
    #[cfg(unix)]
    #[test] fn replay_guard_refuses_replaced_file_with_same_length() {
        let path = replay_test_path("replacement");
        let replacement = replay_test_path("replacement-new");
        let mut guard = super::ReplayGuard::open(path.clone()).unwrap();
        assert!(guard.record_message_id_if_new("first").unwrap());
        std::fs::copy(&path, &replacement).unwrap();
        std::fs::rename(&replacement, &path).unwrap();
        assert!(guard.record_message_id_if_new("second").is_err());
        let _ = std::fs::remove_file(path);
    }
    #[test] fn replay_guard_capacity_failure_never_records_new_id() {
        let path = replay_test_path("capacity");
        let mut guard = super::ReplayGuard::open(path.clone()).unwrap();
        guard.file.set_len(super::MAX_REPLAY_CACHE_BYTES).unwrap();
        guard.expected_bytes = super::MAX_REPLAY_CACHE_BYTES;
        assert!(guard.record_message_id_if_new("at-capacity").is_err());
        assert!(guard.seen.is_empty());
        assert_eq!(std::fs::metadata(&path).unwrap().len(), super::MAX_REPLAY_CACHE_BYTES);
        let _ = std::fs::remove_file(path);
    }
    #[test] fn replay_guard_rejects_control_and_non_ascii_message_ids_without_writing() {
        let path = replay_test_path("invalid-ids");
        let mut guard = super::ReplayGuard::open(path.clone()).unwrap();
        for id in ["", "a b", "a\nb", "\0", "é"] {
            assert!(guard.record_message_id_if_new(id).is_err(), "accepted {id:?}");
        }
        assert!(guard.record_message_id_if_new(&"a".repeat(129)).is_err());
        assert!(guard.seen.is_empty());
        assert_eq!(std::fs::metadata(&path).unwrap().len(), 0);
        let _ = std::fs::remove_file(path);
    }
    #[cfg(unix)]
    #[test] fn replay_guard_creates_private_journal_and_rejects_symlink() {
        use std::os::unix::fs::{symlink, PermissionsExt};
        let path = replay_test_path("permissions");
        let link = replay_test_path("symlink");
        let guard = super::ReplayGuard::open(path.clone()).unwrap();
        assert_eq!(std::fs::metadata(&path).unwrap().permissions().mode() & 0o777, 0o600);
        symlink(&path, &link).unwrap();
        assert!(super::ReplayGuard::open(link.clone()).is_err());
        drop(guard);
        let _ = std::fs::remove_file(link);
        let _ = std::fs::remove_file(path);
    }
    #[test]
    fn encrypted_replay_is_rejected_after_decryption_and_across_restart() {
        use super::e2ee::{create_key_binding, decrypt_for_recipient, encrypt_for_recipient, verify_key_binding, TransportKeyPair};
        use libp2p::{identity, PeerId};
        use std::time::{SystemTime, UNIX_EPOCH};

        let sender = identity::Keypair::generate_ed25519();
        let sender_transport = TransportKeyPair::generate();
        let recipient = identity::Keypair::generate_ed25519();
        let recipient_transport = TransportKeyPair::generate();
        let sender_peer = PeerId::from(sender.public());
        let recipient_peer = PeerId::from(recipient.public());
        let now = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs();
        let binding_record = create_key_binding(
            &sender, &sender_transport.public_key, 1, now.saturating_sub(5), now + 300,
        ).unwrap();
        let sender_binding = verify_key_binding(&binding_record, now, 1).unwrap();
        let observation = serde_json::to_vec(&valid()).unwrap();
        let envelope = encrypt_for_recipient(
            &sender, 1, &recipient_peer.to_string(), &recipient_transport.public_key,
            1, &observation, now, now + 120,
        ).unwrap();
        let plaintext = decrypt_for_recipient(
            &envelope, &sender_binding, &recipient_peer, 1,
            recipient_transport.private_key(), now,
        ).unwrap();
        let decoded: ObservationEnvelope = serde_json::from_slice(&plaintext).unwrap();
        assert!(decoded.validate().is_ok());

        let replay_key = format!("{sender_peer}:{}", envelope.message_id);
        let path = std::env::temp_dir().join(format!(
            "osiris-e2ee-acceptance-{}-{}.cache",
            std::process::id(),
            now,
        ));
        {
            let mut guard = super::ReplayGuard::open(path.clone()).unwrap();
            assert!(guard.record_message_id_if_new(&replay_key).unwrap());
            assert!(!guard.record_message_id_if_new(&replay_key).unwrap());
        }
        {
            let mut restarted = super::ReplayGuard::open(path.clone()).unwrap();
            assert!(!restarted.record_message_id_if_new(&replay_key).unwrap());
        }
        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn invalid_encrypted_envelope_is_not_committed_to_replay_cache() {
        use super::e2ee::{create_key_binding, decrypt_for_recipient, encrypt_for_recipient, verify_key_binding, TransportKeyPair};
        use libp2p::{identity, PeerId};
        use std::time::{SystemTime, UNIX_EPOCH};

        let sender = identity::Keypair::generate_ed25519();
        let sender_transport = TransportKeyPair::generate();
        let recipient = identity::Keypair::generate_ed25519();
        let recipient_transport = TransportKeyPair::generate();
        let recipient_peer = PeerId::from(recipient.public());
        let now = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs();
        let binding_record = create_key_binding(
            &sender, &sender_transport.public_key, 1, now.saturating_sub(5), now + 300,
        ).unwrap();
        let sender_binding = verify_key_binding(&binding_record, now, 1).unwrap();
        let mut envelope = encrypt_for_recipient(
            &sender, 1, &recipient_peer.to_string(), &recipient_transport.public_key,
            1, &serde_json::to_vec(&valid()).unwrap(), now, now + 120,
        ).unwrap();
        envelope.expires_at = envelope.expires_at.saturating_add(1);
        assert_eq!(
            decrypt_for_recipient(
                &envelope, &sender_binding, &recipient_peer, 1,
                recipient_transport.private_key(), now,
            ).err(),
            Some("envelope_signature_invalid"),
        );

        let path = std::env::temp_dir().join(format!(
            "osiris-e2ee-invalid-{}-{}.cache",
            std::process::id(),
            now,
        ));
        let guard = super::ReplayGuard::open(path.clone()).unwrap();
        let replay_key = format!("{}:{}", envelope.sender_peer_id, envelope.message_id);
        let replay_digest = format!("{:x}", Sha256::digest(replay_key.as_bytes()));
        assert!(!guard.seen.contains(&replay_digest));
        let _ = std::fs::remove_file(path);
    }

    #[test] fn accepts_valid_envelope() { assert!(valid().validate().is_ok()); }
    #[test] fn rejects_wrong_schema() { let mut v=valid(); v.schema="v0".into(); assert_eq!(v.validate(),Err("unsupported_schema")); }
    #[test] fn rejects_bad_digest() { let mut v=valid(); v.payload_sha256="xyz".into(); assert_eq!(v.validate(),Err("invalid_payload_sha256")); }
    #[test] fn rejects_empty_instrument() { let mut v=valid(); v.instrument.clear(); assert_eq!(v.validate(),Err("invalid_instrument")); }
    #[test] fn rejects_payload_hash_mismatch() { let mut v=valid(); v.payload.push_str("-tampered"); assert_eq!(v.validate(),Err("payload_hash_mismatch")); }
    #[test] fn rejects_oversized_payload() { let mut v=valid(); v.payload="x".repeat(1_048_577); assert_eq!(v.validate(),Err("payload_too_large")); }
    #[test] fn rejects_empty_provider() { let mut v=valid(); v.provider.clear(); assert_eq!(v.validate(),Err("invalid_provider")); }
    #[test] fn request_bound_revocation_ids_are_fixed_length_and_cursor_sensitive() {
        let peer = PeerId::random();
        let first = super::revocation_request_id(&peer, None);
        let second = super::revocation_request_id(&peer, Some("cursor"));
        assert_eq!(first.len(), 64);
        assert!(first.bytes().all(|byte| byte.is_ascii_hexdigit()));
        assert_ne!(first, second);
    }
    #[test] fn pending_revocation_requests_are_bounded() {
        let mut pending = std::collections::HashMap::new();
        for index in 0..(super::MAX_PENDING_REVOCATION_REQUESTS + 10) {
            super::remember_revocation_request(&mut pending, format!("{index:064x}"));
        }
        assert_eq!(pending.len(), super::MAX_PENDING_REVOCATION_REQUESTS);
    }
    #[test] fn reconnect_interval_uses_default_when_unset_or_invalid() {
        assert_eq!(super::parse_interval_secs(None, 15, 5, 300), 15);
        assert_eq!(super::parse_interval_secs(Some("bad"), 15, 5, 300), 15);
    }
    #[test] fn reconnect_interval_is_clamped_to_safe_bounds() {
        assert_eq!(super::parse_interval_secs(Some("1"), 15, 5, 300), 5);
        assert_eq!(super::parse_interval_secs(Some("999"), 15, 5, 300), 300);
        assert_eq!(super::parse_interval_secs(Some("45"), 15, 5, 300), 45);
    }
    #[test] fn dht_walk_interval_uses_default_when_unset_or_invalid() {
        assert_eq!(super::parse_interval_secs(None, 120, 30, 3600), 120);
        assert_eq!(super::parse_interval_secs(Some("bad"), 120, 30, 3600), 120);
    }
    #[test] fn dht_walk_interval_is_clamped_to_safe_bounds() {
        assert_eq!(super::parse_interval_secs(Some("1"), 120, 30, 3600), 30);
        assert_eq!(super::parse_interval_secs(Some("99999"), 120, 30, 3600), 3600);
        assert_eq!(super::parse_interval_secs(Some("300"), 120, 30, 3600), 300);
    }
    #[test] fn dht_protocol_is_osiris_scoped_not_public_ipfs() {
        assert_eq!(super::DHT_PROTOCOL_NAME, "/osiris-eye/kad/1.0.0");
        assert!(!super::DHT_PROTOCOL_NAME.contains("ipfs"));
    }
    #[test] fn parses_public_tcp_external_addresses() {
        let addresses = parse_external_addresses(Some("/ip4/203.0.113.10/tcp/4001,/dns4/node.example/tcp/4001")).unwrap();
        assert_eq!(addresses.len(), 2);
    }
    #[test] fn rejects_external_address_without_tcp() {
        assert!(parse_external_addresses(Some("/ip4/203.0.113.10/udp/4001")).is_err());
    }
    #[test] fn rejects_external_address_with_peer_id() {
        let peer = PeerId::random();
        let raw = format!("/ip4/203.0.113.10/tcp/4001/p2p/{peer}");
        assert!(parse_external_addresses(Some(&raw)).is_err());
    }
    #[test] fn extracts_peer_id_from_bootstrap_multiaddr() {
        let peer = PeerId::from(identity::Keypair::generate_ed25519().public());
        let address: Multiaddr = format!("/ip4/127.0.0.1/tcp/4001/p2p/{peer}").parse().unwrap();
        assert_eq!(peer_id_from_multiaddr(&address), Some(peer));
    }
    #[test] fn rejects_bootstrap_multiaddr_without_peer_id() {
        let address: Multiaddr = "/ip4/127.0.0.1/tcp/4001".parse().unwrap();
        assert_eq!(peer_id_from_multiaddr(&address), None);
    }
    #[test] fn circuit_bootstrap_identity_is_the_recipient_not_the_relay() {
        let relay = PeerId::random();
        let recipient = PeerId::random();
        let address: Multiaddr = format!("/ip4/127.0.0.1/tcp/4001/p2p/{relay}/p2p-circuit/p2p/{recipient}").parse().unwrap();
        assert_eq!(peer_id_from_multiaddr(&address), Some(recipient));
    }
    #[test] fn bootstrap_parser_preserves_recipient_identity_and_deduplicates() {
        let relay = PeerId::random();
        let recipient = PeerId::random();
        let address = format!("/ip4/127.0.0.1/tcp/4001/p2p/{relay}/p2p-circuit/p2p/{recipient}");
        let repeated = format!("{address},{address}");
        let parsed = super::parse_bootstrap_peers(Some(&repeated)).unwrap();
        assert_eq!(parsed.len(), 1);
        assert_eq!(parsed[0].0, recipient);
        assert_eq!(parsed[0].1.to_string(), address);
        assert!(super::parse_bootstrap_peers(Some("/ip4/127.0.0.1/tcp/4001")).is_err());
    }
    #[test] fn relay_reservation_endpoint_requires_explicit_host_port_and_identity() {
        let peer = PeerId::random();
        let address = format!("/ip4/127.0.0.1/tcp/4001/p2p/{peer}");
        let (actual_peer, actual_address) = super::parse_relay_address(Some(&address)).unwrap().unwrap();
        assert_eq!(actual_peer, peer);
        assert_eq!(actual_address.to_string(), address);
        assert!(super::parse_relay_address(None).unwrap().is_none());
        for invalid in ["".to_owned(), "/ip4/127.0.0.1/tcp/4001".into(),
            format!("/ip4/127.0.0.1/tcp/0/p2p/{peer}"),
            format!("{address}/p2p-circuit"),
            format!("{address}/p2p-circuit/p2p/{}", PeerId::random()),
            format!("/ip4/127.0.0.1/udp/4001/p2p/{peer}")] {
            assert!(super::parse_relay_address(Some(&invalid)).is_err(), "accepted {invalid}");
        }
    }
    #[test] fn mdns_configuration_rejects_ambiguous_values() {
        assert!(super::parse_mdns_enabled(None).unwrap());
        assert!(super::parse_mdns_enabled(Some("1")).unwrap());
        assert!(!super::parse_mdns_enabled(Some("0")).unwrap());
        assert!(super::parse_mdns_enabled(Some("false")).is_err());
        assert!(super::parse_mdns_enabled(Some("")).is_err());
    }
    #[test] fn relay_failover_parser_accepts_multiple_relays_and_deduplicates() {
        let first = PeerId::random();
        let second = PeerId::random();
        let list = format!(
            "/ip4/127.0.0.1/tcp/4001/p2p/{first}, /ip4/127.0.0.2/tcp/4002/p2p/{second}, /ip4/127.0.0.3/tcp/4003/p2p/{first}"
        );
        let relays = super::parse_relay_addresses(Some(&list)).unwrap();
        assert_eq!(relays.len(), 2);
        assert_eq!(relays[0].0, first);
        assert_eq!(relays[1].0, second);
        assert!(super::parse_relay_addresses(None).unwrap().is_empty());
    }
    #[test] fn relay_failover_parser_rejects_invalid_entry_in_list() {
        let relay = PeerId::random();
        let list = format!("/ip4/127.0.0.1/tcp/4001/p2p/{relay}, /ip4/127.0.0.1/tcp/4002");
        assert!(super::parse_relay_addresses(Some(&list)).is_err());
    }
    #[test] fn relay_readiness_needs_only_one_active_reservation() {
        let relays = super::parse_relay_addresses(Some(&format!(
            "/ip4/127.0.0.1/tcp/4001/p2p/{}, /ip4/127.0.0.2/tcp/4002/p2p/{}",
            PeerId::random(), PeerId::random()))).unwrap();
        assert!(super::relay_readiness(&[], &Default::default()).is_none());
        assert_eq!(super::relay_readiness(&relays, &Default::default()), Some(false));
        let mut active = std::collections::HashSet::new();
        active.insert(relays[1].0);
        assert_eq!(super::relay_readiness(&relays, &active), Some(true));
    }
    #[test] fn rejoin_plan_skips_self_bootstrap_relays_and_policy_blocked_peers() {
        let self_peer = PeerId::random();
        let bootstrap_peer = PeerId::random();
        let relay_peer = PeerId::random();
        let blocked_peer = PeerId::random();
        let plain_peer = PeerId::random();
        let cached: Vec<super::CachedPeer> = [
            (self_peer, 1u16), (bootstrap_peer, 2), (relay_peer, 3), (blocked_peer, 4), (plain_peer, 5),
        ].iter().map(|(peer, port)| super::CachedPeer {
            peer_id: peer.to_string(),
            addresses: vec![format!("/ip4/127.0.0.1/tcp/400{port}")],
            last_seen: 100,
            source: "dht".into(),
        }).collect();
        let bootstrap = vec![(bootstrap_peer, "/ip4/127.0.0.1/tcp/9001".parse::<Multiaddr>().unwrap())];
        let relays = vec![(relay_peer, "/ip4/127.0.0.1/tcp/9002".parse::<Multiaddr>().unwrap())];
        let mut revoked = std::collections::HashSet::new();
        revoked.insert(blocked_peer);
        let plan = super::plan_rejoin_dials(
            &cached, &self_peer, &bootstrap, &relays, &None, &Some(revoked), 16);
        assert_eq!(plan.len(), 1);
        assert_eq!(plan[0].0, plain_peer);
        assert!(super::plan_rejoin_dials(&cached, &self_peer, &bootstrap, &relays, &None, &None, 0).is_empty());
    }
    #[test] fn seed_list_parser_skips_comments_and_bad_lines() {
        let peer = PeerId::random();
        let body = format!(
            "# community seeds\n\nnot-a-multiaddr\n/ip4/127.0.0.1/tcp/4001\n/ip4/127.0.0.1/tcp/4001/p2p/{peer}\n# trailing\n"
        );
        let seeds = super::parse_seed_list(&body);
        assert_eq!(seeds.len(), 1);
        assert_eq!(seeds[0].0, peer);
    }
    #[test] fn seed_url_resolution_defaults_and_disables() {
        assert_eq!(super::resolve_seed_url(None).as_deref(), Some(super::DEFAULT_BOOTSTRAP_SEED_URL));
        assert_eq!(super::resolve_seed_url(Some("off")), None);
        assert_eq!(super::resolve_seed_url(Some("none")), None);
        assert_eq!(super::resolve_seed_url(Some("  ")), None);
        assert_eq!(super::resolve_seed_url(Some("https://example.com/seeds.txt")).as_deref(), Some("https://example.com/seeds.txt"));
    }
    #[test] fn flag_parser_accepts_only_binary_values() {
        assert!(super::parse_flag(None, "X", true).unwrap());
        assert!(!super::parse_flag(None, "X", false).unwrap());
        assert!(super::parse_flag(Some("1"), "X", false).unwrap());
        assert!(!super::parse_flag(Some("0"), "X", true).unwrap());
        assert!(super::parse_flag(Some("true"), "X", true).is_err());
    }
}

#[derive(NetworkBehaviour)]
#[behaviour(to_swarm = "BehaviourEvent")]
struct Behaviour {
    gossipsub: gossipsub::Behaviour,
    kademlia: kad::Behaviour<MemoryStore>,
    mdns: Toggle<mdns::tokio::Behaviour>,
    identify: identify::Behaviour,
    relay: relay::client::Behaviour,
    // Every node can optionally serve as a relay for others. With many nodes
    // opted in, the mesh needs no dedicated relay VPS.
    relay_server: Toggle<relay::Behaviour>,
    autonat: autonat::Behaviour,
    // NAT-to-NAT direct connections via relayed signalling (DCUtR).
    dcutr: Toggle<dcutr::Behaviour>,
    // Ask the home router for a public port mapping so a laptop node becomes
    // reachable without any manual firewall rule.
    upnp: Toggle<upnp::tokio::Behaviour>,
    connection_limits: connection_limits::Behaviour,
}

#[derive(Debug)]
enum BehaviourEvent {
    Gossipsub(gossipsub::Event),
    Kademlia(kad::Event),
    Mdns(mdns::Event),
    Identify(identify::Event),
    Relay(relay::client::Event),
    RelayServer(relay::Event),
    Autonat(autonat::Event),
    Dcutr(dcutr::Event),
    Upnp(upnp::Event),
}

impl From<gossipsub::Event> for BehaviourEvent {
    fn from(event: gossipsub::Event) -> Self { Self::Gossipsub(event) }
}
impl From<kad::Event> for BehaviourEvent {
    fn from(event: kad::Event) -> Self { Self::Kademlia(event) }
}
impl From<mdns::Event> for BehaviourEvent {
    fn from(event: mdns::Event) -> Self { Self::Mdns(event) }
}
impl From<identify::Event> for BehaviourEvent {
    fn from(event: identify::Event) -> Self { Self::Identify(event) }
}

impl From<relay::client::Event> for BehaviourEvent {
    fn from(event: relay::client::Event) -> Self { Self::Relay(event) }
}

impl From<relay::Event> for BehaviourEvent {
    fn from(event: relay::Event) -> Self { Self::RelayServer(event) }
}

impl From<autonat::Event> for BehaviourEvent {
    fn from(event: autonat::Event) -> Self { Self::Autonat(event) }
}

impl From<dcutr::Event> for BehaviourEvent {
    fn from(event: dcutr::Event) -> Self { Self::Dcutr(event) }
}

impl From<upnp::Event> for BehaviourEvent {
    fn from(event: upnp::Event) -> Self { Self::Upnp(event) }
}
impl From<std::convert::Infallible> for BehaviourEvent {
    fn from(event: std::convert::Infallible) -> Self { match event {} }
}

fn write_node_identity(path: &PathBuf) -> Result<identity::Keypair, Box<dyn Error>> {
    let key = identity::Keypair::generate_ed25519();
    let encoded = key.to_protobuf_encoding()?;
    let mut file = OpenOptions::new().write(true).create_new(true).open(path)?;
    file.write_all(&encoded)?;
    file.sync_all()?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(path, fs::Permissions::from_mode(0o600))?;
    }
    Ok(key)
}

fn load_or_create_identity() -> Result<identity::Keypair, Box<dyn Error>> {
    let path = env::var_os("OSIRIS_NODE_KEY")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(".osiris-node-key"));
    let rotate = match env::var("OSIRIS_ROTATE_NODE_KEY") {
        Ok(value) if value == "1" => true,
        Ok(value) if value == "0" => false,
        Ok(_) => return Err("OSIRIS_ROTATE_NODE_KEY must be 0 or 1".into()),
        Err(env::VarError::NotPresent) => false,
        Err(error) => return Err(error.into()),
    };

    if path.exists() && !rotate {
        let encoded = fs::read(&path)?;
        return Ok(identity::Keypair::from_protobuf_encoding(&encoded)?);
    }
    if rotate && !path.exists() {
        return Err("identity rotation requested but OSIRIS_NODE_KEY does not exist".into());
    }

    if rotate {
        let old_key = identity::Keypair::from_protobuf_encoding(&fs::read(&path)?)?;
        let old_peer = PeerId::from(old_key.public());
        let timestamp = SystemTime::now().duration_since(UNIX_EPOCH)?.as_secs();
        let mut backup_name = path.as_os_str().to_os_string();
        backup_name.push(format!(".backup-{timestamp}"));
        let backup = PathBuf::from(backup_name);
        if backup.exists() {
            return Err(format!("identity backup already exists: {}", backup.display()).into());
        }
        fs::rename(&path, &backup)?;
        match write_node_identity(&path) {
            Ok(new_key) => {
                let new_peer = PeerId::from(new_key.public());
                emit!("identity_rotated old_peer={old_peer} new_peer={new_peer} backup={}", backup.display());
                return Ok(new_key);
            }
            Err(error) => {
                let _ = fs::rename(&backup, &path);
                return Err(error);
            }
        }
    }

    let key = write_node_identity(&path)?;
    eprintln!("Created node identity at {} (keep this file private)", path.display());
    Ok(key)
}


fn load_or_create_transport_key() -> Result<TransportKeyPair, Box<dyn Error>> {
    let path = env::var_os("OSIRIS_TRANSPORT_KEY")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(".osiris-transport-key"));
    if path.exists() {
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mode = fs::metadata(&path)?.permissions().mode();
            if mode & 0o077 != 0 {
                return Err("transport key file permissions must be 0600 or stricter".into());
            }
        }
        let bytes = fs::read(&path)?;
        let key: [u8; 32] = bytes.try_into()
            .map_err(|_| "transport key file must contain exactly 32 bytes")?;
        return Ok(TransportKeyPair::from_private_bytes(key));
    }
    let key = TransportKeyPair::generate();
    let mut file = OpenOptions::new().write(true).create_new(true).open(&path)?;
    file.write_all(&key.private_bytes())?;
    file.sync_all()?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&path, fs::Permissions::from_mode(0o600))?;
    }
    Ok(key)
}

fn revocation_request_id(peer_id: &PeerId, cursor: Option<&str>) -> String {
    let now = SystemTime::now().duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_nanos()).unwrap_or_default();
    let material = format!("osiris.revocation.request.v2|{peer_id}|{now}|{}", cursor.unwrap_or(""));
    format!("{:x}", Sha256::digest(material.as_bytes()))
}

fn remember_revocation_request(pending: &mut HashMap<String, Instant>, request_id: String) {
    let now = Instant::now();
    pending.retain(|_, created| now.duration_since(*created) <= REVOCATION_REQUEST_TTL);
    if pending.len() >= MAX_PENDING_REVOCATION_REQUESTS {
        if let Some(oldest) = pending.iter().min_by_key(|(_, created)| **created).map(|(id, _)| id.clone()) {
            pending.remove(&oldest);
        }
    }
    pending.insert(request_id, now);
}

fn parse_interval_secs(raw: Option<&str>, default: u64, min: u64, max: u64) -> u64 {
    raw.and_then(|value| value.parse::<u64>().ok())
        .unwrap_or(default)
        .clamp(min, max)
}

fn peer_id_from_multiaddr(address: &Multiaddr) -> Option<PeerId> {
    address.iter().filter_map(|protocol| match protocol {
        libp2p::multiaddr::Protocol::P2p(peer_id) => Some(peer_id),
        _ => None,
    }).last()
}

fn parse_external_addresses(raw: Option<&str>) -> Result<Vec<Multiaddr>, Box<dyn Error>> {
    let Some(raw) = raw else {
        return Ok(Vec::new());
    };

    let mut addresses = Vec::new();
    for entry in raw.split(',').map(str::trim).filter(|entry| !entry.is_empty()) {
        let address: Multiaddr = entry.parse()?;
        let has_tcp = address
            .iter()
            .any(|protocol| matches!(protocol, libp2p::multiaddr::Protocol::Tcp(_)));
        let has_peer_id = peer_id_from_multiaddr(&address).is_some();
        if !has_tcp || has_peer_id {
            return Err(format!(
                "external address must contain a TCP transport and must not contain /p2p/<peer-id>: {address}"
            ).into());
        }
        if !addresses.contains(&address) {
            addresses.push(address);
        }
    }
    Ok(addresses)
}

/// Built-in fallback bootstrap entries, used only when the operator has
/// configured nothing else. The community seed file below is the maintained
/// public entry point; keep embedded seeds empty until well-known public
/// OSIRIS peers exist.
const EMBEDDED_BOOTSTRAP_SEEDS: &[&str] = &[];
const MAX_SEED_FILE_BYTES: u64 = 65_536;
const MAX_SEED_PEERS: usize = 128;
const DEFAULT_BOOTSTRAP_SEED_URL: &str =
    "https://raw.githubusercontent.com/Osiris6900/osiris-eye/main/deploy/p2p/bootstrap-peers.txt";

/// A reservation endpoint must identify the relay, not an application recipient.
/// Circuit recipient addresses belong in OSIRIS_BOOTSTRAP_PEERS instead.
fn parse_relay_address(raw: Option<&str>) -> Result<Option<(PeerId, Multiaddr)>, Box<dyn Error>> {
    let Some(raw) = raw else { return Ok(None); };
    let address: Multiaddr = raw.parse()?;
    let protocols: Vec<_> = address.iter().collect();
    let valid_host = matches!(protocols.first(), Some(libp2p::multiaddr::Protocol::Ip4(_))
        | Some(libp2p::multiaddr::Protocol::Ip6(_)));
    let valid_port = matches!(protocols.get(1), Some(libp2p::multiaddr::Protocol::Tcp(port)) if *port != 0);
    if protocols.len() != 3 || !valid_host || !valid_port {
        return Err("OSIRIS_RELAY_ADDRESS must be /ip4/<ip>/tcp/<port>/p2p/<relay-id> or /ip6/<ip>/tcp/<port>/p2p/<relay-id>".into());
    }
    let Some(libp2p::multiaddr::Protocol::P2p(peer)) = protocols.last() else {
        return Err("OSIRIS_RELAY_ADDRESS must end in /p2p/<relay-id>".into());
    };
    Ok(Some((*peer, address)))
}

/// Comma-separated relay list. Failover across several relays keeps the node
/// reachable even when any single relay dies.
fn parse_relay_addresses(raw: Option<&str>) -> Result<Vec<(PeerId, Multiaddr)>, Box<dyn Error>> {
    let Some(raw) = raw else { return Ok(Vec::new()); };
    let mut relays: Vec<(PeerId, Multiaddr)> = Vec::new();
    for entry in raw.split(',').map(str::trim).filter(|entry| !entry.is_empty()) {
        let (peer, address) = parse_relay_address(Some(entry))?
            .ok_or("relay address entry unexpectedly empty")?;
        if relays.iter().any(|(known, _)| *known == peer) { continue; }
        if relays.len() >= 16 { return Err("at most 16 relay addresses are supported".into()); }
        relays.push((peer, address));
    }
    Ok(relays)
}

fn relay_readiness(relay_addresses: &[(PeerId, Multiaddr)], active: &HashSet<PeerId>) -> Option<bool> {
    if relay_addresses.is_empty() { None } else { Some(!active.is_empty()) }
}

fn parse_mdns_enabled(raw: Option<&str>) -> Result<bool, Box<dyn Error>> {
    match raw {
        None | Some("1") => Ok(true),
        Some("0") => Ok(false),
        _ => Err("OSIRIS_MDNS_ENABLED must be 0 or 1".into()),
    }
}

fn parse_bootstrap_peers(raw: Option<&str>) -> Result<Vec<(PeerId, Multiaddr)>, Box<dyn Error>> {
    let Some(raw) = raw else { return Ok(Vec::new()); };
    let mut peers = Vec::new();
    for entry in raw.split(',').map(str::trim).filter(|entry| !entry.is_empty()) {
        let address: Multiaddr = entry.parse()?;
        let peer = match address.iter().last() {
            Some(libp2p::multiaddr::Protocol::P2p(peer)) => peer,
            _ => return Err("bootstrap multiaddr must end with /p2p/<peer-id>".into()),
        };
        if !peers.iter().any(|(known, _)| *known == peer) {
            if peers.len() >= 128 { return Err("at most 128 bootstrap peers are supported".into()); }
            peers.push((peer, address));
        }
    }
    Ok(peers)
}

/// Restart rejoin plan: cached peers are dialled again only if they are not
/// already covered by static config and still pass live trust/revocation
/// policy. The cache is discovery data, never an authorization source.
fn plan_rejoin_dials(
    cached_peers: &[CachedPeer],
    self_peer: &PeerId,
    bootstrap_peers: &[(PeerId, Multiaddr)],
    relay_addresses: &[(PeerId, Multiaddr)],
    trusted_peers: &Option<HashSet<PeerId>>,
    revoked_peers: &Option<HashSet<PeerId>>,
    dial_limit: usize,
) -> Vec<(PeerId, Vec<Multiaddr>)> {
    let mut plan = Vec::new();
    for cached in cached_peers {
        if plan.len() >= dial_limit { break; }
        let Ok(candidate) = cached.peer_id.parse::<PeerId>() else { continue; };
        if candidate == *self_peer { continue; }
        if bootstrap_peers.iter().any(|(peer, _)| *peer == candidate) { continue; }
        if relay_addresses.iter().any(|(peer, _)| *peer == candidate) { continue; }
        if !is_peer_allowed(&candidate, trusted_peers, revoked_peers) { continue; }
        let addresses: Vec<Multiaddr> = cached.addresses.iter()
            .filter_map(|raw| raw.parse::<Multiaddr>().ok())
            .take(4)
            .collect();
        if addresses.is_empty() { continue; }
        plan.push((candidate, addresses));
    }
    plan
}

fn optional_env(name: &str) -> Result<Option<String>, Box<dyn Error>> {
    match env::var(name) {
        Ok(value) => Ok(Some(value)),
        Err(env::VarError::NotPresent) => Ok(None),
        Err(error) => Err(format!("{name} must be valid Unicode: {error}").into()),
    }
}

fn parse_flag(raw: Option<&str>, setting: &str, default: bool) -> Result<bool, Box<dyn Error>> {
    match raw.map(str::trim) {
        None => Ok(default),
        Some("1") => Ok(true),
        Some("0") => Ok(false),
        _ => Err(format!("{setting} must be 0 or 1").into()),
    }
}

/// Seed list format: one multiaddress per line, `#` comments allowed, every
/// entry must end in /p2p/<peer-id>. Invalid lines are skipped so a partially
/// corrupt community file can never block startup.
fn parse_seed_list(body: &str) -> Vec<(PeerId, Multiaddr)> {
    let mut peers = Vec::new();
    for line in body.lines().map(str::trim) {
        if line.is_empty() || line.starts_with('#') { continue; }
        let Ok(address) = line.parse::<Multiaddr>() else { continue; };
        let peer = match address.iter().last() {
            Some(libp2p::multiaddr::Protocol::P2p(peer)) => peer,
            _ => continue,
        };
        if peers.iter().any(|(known, _)| *known == peer) { continue; }
        if peers.len() >= MAX_SEED_PEERS { break; }
        peers.push((peer, address));
    }
    peers
}

fn resolve_seed_url(raw: Option<&str>) -> Option<String> {
    match raw.map(str::trim) {
        None => Some(DEFAULT_BOOTSTRAP_SEED_URL.to_owned()),
        Some("") | Some("off") | Some("none") => None,
        Some(url) => Some(url.to_owned()),
    }
}

/// Fetch the community seed file. HTTPS only, no redirects, strict size cap.
/// Runs inside tokio::task::spawn_blocking at the call site.
fn fetch_seed_list(url: &str, timeout: Duration) -> Result<String, Box<dyn Error + Send + Sync>> {
    use std::io::Read as _;
    if !url.starts_with("https://") {
        return Err("bootstrap seed URL must use https://".into());
    }
    let agent = ureq::AgentBuilder::new().redirects(0).timeout(timeout).build();
    let response = agent.get(url).set("user-agent", "osiris-eye-node").call()?;
    let mut body = String::new();
    response.into_reader().take(MAX_SEED_FILE_BYTES + 1).read_to_string(&mut body)?;
    if body.len() as u64 > MAX_SEED_FILE_BYTES {
        return Err("bootstrap seed file exceeds the 64 KiB limit".into());
    }
    Ok(body)
}

#[derive(Clone, Debug, Serialize)]
struct HealthSnapshot {
    status: &'static str,
    ready: bool,
    peer_id: String,
    uptime_seconds: u64,
    connected_peers: usize,
    bootstrap_peers_connected: usize,
    bootstrap_peers_total: usize,
    relay_configured: bool,
    relay_reservation_active: bool,
    relays_total: usize,
    relay_reservations: usize,
}

fn build_health_snapshot(
    peer_id: &PeerId,
    started_at: Instant,
    swarm: &Swarm<Behaviour>,
    bootstrap_peers: &[(PeerId, Multiaddr)],
    relay_ready: Option<bool>,
    relays_total: usize,
    relay_reservations: usize,
) -> HealthSnapshot {
    let connected_peers = swarm.connected_peers().count();
    let bootstrap_peers_connected = bootstrap_peers
        .iter()
        .filter(|(bootstrap_peer, _)| swarm.is_connected(bootstrap_peer))
        .count();
    HealthSnapshot {
        status: "alive",
        ready: (bootstrap_peers.is_empty() || bootstrap_peers_connected == bootstrap_peers.len())
            && relay_ready.unwrap_or(true),
        peer_id: peer_id.to_string(),
        uptime_seconds: started_at.elapsed().as_secs(),
        connected_peers,
        bootstrap_peers_connected,
        bootstrap_peers_total: bootstrap_peers.len(),
        relay_configured: relay_ready.is_some(),
        relay_reservation_active: relay_ready.unwrap_or(false),
        relays_total,
        relay_reservations,
    }
}

async fn serve_health_connection(mut stream: TcpStream, state: watch::Receiver<HealthSnapshot>) {
    let mut request = [0_u8; 1024];
    let read = match tokio::time::timeout(Duration::from_secs(2), stream.read(&mut request)).await {
        Ok(Ok(count)) => count,
        _ => return,
    };
    let request_line = std::str::from_utf8(&request[..read])
        .ok()
        .and_then(|value| value.lines().next())
        .unwrap_or("");
    let mut parts = request_line.split_whitespace();
    let method = parts.next().unwrap_or("");
    let path = parts.next().unwrap_or("");

    let (status, body) = if method != "GET" {
        ("405 Method Not Allowed", r#"{"error":"method_not_allowed"}"#.to_owned())
    } else if path == "/health" {
        let snapshot = state.borrow().clone();
        ("200 OK", serde_json::to_string(&snapshot).unwrap_or_else(|_| r#"{"status":"error"}"#.to_owned()))
    } else if path == "/ready" {
        let snapshot = state.borrow().clone();
        let status = if snapshot.ready { "200 OK" } else { "503 Service Unavailable" };
        (status, serde_json::to_string(&snapshot).unwrap_or_else(|_| r#"{"status":"error"}"#.to_owned()))
    } else {
        ("404 Not Found", r#"{"error":"not_found"}"#.to_owned())
    };
    let response = format!(
        "HTTP/1.1 {status}\r\ncontent-type: application/json\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{body}",
        body.len()
    );
    let _ = stream.write_all(response.as_bytes()).await;
}

async fn serve_health_endpoint(
    address: SocketAddr,
    state: watch::Receiver<HealthSnapshot>,
) -> Result<(), Box<dyn Error + Send + Sync>> {
    let listener = TcpListener::bind(address).await?;
    emit!("health_http_listening={}", listener.local_addr()?);
    loop {
        let (stream, _) = listener.accept().await?;
        let state = state.clone();
        tokio::spawn(async move { serve_health_connection(stream, state).await; });
    }
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn Error>> {
    let max_observation_age = observation_policy::parse_max_age(
        read_peer_setting("OSIRIS_MAX_OBSERVATION_AGE_SECS")?.as_deref(),
    )?;
    let key = load_or_create_identity()?;
    let peer_id = PeerId::from(key.public());
    let e2ee_enabled = match env::var("OSIRIS_E2EE_ENABLED") {
        Ok(value) if value == "1" => true,
        Ok(value) if value == "0" => false,
        Ok(_) => return Err("OSIRIS_E2EE_ENABLED must be 0 or 1".into()),
        Err(env::VarError::NotPresent) => false,
        Err(error) => return Err(error.into()),
    };
    let delivery_receipts_enabled = parse_flag(
        read_peer_setting("OSIRIS_DELIVERY_RECEIPTS")?.as_deref(),
        "OSIRIS_DELIVERY_RECEIPTS", false,
    )?;
    if delivery_receipts_enabled && !e2ee_enabled {
        return Err("OSIRIS_DELIVERY_RECEIPTS requires OSIRIS_E2EE_ENABLED=1".into());
    }
    let transport_key = load_or_create_transport_key()?;
    let transport_key_epoch = match env::var("OSIRIS_TRANSPORT_KEY_EPOCH") {
        Ok(value) => value.parse::<u64>()?,
        Err(env::VarError::NotPresent) => 1,
        Err(error) => return Err(error.into()),
    };
    if transport_key_epoch == 0 { return Err("OSIRIS_TRANSPORT_KEY_EPOCH must be positive".into()); }
    let now = SystemTime::now().duration_since(UNIX_EPOCH)?.as_secs();
    let mut local_key_binding = create_key_binding(&key, &transport_key.public_key, transport_key_epoch, now, now + 30 * 24 * 60 * 60)?;

    let gossipsub_config = gossipsub::ConfigBuilder::default()
        .heartbeat_interval(Duration::from_secs(10))
        .max_transmit_size(2 * 1024 * 1024)
        .validation_mode(gossipsub::ValidationMode::Strict)
        .build()?;
    gossipsub_config.validate_messages();

    let gossipsub = gossipsub::Behaviour::new(
        gossipsub::MessageAuthenticity::Signed(key.clone()),
        gossipsub_config,
    )?;
    let topic = gossipsub::IdentTopic::new(TOPIC_NAME);
    let revocation_topic = gossipsub::IdentTopic::new(REVOCATION_TOPIC_NAME);
    let key_binding_topic = gossipsub::IdentTopic::new(KEY_BINDING_TOPIC_NAME);
    let encrypted_topic = gossipsub::IdentTopic::new(ENCRYPTED_TOPIC_NAME);
    let receipt_topic = gossipsub::IdentTopic::new(RECEIPT_TOPIC);
    let mdns_enabled = parse_mdns_enabled(read_peer_setting("OSIRIS_MDNS_ENABLED")?.as_deref())?;
    // Home-router port mapping (UPnP) makes a laptop node publicly reachable
    // with no manual firewall work. AutoNAT observes the result. DCUtR
    // upgrades relayed connections to direct ones. All default on; set to 0
    // to disable.
    let upnp_enabled = parse_flag(read_peer_setting("OSIRIS_UPNP_ENABLED")?.as_deref(), "OSIRIS_UPNP_ENABLED", true)?;
    let hole_punch_enabled = parse_flag(read_peer_setting("OSIRIS_HOLE_PUNCH_ENABLED")?.as_deref(), "OSIRIS_HOLE_PUNCH_ENABLED", true)?;
    // Opt-in: also serve as a relay for other nodes. With enough nodes opted
    // in, the mesh needs no dedicated relay server at all.
    let relay_server_enabled = parse_flag(read_peer_setting("OSIRIS_RELAY_SERVER")?.as_deref(), "OSIRIS_RELAY_SERVER", false)?;
    // Without a configured relay, borrow reservations from connected peers.
    let auto_relay_enabled = parse_flag(read_peer_setting("OSIRIS_AUTO_RELAY")?.as_deref(), "OSIRIS_AUTO_RELAY", true)?;
    let (relay_transport, relay_behaviour) = relay::client::new(peer_id);
    let mut behaviour = Behaviour {
        gossipsub,
        kademlia: kad::Behaviour::with_config(
            peer_id,
            MemoryStore::new(peer_id),
            kad::Config::new(StreamProtocol::new(DHT_PROTOCOL_NAME)),
        ),
        mdns: if mdns_enabled {
            Some(mdns::tokio::Behaviour::new(mdns::Config::default(), peer_id)?)
        } else { None }.into(),
        identify: identify::Behaviour::new(
            identify::Config::new(PROTOCOL_VERSION.to_owned(), key.public()),
        ),
        relay: relay_behaviour,
        relay_server: if relay_server_enabled {
            // Same conservative limits as the standalone relay binary.
            let mut config = relay::Config::default();
            config.max_reservations = 64;
            config.max_reservations_per_peer = 2;
            config.max_circuits = 8;
            config.max_circuits_per_peer = 2;
            config.reservation_duration = Duration::from_secs(30 * 60);
            config.max_circuit_duration = Duration::from_secs(60);
            config.max_circuit_bytes = 1_048_576;
            Some(relay::Behaviour::new(peer_id, config))
        } else { None }.into(),
        autonat: autonat::Behaviour::new(peer_id, autonat::Config::default()),
        dcutr: if hole_punch_enabled {
            Some(dcutr::Behaviour::new(peer_id))
        } else { None }.into(),
        upnp: if upnp_enabled {
            Some(upnp::tokio::Behaviour::default())
        } else { None }.into(),
        connection_limits: connection_limits::Behaviour::new(
            connection_limits::ConnectionLimits::default()
                .with_max_pending_incoming(Some(32))
                .with_max_pending_outgoing(Some(32))
                .with_max_established_incoming(Some(128))
                .with_max_established_outgoing(Some(128))
                .with_max_established_per_peer(Some(4))
                .with_max_established(Some(256)),
        ),
    };
    behaviour.kademlia.set_mode(Some(kad::Mode::Server));
    behaviour.gossipsub.subscribe(&topic)?;
    behaviour.gossipsub.subscribe(&revocation_topic)?;
    behaviour.gossipsub.subscribe(&key_binding_topic)?;
    behaviour.gossipsub.subscribe(&encrypted_topic)?;
    if delivery_receipts_enabled {
        behaviour.gossipsub.subscribe(&receipt_topic)?;
    }

    // Both direct and relayed circuits use the same authenticated transport and
    // Behaviour. A relay never bypasses recipient E2EE or local peer policy.
    let transport = OrTransport::new(
        relay_transport, tcp::tokio::Transport::new(tcp::Config::default()),
    )
        .upgrade(upgrade::Version::V1)
        .authenticate(noise::Config::new(&key)?)
        .multiplex(yamux::Config::default())
        .boxed();
    let mut swarm = Swarm::new(transport, behaviour, peer_id,
        SwarmConfig::with_tokio_executor()
            .with_idle_connection_timeout(Duration::from_secs(90)));

    // Public nodes behind a known public IP or DNS name should set
    // OSIRIS_EXTERNAL_ADDRS to comma-separated reachable TCP multiaddresses.
    // These are advertised to peers; firewall/NAT rules must forward the same port.
    let external_addresses = parse_external_addresses(env::var("OSIRIS_EXTERNAL_ADDRS").ok().as_deref())?;
    for address in &external_addresses {
        swarm.add_external_address(address.clone());
        emit!("external_address_advertised={address}");
    }

    let listen: Multiaddr = env::var("OSIRIS_LISTEN_ADDR")
        .unwrap_or_else(|_| "/ip4/0.0.0.0/tcp/0".to_owned())
        .parse()?;
    swarm.listen_on(listen)?;

    // OSIRIS_RELAY_ADDRESSES (comma-separated) enables relay failover;
    // OSIRIS_RELAY_ADDRESS remains as the single-relay shorthand.
    let mut relay_addresses = parse_relay_addresses(read_peer_setting("OSIRIS_RELAY_ADDRESSES")?.as_deref())?;
    if relay_addresses.is_empty() {
        relay_addresses = parse_relay_address(read_peer_setting("OSIRIS_RELAY_ADDRESS")?.as_deref())?
            .into_iter()
            .collect();
    }
    let mut relay_listeners: HashMap<PeerId, libp2p::core::transport::ListenerId> = HashMap::new();
    let mut active_relay_reservations: HashSet<PeerId> = HashSet::new();
    let bootstrap_peers = parse_bootstrap_peers(read_peer_setting("OSIRIS_BOOTSTRAP_PEERS")?.as_deref())?;
    let trusted_peers = parse_trusted_peers(read_peer_setting("OSIRIS_TRUSTED_PEERS")?.as_deref())?;
    let mut revoked_peer_set = parse_revoked_peers(read_peer_setting("OSIRIS_REVOKED_PEERS")?.as_deref())?
        .unwrap_or_default();
    let authority_key = read_peer_setting("OSIRIS_REVOCATION_AUTHORITY_PUBLIC_KEY")?;
    let registry_path = env::var_os("OSIRIS_REVOCATION_REGISTRY")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(".osiris-revocations.json"));
    let mut revocation_registry = if let Some(authority_key) = authority_key {
        let registry = RevocationRegistry::open(registry_path.clone(), authority_key)?;
        revoked_peer_set.extend(registry.revoked_peer_ids()?);
        emit!("signed_revocation_registry=loaded records={} path={}", registry.records().len(), registry_path.display());
        Some(registry)
    } else {
        emit!("signed_revocation_registry=disabled reason=authority_public_key_not_configured");
        None
    };
    let mut revoked_peers = if revoked_peer_set.is_empty() { None } else { Some(revoked_peer_set) };
    let replay_path = env::var_os("OSIRIS_REPLAY_CACHE")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(".osiris-replay-cache"));
    let mut replay_guard = ReplayGuard::open(replay_path.clone())?;
    let peer_key_store_path = env::var_os("OSIRIS_PEER_KEY_STORE")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(".osiris-peer-keys.sqlite3"));
    let mut peer_key_store = PeerKeyStore::open(peer_key_store_path.clone())?;
    emit!("peer_key_store=loaded bindings={} path={}", peer_key_store.len()?, peer_key_store_path.display());
    // Durable peer cache: restart-proof rejoin without any fixed bootstrap.
    let peer_cache_path = env::var_os("OSIRIS_PEER_CACHE")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(".osiris-peer-cache.sqlite3"));
    let peer_cache_max_age_secs = parse_interval_secs(
        env::var("OSIRIS_PEER_CACHE_MAX_AGE_SECS").ok().as_deref(), 7 * 24 * 60 * 60, 3600, 2_592_000);
    let peer_cache_dial_on_start = parse_interval_secs(
        env::var("OSIRIS_PEER_CACHE_DIAL_ON_START").ok().as_deref(), 16, 0, 64) as usize;
    let mut peer_cache = PeerCache::open(peer_cache_path.clone())?;
    emit!("replay_protection=durable cache_entries={} cache_path={}", replay_guard.seen.len(), replay_path.display());
    for (bootstrap_peer, _) in &bootstrap_peers {
        if !is_peer_allowed(bootstrap_peer, &trusted_peers, &revoked_peers) {
            return Err(format!(
                "configured bootstrap peer {bootstrap_peer} is not allowed by local trust/revocation policy"
            ).into());
        }
    }
    for (relay_peer, address) in &relay_addresses {
        if !is_peer_allowed(relay_peer, &trusted_peers, &revoked_peers) {
            return Err("configured relay is not allowed by local trust/revocation policy".into());
        }
        swarm.dial(address.clone())?;
        emit!("relay_configured peer={relay_peer} address={address}");
    }
    if relay_addresses.len() > 1 {
        emit!("relay_failover=enabled relays={}", relay_addresses.len());
    }
    // Parse and admit every configured endpoint before any outbound dial.
    for (bootstrap_peer, address) in &bootstrap_peers {
        swarm.behaviour_mut().kademlia.add_address(bootstrap_peer, address.clone());
        swarm.dial(address.clone())?;
    }
    if !bootstrap_peers.is_empty() { let _ = swarm.behaviour_mut().kademlia.bootstrap(); }
    // A revoked peer must never come back through the restart-rejoin path.
    if let Some(revoked) = &revoked_peers {
        for revoked_peer in revoked {
            let _ = peer_cache.remove(&revoked_peer.to_string());
        }
    }
    let startup_now = SystemTime::now().duration_since(UNIX_EPOCH)?.as_secs();
    let cached_peers = peer_cache.load_recent(256, peer_cache_max_age_secs, startup_now)?;
    let rejoin_plan = plan_rejoin_dials(
        &cached_peers, &peer_id, &bootstrap_peers, &relay_addresses,
        &trusted_peers, &revoked_peers, peer_cache_dial_on_start);
    emit!(
        "peer_cache=loaded cache_total={} rejoin_candidates={} rejoin_dials={} path={}",
        peer_cache.len()?, cached_peers.len(), rejoin_plan.len(), peer_cache.path().display());
    for (cached_peer, addresses) in rejoin_plan {
        match swarm.dial(DialOpts::peer_id(cached_peer).addresses(addresses).build()) {
            Ok(()) => emit!("peer_cache_rejoin_dial peer={cached_peer}"),
            Err(error) => emit!("peer_cache_rejoin_skipped peer={cached_peer} error={error}"),
        }
    }
    // Last-resort bootstrap when neither config nor cache knows any peer:
    // embedded seeds plus a small community seed file fetched over HTTPS.
    let (seed_tx, mut seed_rx) = tokio::sync::mpsc::channel::<Result<String, String>>(1);
    let mut seed_channel_open;
    for (seed_peer, seed_address) in parse_seed_list(&EMBEDDED_BOOTSTRAP_SEEDS.join("\n")) {
        if seed_peer == peer_id || !is_peer_allowed(&seed_peer, &trusted_peers, &revoked_peers) {
            continue;
        }
        swarm.behaviour_mut().kademlia.add_address(&seed_peer, seed_address.clone());
        match swarm.dial(seed_address) {
            Ok(()) => emit!("bootstrap_seed_dial peer={seed_peer} source=embedded"),
            Err(error) => emit!("bootstrap_seed_skipped peer={seed_peer} error={error}"),
        }
    }
    if let Some(seed_url) = resolve_seed_url(env::var("OSIRIS_BOOTSTRAP_SEED_URL").ok().as_deref()) {
        emit!("bootstrap_seed_fetch=scheduled url={seed_url}");
        tokio::task::spawn_blocking(move || {
            let result = fetch_seed_list(&seed_url, Duration::from_secs(15)).map_err(|error| error.to_string());
            let _ = seed_tx.blocking_send(result);
        });
        seed_channel_open = true;
    } else {
        emit!("bootstrap_seed_fetch=disabled");
        seed_channel_open = false;
    }
    if let Some(allowed) = &trusted_peers {
        emit!("peer_authentication=allowlist trusted_peers={}", allowed.len());
    } else {
        emit!("peer_authentication=open");
    }
    emit!("peer_revocation=local revoked_peers={}", revoked_peers.as_ref().map_or(0, |peers| peers.len()));
    let reconnect_interval_secs = parse_interval_secs(
        env::var("OSIRIS_BOOTSTRAP_RECONNECT_INTERVAL_SECS").ok().as_deref(),
        15,
        5,
        300,
    );
    // How often the node walks the DHT to discover new peers on the internet.
    let dht_walk_interval_secs = parse_interval_secs(
        env::var("OSIRIS_DHT_WALK_INTERVAL_SECS").ok().as_deref(),
        120,
        30,
        3600,
    );
    let revocation_sync_interval_secs = parse_interval_secs(
        env::var("OSIRIS_REVOCATION_SYNC_INTERVAL_SECS").ok().as_deref(),
        60,
        30,
        3600,
    );
    let mut pending_revocation_requests: HashMap<String, Instant> = HashMap::new();
    let mut revocation_sync_tick = tokio::time::interval(Duration::from_secs(revocation_sync_interval_secs));
    revocation_sync_tick.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
    let mut reconnect_tick = tokio::time::interval(Duration::from_secs(reconnect_interval_secs));
    reconnect_tick.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
    let mut dht_walk_tick = tokio::time::interval(Duration::from_secs(dht_walk_interval_secs));
    dht_walk_tick.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
    let health_interval_secs = env::var("OSIRIS_HEALTH_INTERVAL_SECS")
        .ok()
        .and_then(|value| value.parse::<u64>().ok())
        .unwrap_or(30)
        .clamp(5, 300);
    let mut health_tick = tokio::time::interval(Duration::from_secs(health_interval_secs));
    let mut key_binding_tick = tokio::time::interval(Duration::from_secs(24 * 60 * 60));
    key_binding_tick.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
    health_tick.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
    let started_at = Instant::now();
    let health_address: SocketAddr = env::var("OSIRIS_HEALTH_HTTP_ADDR")
        .unwrap_or_else(|_| "127.0.0.1:9090".to_owned())
        .parse()?;
    let initial_health = build_health_snapshot(&peer_id, started_at, &swarm, &bootstrap_peers, relay_readiness(&relay_addresses, &active_relay_reservations), relay_addresses.len(), active_relay_reservations.len());
    let (health_tx, health_rx) = watch::channel(initial_health);
    tokio::spawn(async move {
        if let Err(error) = serve_health_endpoint(health_address, health_rx).await {
            eprintln!("health_http_server_error={error}");
        }
    });
    emit!("OSIRIS EYE P2P node starting");
    emit!("peer_id={peer_id}");
    emit!("topic={TOPIC_NAME}");
    emit!("e2ee_enabled={e2ee_enabled} key_binding_topic={KEY_BINDING_TOPIC_NAME} encrypted_topic={ENCRYPTED_TOPIC_NAME}");
    emit!("transport_key_epoch={transport_key_epoch} transport_key_file_configured=true");
    let binding_bytes = serde_json::to_vec(&local_key_binding)?;
    let _ = swarm.behaviour_mut().gossipsub.publish(key_binding_topic.clone(), binding_bytes);
    emit!("mdns_enabled={mdns_enabled}");
    emit!("upnp_enabled={upnp_enabled} autonat=enabled hole_punch={}", if hole_punch_enabled { "enabled" } else { "disabled" });
    emit!("relay_server={} auto_relay={auto_relay_enabled}", if relay_server_enabled { "enabled" } else { "disabled" });
    emit!("connection_limits pending_incoming=32 pending_outgoing=32 established_total=256 established_per_peer=4");
    emit!("bootstrap_peers={}", bootstrap_peers.len());
    emit!("bootstrap_reconnect_interval_seconds={reconnect_interval_secs}");
    emit!("dht_protocol={DHT_PROTOCOL_NAME}");
    emit!("dht_walk_interval_seconds={dht_walk_interval_secs}");
    emit!("revocation_sync_interval_seconds={revocation_sync_interval_secs}");
    emit!("health_interval_seconds={health_interval_secs}");
    emit!("Live trading and broker order submission are not part of this node.");

    let stdin = BufReader::new(io::stdin());
    let mut lines = stdin.lines();
    // EOF is normal for daemonized nodes (stdin is often /dev/null).
    // Disable only the stdin branch; keep polling the swarm for incoming peers.
    let mut stdin_open = true;
    let mut pending_receipts = PendingReceipts::new();
    let mut receipt_bridge = VerifiedReceiptBridge::open_configured(
        delivery_receipts_enabled,
        optional_env("OSIRIS_VERIFIED_RECEIPT_SOCKET")?.as_deref(),
        optional_env("OSIRIS_VERIFIED_RECEIPT_SPOOL")?.as_deref(),
        optional_env("OSIRIS_VERIFIED_RECEIPT_LISTENER_PID")?.as_deref(),
    )?;
    let mut receipt_tick = tokio::time::interval(Duration::from_secs(5));
    receipt_tick.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
    emit!("delivery_receipts_enabled={delivery_receipts_enabled} receipt_topic={RECEIPT_TOPIC}");
    emit!("verified_receipt_bridge={}", if receipt_bridge.is_some() { "enabled" } else { "disabled" });

    loop {
        tokio::select! {
            _ = tokio::signal::ctrl_c() => {
                emit!("shutdown_signal=ctrl_c");
                break;
            }
            _ = receipt_tick.tick(), if delivery_receipts_enabled => {
                for (recipient, message_id) in pending_receipts.expire(Instant::now()) {
                    emit!("observation_delivery_unconfirmed recipient={recipient} message_id={message_id} reason=receipt_timeout");
                }
                if let Some(bridge) = receipt_bridge.as_mut() {
                    match bridge.pending_events(8) {
                        Ok(events) => {
                            for event in events {
                                let event_id = event.event_id.clone();
                                match bridge.transmit(&event, Duration::from_secs(1)) {
                                    Ok(disposition) => emit!("verified_receipt_ledger_confirmed event_id={event_id} disposition={disposition}"),
                                    Err(reason) => {
                                        emit!("verified_receipt_ledger_deferred event_id={event_id} reason={reason}");
                                        break;
                                    }
                                }
                            }
                        }
                        Err(reason) => emit!("verified_receipt_ledger_deferred reason={reason}"),
                    }
                }
            }
            _ = key_binding_tick.tick() => {
                let issued_at = SystemTime::now().duration_since(UNIX_EPOCH)?.as_secs();
                local_key_binding = create_key_binding(
                    &key, &transport_key.public_key, transport_key_epoch,
                    issued_at, issued_at + 30 * 24 * 60 * 60,
                )?;
                let binding_bytes = serde_json::to_vec(&local_key_binding)?;
                match swarm.behaviour_mut().gossipsub.publish(key_binding_topic.clone(), binding_bytes) {
                    Ok(_) => emit!("key_binding_refreshed epoch={transport_key_epoch}"),
                    Err(error) => emit!("key_binding_refresh_deferred error={error}"),
                }
            }
            _ = health_tick.tick() => {
                let snapshot = build_health_snapshot(&peer_id, started_at, &swarm, &bootstrap_peers, relay_readiness(&relay_addresses, &active_relay_reservations), relay_addresses.len(), active_relay_reservations.len());
                let connected_peers = snapshot.connected_peers;
                let connected_bootstrap_peers = snapshot.bootstrap_peers_connected;
                health_tx.send_replace(snapshot);
                emit!(
                    "node_health status=alive ready={} peer_id={} uptime_seconds={} connected_peers={} bootstrap_peers_connected={}/{}",
                    build_health_snapshot(&peer_id, started_at, &swarm, &bootstrap_peers, relay_readiness(&relay_addresses, &active_relay_reservations), relay_addresses.len(), active_relay_reservations.len()).ready,
                    peer_id,
                    started_at.elapsed().as_secs(),
                    connected_peers,
                    connected_bootstrap_peers,
                    bootstrap_peers.len()
                );
            }
            _ = revocation_sync_tick.tick(), if revocation_registry.is_some() => {
                let request_id = revocation_request_id(&peer_id, None);
                remember_revocation_request(&mut pending_revocation_requests, request_id.clone());
                let request = serde_json::to_vec(&RevocationSyncMessage::SyncRequestPageV2 { request_id: request_id.clone(), requester_peer_id: peer_id.to_string(), after_peer_id: None })?;
                match swarm.behaviour_mut().gossipsub.publish(revocation_topic.clone(), request) {
                    Ok(message_id) => emit!("revocation_anti_entropy_request_sent request_id={request_id} message_id={message_id}"),
                    Err(error) => emit!("revocation_anti_entropy_request_deferred error={error}"),
                }
            }
            _ = reconnect_tick.tick() => {
                for (relay_peer, address) in &relay_addresses {
                    if is_peer_allowed(relay_peer, &trusted_peers, &revoked_peers)
                        && !swarm.is_connected(relay_peer) {
                        match swarm.dial(address.clone()) {
                            Ok(()) => emit!("relay_reconnect_attempt peer={relay_peer}"),
                            Err(error) => emit!("relay_reconnect_error peer={relay_peer} error={error}"),
                        }
                    } else if swarm.is_connected(relay_peer) && !relay_listeners.contains_key(relay_peer)
                        && is_peer_allowed(relay_peer, &trusted_peers, &revoked_peers) {
                        match swarm.listen_on(address.clone().with(libp2p::multiaddr::Protocol::P2pCircuit)) {
                            Ok(listener) => { relay_listeners.insert(*relay_peer, listener); emit!("relay_reservation_requested=true relay_peer={relay_peer}"); }
                            Err(error) => emit!("relay_reservation_error peer={relay_peer} error={error}"),
                        }
                    }
                }
                // No configured relay: ask up to two already-connected,
                // policy-allowed bootstrap peers for a reservation instead.
                if auto_relay_enabled && relay_addresses.is_empty() {
                    let mut auto_relay_requests = 0usize;
                    for (bootstrap_peer, address) in &bootstrap_peers {
                        if auto_relay_requests >= 2 { break; }
                        if relay_listeners.contains_key(bootstrap_peer)
                            || active_relay_reservations.contains(bootstrap_peer) { continue; }
                        if !swarm.is_connected(bootstrap_peer)
                            || !is_peer_allowed(bootstrap_peer, &trusted_peers, &revoked_peers) { continue; }
                        match swarm.listen_on(address.clone().with(libp2p::multiaddr::Protocol::P2pCircuit)) {
                            Ok(listener) => {
                                relay_listeners.insert(*bootstrap_peer, listener);
                                auto_relay_requests += 1;
                                emit!("auto_relay_reservation_requested=true relay_peer={bootstrap_peer}");
                            }
                            Err(error) => emit!("auto_relay_reservation_error peer={bootstrap_peer} error={error}"),
                        }
                    }
                }
                for (bootstrap_peer, address) in &bootstrap_peers {
                    if is_peer_allowed(bootstrap_peer, &trusted_peers, &revoked_peers)
                        && !swarm.is_connected(bootstrap_peer) {
                        match swarm.dial(address.clone()) {
                            Ok(()) => emit!("bootstrap_reconnect_attempt peer={bootstrap_peer}"),
                            Err(error) => emit!("bootstrap_reconnect_error peer={bootstrap_peer} error={error}"),
                        }
                    }
                }
                if !bootstrap_peers.is_empty() {
                    match swarm.behaviour_mut().kademlia.bootstrap() {
                        Ok(query_id) => emit!("dht_bootstrap_refresh={query_id:?}"),
                        Err(error) => emit!("dht_bootstrap_refresh_skipped={error:?}"),
                    }
                }
            }
            _ = dht_walk_tick.tick() => {
                // Random-target walk: over time this visits the whole DHT and
                // surfaces peers the node has never seen before.
                let query = swarm.behaviour_mut().kademlia.get_closest_peers(PeerId::random());
                emit!("dht_walk_started query={query:?}");
            }
            seeds = seed_rx.recv(), if seed_channel_open => {
                seed_channel_open = false;
                match seeds {
                    Some(Ok(body)) => {
                        let fetched_at = SystemTime::now().duration_since(UNIX_EPOCH)?.as_secs();
                        let mut dialed = 0usize;
                        for (seed_peer, seed_address) in parse_seed_list(&body) {
                            if seed_peer == peer_id || swarm.is_connected(&seed_peer) { continue; }
                            if !is_peer_allowed(&seed_peer, &trusted_peers, &revoked_peers) {
                                emit!("bootstrap_seed_ignored peer={seed_peer} reason=not_in_trusted_peer_allowlist");
                                continue;
                            }
                            swarm.behaviour_mut().kademlia.add_address(&seed_peer, seed_address.clone());
                            let _ = peer_cache.remember(&seed_peer.to_string(), &[seed_address.to_string()], "seed", fetched_at);
                            match swarm.dial(seed_address) {
                                Ok(()) => { dialed += 1; emit!("bootstrap_seed_dial peer={seed_peer}"); }
                                Err(error) => emit!("bootstrap_seed_skipped peer={seed_peer} error={error}"),
                            }
                        }
                        if dialed > 0 { let _ = swarm.behaviour_mut().kademlia.bootstrap(); }
                        emit!("bootstrap_seed_fetch=applied dialed={dialed}");
                    }
                    Some(Err(error)) => emit!("bootstrap_seed_fetch=failed error={error}"),
                    None => {}
                }
            }
            event = swarm.select_next_some() => match event {
                SwarmEvent::NewListenAddr { listener_id, address } => {
                    let address = local_listen_address(address, peer_id);
                    emit!("listener_ready listener={listener_id:?} address={address}");
                    if let Some(relay_peer) = relay_listeners.iter()
                        .find(|(_, id)| **id == listener_id)
                        .map(|(peer, _)| *peer) {
                        emit!("relay_reserved_address={address} relay_peer={relay_peer}");
                    }
                }
                SwarmEvent::ListenerError { listener_id, error } => {
                    emit!("listener_error listener={listener_id:?} error={error}");
                }
                SwarmEvent::ListenerClosed { listener_id, addresses, reason } => {
                    emit!("listener_closed listener={listener_id:?} addresses={addresses:?} reason={reason:?}");
                    if let Some(relay_peer) = relay_listeners.iter()
                        .find(|(_, id)| **id == listener_id)
                        .map(|(peer, _)| *peer) {
                        relay_listeners.remove(&relay_peer);
                        active_relay_reservations.remove(&relay_peer);
                    }
                }
                SwarmEvent::Dialing { peer_id: target, connection_id } => {
                    emit!("outgoing_dial_started peer={target:?} connection={connection_id:?}");
                }
                SwarmEvent::OutgoingConnectionError { peer_id: target, connection_id, error } => {
                    emit!("outgoing_connection_error peer={target:?} connection={connection_id:?} error={error}");
                }
                SwarmEvent::IncomingConnectionError { connection_id, local_addr, send_back_addr, error, .. } => {
                    emit!("incoming_connection_error connection={connection_id:?} local={local_addr} remote={send_back_addr} error={error}");
                }
                SwarmEvent::ConnectionEstablished { peer_id, endpoint, connection_id, num_established, .. } => {
                    emit!("connection_established_detail peer={peer_id} endpoint={endpoint:?} connection={connection_id:?} total={num_established}");
                    if !is_peer_allowed(&peer_id, &trusted_peers, &revoked_peers) {
                        emit!("peer_rejected={peer_id} reason=not_in_trusted_peer_allowlist");
                        let _ = swarm.disconnect_peer_id(peer_id);
                        continue;
                    }
                    emit!("peer_connected={peer_id}");
                    emit!("peer_connection_path peer={peer_id} relayed={}", endpoint.is_relayed());
                    let connected_at = SystemTime::now().duration_since(UNIX_EPOCH)?.as_secs();
                    if let Err(error) = peer_cache.remember(
                        &peer_id.to_string(),
                        &[endpoint.get_remote_address().to_string()],
                        "connection",
                        connected_at,
                    ) {
                        emit!("peer_cache_write_deferred peer={peer_id} error={error}");
                    }
                    if let Some((relay_peer, address)) = relay_addresses.iter()
                        .find(|(relay_peer, _)| *relay_peer == peer_id) {
                        if !relay_listeners.contains_key(relay_peer) {
                            match swarm.listen_on(address.clone().with(libp2p::multiaddr::Protocol::P2pCircuit)) {
                                Ok(listener) => { relay_listeners.insert(peer_id, listener); emit!("relay_reservation_requested=true relay_peer={peer_id}"); }
                                Err(error) => emit!("relay_reservation_error peer={peer_id} error={error}"),
                            }
                        }
                    }
                }
                SwarmEvent::ConnectionClosed { peer_id, cause, num_established, .. } => {
                    emit!("peer_disconnected={peer_id} cause={cause:?} remaining={num_established}");
                    if num_established == 0 {
                        active_relay_reservations.remove(&peer_id);
                        if let Some(listener) = relay_listeners.remove(&peer_id) {
                            let _ = swarm.remove_listener(listener);
                        }
                    }
                }
                SwarmEvent::Behaviour(BehaviourEvent::Mdns(mdns::Event::Discovered(peers))) => {
                    for (peer, address) in peers {
                        if !is_peer_allowed(&peer, &trusted_peers, &revoked_peers) {
                            emit!("mdns_peer_ignored={peer} reason=not_in_trusted_peer_allowlist");
                            continue;
                        }
                        let discovered_at = SystemTime::now().duration_since(UNIX_EPOCH)?.as_secs();
                        let _ = peer_cache.remember(&peer.to_string(), &[address.to_string()], "mdns", discovered_at);
                        swarm.behaviour_mut().kademlia.add_address(&peer, address.clone());
                        if !swarm.is_connected(&peer) {
                            match swarm.dial(address) {
                                Ok(()) => emit!("mdns_peer_dial_started={peer}"),
                                Err(error) => emit!("mdns_peer_dial_error peer={peer} error={error}"),
                            }
                        }
                    }
                }
                SwarmEvent::Behaviour(BehaviourEvent::Mdns(mdns::Event::Expired(peers))) => {
                    for (peer, address) in peers {
                        swarm.behaviour_mut().kademlia.remove_address(&peer, &address);
                        emit!("mdns_peer_expired={peer}");
                    }
                }
                SwarmEvent::Behaviour(BehaviourEvent::Gossipsub(gossipsub::Event::Subscribed { peer_id: subscribed_peer_id, .. })) => {
                    if !is_peer_allowed(&subscribed_peer_id, &trusted_peers, &revoked_peers) {
                        emit!("peer_rejected={subscribed_peer_id} reason=not_in_trusted_peer_allowlist");
                        let _ = swarm.disconnect_peer_id(subscribed_peer_id);
                        continue;
                    }
                    emit!("gossipsub_subscribed peer={subscribed_peer_id}");
                    let binding_bytes = serde_json::to_vec(&local_key_binding)?;
                    let _ = swarm.behaviour_mut().gossipsub.publish(key_binding_topic.clone(), binding_bytes);
                    if revocation_registry.is_some() {
                        let request_id = revocation_request_id(&peer_id, None);
                        remember_revocation_request(&mut pending_revocation_requests, request_id.clone());
                        let request = serde_json::to_vec(&RevocationSyncMessage::SyncRequestPageV2 { request_id, requester_peer_id: peer_id.to_string(), after_peer_id: None })?;
                        let _ = swarm.behaviour_mut().gossipsub.publish(revocation_topic.clone(), request);
                    }
                }
                SwarmEvent::Behaviour(BehaviourEvent::Gossipsub(gossipsub::Event::Message {
                    propagation_source, message, ..
                })) => {
                    let authenticated_source = message.source.unwrap_or(propagation_source);

                    if message.topic.as_str() == RECEIPT_TOPIC {
                        if !delivery_receipts_enabled {
                            continue;
                        }
                        // Never infer the receipt signer from the forwarding peer.
                        let source_allowed = message.source.is_some()
                            && is_peer_allowed(&authenticated_source, &trusted_peers, &revoked_peers);
                        let propagation_allowed = is_peer_allowed(&propagation_source, &trusted_peers, &revoked_peers);
                        if !source_allowed || !propagation_allowed {
                            emit!("delivery_receipt_rejected peer={authenticated_source} reason=publisher_not_allowed");
                            continue;
                        }
                        if message.data.len() > MAX_RECEIPT_BYTES {
                            emit!("delivery_receipt_rejected peer={authenticated_source} reason=receipt_too_large");
                            continue;
                        }
                        let receipt: DeliveryReceipt = match serde_json::from_slice(&message.data) {
                            Ok(receipt) => receipt,
                            Err(_) => {
                                emit!("delivery_receipt_rejected peer={authenticated_source} reason=invalid_receipt_json");
                                continue;
                            }
                        };
                        let Some(binding_record) = peer_key_store.get(&authenticated_source.to_string())? else {
                            emit!("delivery_receipt_rejected peer={authenticated_source} reason=recipient_key_binding_missing");
                            continue;
                        };
                        let receipt_now = SystemTime::now().duration_since(UNIX_EPOCH)?.as_secs();
                        let binding = match verify_key_binding(&binding_record, receipt_now, binding_record.key_epoch) {
                            Ok(binding) if binding.peer_id == authenticated_source => binding,
                            _ => {
                                emit!("delivery_receipt_rejected peer={authenticated_source} reason=recipient_key_binding_invalid");
                                continue;
                            }
                        };
                        let policy = AdmissionPolicy {
                            authenticated_source_allowed: source_allowed,
                            propagation_source_allowed: propagation_allowed,
                        };
                        match pending_receipts.verify(
                            &receipt, &authenticated_source, &peer_id,
                            &binding.identity_public_key, receipt_now, Instant::now(), policy,
                        ) {
                            Ok(event) => {
                                if let Some(bridge) = receipt_bridge.as_mut() {
                                    // Spool the verified event before dropping the pending attempt.
                                    match bridge.enqueue(&event) {
                                        Ok(()) => {
                                            match pending_receipts.commit_verified(&event, Instant::now()) {
                                                Ok(()) => emit!(
                                                    "observation_delivery_acknowledged recipient={} message_id={} envelope_sha256={} admission=validated_decrypted_replay_recorded",
                                                    receipt.recipient_peer_id, receipt.message_id, receipt.envelope_sha256,
                                                ),
                                                Err(reason) => emit!("verified_receipt_pending_commit_failed event_id={} reason={reason}", event.event_id),
                                            }
                                            match bridge.transmit(&event, Duration::from_secs(1)) {
                                                Ok(disposition) => emit!("verified_receipt_ledger_confirmed event_id={} disposition={disposition}", event.event_id),
                                                Err(reason) => emit!("verified_receipt_ledger_deferred event_id={} reason={reason}", event.event_id),
                                            }
                                        }
                                        Err(reason) => emit!("delivery_receipt_rejected peer={authenticated_source} reason=spool_failed detail={reason}"),
                                    }
                                } else {
                                    match pending_receipts.commit_verified(&event, Instant::now()) {
                                        Ok(()) => emit!(
                                            "observation_delivery_acknowledged recipient={} message_id={} envelope_sha256={} admission=validated_decrypted_replay_recorded",
                                            receipt.recipient_peer_id, receipt.message_id, receipt.envelope_sha256,
                                        ),
                                        Err(reason) => emit!("delivery_receipt_rejected peer={authenticated_source} reason={reason}"),
                                    }
                                }
                            }
                            Err(reason) => emit!("delivery_receipt_rejected peer={authenticated_source} reason={reason}"),
                        }
                        continue;
                    }

                    if message.topic.as_str() == KEY_BINDING_TOPIC_NAME {
                        if !is_peer_allowed(&authenticated_source, &trusted_peers, &revoked_peers) {
                            emit!("key_binding_rejected peer={authenticated_source} reason=peer_not_allowed");
                            continue;
                        }
                        match serde_json::from_slice::<KeyBindingRecord>(&message.data) {
                            Ok(record) => {
                                let peer_key = authenticated_source.to_string();
                                let existing = peer_key_store.get(&peer_key)?;
                                let minimum_epoch = existing.as_ref()
                                    .map(|binding| binding.key_epoch).unwrap_or(1);
                                match verify_key_binding(&record, SystemTime::now().duration_since(UNIX_EPOCH)?.as_secs(), minimum_epoch) {
                                    Ok(verified) if verified.peer_id == authenticated_source => {
                                        match peer_key_store.upsert_verified(&record) {
                                            Ok(()) => emit!("key_binding_accepted peer={authenticated_source} epoch={} durable=true", verified.key_epoch),
                                            Err(error) => emit!("key_binding_rejected peer={authenticated_source} reason=durable_store_rejected error={error}"),
                                        }
                                    }
                                    Ok(_) => emit!("key_binding_rejected peer={authenticated_source} reason=publisher_peer_id_mismatch"),
                                    Err(error) => emit!("key_binding_rejected peer={authenticated_source} reason={error}"),
                                }
                            }
                            Err(error) => emit!("key_binding_rejected peer={authenticated_source} reason=invalid_record error={error}"),
                        }
                        continue;
                    }
                    if message.topic.as_str() == ENCRYPTED_TOPIC_NAME {
                        if !e2ee_enabled {
                            emit!("encrypted_message_ignored reason=e2ee_disabled");
                            continue;
                        }
                        let parsed_envelope = match run_if_peer_allowed(
                            &authenticated_source, &trusted_peers, &revoked_peers,
                            || serde_json::from_slice::<EncryptedEnvelope>(&message.data),
                        ) {
                            Ok(parsed) => parsed,
                            Err(()) => {
                                emit!("encrypted_message_rejected peer={authenticated_source} reason=peer_not_allowed");
                                continue;
                            }
                        };
                        match parsed_envelope {
                            Ok(envelope) if envelope.sender_peer_id == authenticated_source.to_string()
                                && envelope.recipient_peer_id == peer_id.to_string() => {
                                let Some(binding_record) = peer_key_store.get(&authenticated_source.to_string())? else {
                                    emit!("encrypted_message_rejected peer={authenticated_source} reason=sender_key_binding_missing");
                                    continue;
                                };
                                let sender_binding = match verify_key_binding(
                                    &binding_record,
                                    SystemTime::now().duration_since(UNIX_EPOCH)?.as_secs(),
                                    envelope.sender_key_epoch,
                                ) {
                                    Ok(binding) if binding.key_epoch == envelope.sender_key_epoch => binding,
                                    _ => {
                                        emit!("encrypted_message_rejected peer={authenticated_source} reason=sender_key_binding_invalid");
                                        continue;
                                    }
                                };
                                let plaintext = match decrypt_for_recipient(
                                    &envelope, &sender_binding, &peer_id, transport_key_epoch,
                                    transport_key.private_key(),
                                    SystemTime::now().duration_since(UNIX_EPOCH)?.as_secs(),
                                ) {
                                    Ok(plaintext) => plaintext,
                                    Err(error) => {
                                        emit!("encrypted_message_rejected peer={authenticated_source} reason={error}");
                                        continue;
                                    }
                                };
                                match admit_decrypted_observation(
                                    &plaintext, &format!("{}:{}", envelope.sender_peer_id, envelope.message_id),
                                    max_observation_age, &mut replay_guard,
                                ) {
                                    Ok(observation) => {
                                        emit!(
                                            "encrypted_observation_received peer={} instrument={} provider={} observed_at={} payload_sha256={} payload_bytes={}",
                                            authenticated_source, observation.instrument, observation.provider,
                                            observation.observed_at, observation.payload_sha256, observation.payload.len()
                                        );
                                        if delivery_receipts_enabled {
                                            // Admit and sync the replay journal before attesting reception.
                                            // Only ciphertext metadata is put on the receipt topic.
                                            let envelope_sha256 = format!("{:x}", Sha256::digest(&message.data));
                                            let accepted_at = SystemTime::now().duration_since(UNIX_EPOCH)?.as_secs();
                                            match create_receipt(
                                                &key, &authenticated_source, &envelope.message_id,
                                                &envelope_sha256, accepted_at,
                                            ) {
                                                Ok(receipt) => {
                                                    let bytes = serde_json::to_vec(&receipt)?;
                                                    match swarm.behaviour_mut().gossipsub.publish(receipt_topic.clone(), bytes) {
                                                        Ok(_) => emit!(
                                                            "delivery_receipt_published sender={authenticated_source} recipient={peer_id} message_id={} envelope_sha256={envelope_sha256}",
                                                            envelope.message_id,
                                                        ),
                                                        Err(error) => emit!("delivery_receipt_deferred sender={authenticated_source} reason=publish_failed error={error}"),
                                                    }
                                                }
                                                Err(reason) => emit!("delivery_receipt_deferred sender={authenticated_source} reason={reason}"),
                                            }
                                        }
                                    }
                                    Err(EncryptedObservationError::Invalid(reason)) => emit!("encrypted_message_rejected peer={authenticated_source} reason=invalid_plaintext_observation detail={reason}"),
                                    Err(EncryptedObservationError::Replay) => emit!("encrypted_message_rejected peer={authenticated_source} reason=replay_detected"),
                                    Err(EncryptedObservationError::Storage(error)) => emit!("encrypted_message_rejected peer={authenticated_source} reason=replay_store_error error={error}"),
                                }
                            }
                            Ok(_) => emit!("encrypted_message_discarded reason=not_for_this_recipient_or_sender_mismatch"),
                            Err(error) => emit!("encrypted_message_rejected peer={authenticated_source} reason=invalid_envelope error={error}"),
                        }
                        continue;
                    }

                    if message.topic.as_str() == REVOCATION_TOPIC_NAME {
                        let authenticated_source = message.source.unwrap_or(propagation_source);
                        if !is_peer_allowed(&authenticated_source, &trusted_peers, &revoked_peers) {
                            emit!("revocation_sync_message_rejected peer={propagation_source} reason=peer_not_allowed");
                            continue;
                        }
                        let Some(registry) = revocation_registry.as_mut() else {
                            emit!("revocation_sync_disabled reason=authority_key_not_configured");
                            continue;
                        };
                        pending_revocation_requests.retain(|_, created| created.elapsed() <= REVOCATION_REQUEST_TTL);
                        match serde_json::from_slice::<RevocationSyncMessage>(&message.data) {
                            Ok(RevocationSyncMessage::SyncRequest) => {
                                let snapshot = RevocationSyncMessage::Snapshot(registry.records());
                                let bytes = serde_json::to_vec(&snapshot)?;
                                let _ = swarm.behaviour_mut().gossipsub.publish(revocation_topic.clone(), bytes);
                                emit!("revocation_sync_snapshot_sent requester={propagation_source} records={}", registry.records().len());
                            }
                            Ok(RevocationSyncMessage::SyncRequestPage { after_peer_id }) => {
                                let (records, next_cursor) = registry.page_after(after_peer_id.as_deref(), REVOCATION_SYNC_PAGE_SIZE);
                                let count = records.len();
                                let page = RevocationSyncMessage::SnapshotPage { records, next_cursor: next_cursor.clone() };
                                let bytes = serde_json::to_vec(&page)?;
                                let _ = swarm.behaviour_mut().gossipsub.publish(revocation_topic.clone(), bytes);
                                emit!("revocation_sync_page_sent requester={propagation_source} records={count} next_page={}", next_cursor.is_some());
                            }
                            Ok(RevocationSyncMessage::SyncRequestPageV2 { request_id, requester_peer_id, after_peer_id }) => {
                                if requester_peer_id != authenticated_source.to_string() || request_id.len() != 64 || !request_id.bytes().all(|byte| byte.is_ascii_hexdigit()) {
                                    emit!("revocation_sync_request_rejected source={propagation_source} reason=invalid_request_binding");
                                    continue;
                                }
                                let (records, next_cursor) = registry.page_after(after_peer_id.as_deref(), REVOCATION_SYNC_PAGE_SIZE);
                                let count = records.len();
                                let page = RevocationSyncMessage::SnapshotPageV2 {
                                    request_id: request_id.clone(), requester_peer_id,
                                    responder_peer_id: peer_id.to_string(), records, next_cursor: next_cursor.clone(),
                                };
                                let bytes = serde_json::to_vec(&page)?;
                                let _ = swarm.behaviour_mut().gossipsub.publish(revocation_topic.clone(), bytes);
                                emit!("revocation_sync_page_v2_sent request_id={request_id} requester={propagation_source} records={count} next_page={}", next_cursor.is_some());
                            }
                            Ok(RevocationSyncMessage::Record(record)) => {
                                match registry.apply(record.clone(), true) {
                                    Ok(true) => {
                                        let peer = record.peer_id.parse::<PeerId>()?;
                                        revoked_peers.get_or_insert_with(HashSet::new).insert(peer);
                                        let _ = peer_cache.remove(&record.peer_id);
                                        let bytes = serde_json::to_vec(&RevocationSyncMessage::Record(record))?;
                                        let _ = swarm.behaviour_mut().gossipsub.publish(revocation_topic.clone(), bytes);
                                        emit!("signed_peer_revocation_applied peer={peer} source={propagation_source}");
                                        let _ = swarm.disconnect_peer_id(peer);
                                    }
                                    Ok(false) => emit!("revocation_record_ignored reason=replay_or_stale"),
                                    Err(error) => emit!("revocation_record_rejected source={propagation_source} error={error}"),
                                }
                            }
                            Ok(RevocationSyncMessage::Snapshot(records)) => {
                                let mut applied = 0usize;
                                for record in records {
                                    match registry.apply(record.clone(), true) {
                                        Ok(true) => {
                                            let peer = record.peer_id.parse::<PeerId>()?;
                                            revoked_peers.get_or_insert_with(HashSet::new).insert(peer);
                                            let _ = peer_cache.remove(&record.peer_id);
                                            let bytes = serde_json::to_vec(&RevocationSyncMessage::Record(record))?;
                                            let _ = swarm.behaviour_mut().gossipsub.publish(revocation_topic.clone(), bytes);
                                            let _ = swarm.disconnect_peer_id(peer);
                                            applied += 1;
                                        }
                                        Ok(false) => {}
                                        Err(error) => emit!("revocation_snapshot_record_rejected source={propagation_source} error={error}"),
                                    }
                                }
                                emit!("revocation_sync_snapshot_applied source={propagation_source} applied={applied}");
                            }
                            Ok(RevocationSyncMessage::SnapshotPageV2 { request_id, requester_peer_id, responder_peer_id, records, next_cursor }) => {
                                if requester_peer_id != peer_id.to_string()
                                    || responder_peer_id != authenticated_source.to_string()
                                    || !pending_revocation_requests.contains_key(&request_id) {
                                    emit!("revocation_sync_response_rejected source={propagation_source} reason=unmatched_or_wrong_peer_binding");
                                    continue;
                                }
                                let mut applied = 0usize;
                                for record in records {
                                    match registry.apply(record.clone(), true) {
                                        Ok(true) => {
                                            let revoked_peer = record.peer_id.parse::<PeerId>()?;
                                            revoked_peers.get_or_insert_with(HashSet::new).insert(revoked_peer);
                                            let _ = peer_cache.remove(&record.peer_id);
                                            let bytes = serde_json::to_vec(&RevocationSyncMessage::Record(record))?;
                                            let _ = swarm.behaviour_mut().gossipsub.publish(revocation_topic.clone(), bytes);
                                            let _ = swarm.disconnect_peer_id(revoked_peer);
                                            applied += 1;
                                        }
                                        Ok(false) => {}
                                        Err(error) => emit!("revocation_snapshot_record_rejected source={propagation_source} error={error}"),
                                    }
                                }
                                emit!("revocation_sync_page_v2_applied request_id={request_id} source={propagation_source} applied={applied} has_more={}", next_cursor.is_some());
                                if let Some(cursor) = next_cursor {
                                    let next_id = revocation_request_id(&peer_id, Some(&cursor));
                                    remember_revocation_request(&mut pending_revocation_requests, next_id.clone());
                                    let request = RevocationSyncMessage::SyncRequestPageV2 {
                                        request_id: next_id, requester_peer_id: peer_id.to_string(), after_peer_id: Some(cursor),
                                    };
                                    let bytes = serde_json::to_vec(&request)?;
                                    let _ = swarm.behaviour_mut().gossipsub.publish(revocation_topic.clone(), bytes);
                                }
                            }
                            Ok(RevocationSyncMessage::SnapshotPage { .. }) => {
                                emit!("revocation_sync_response_rejected source={propagation_source} reason=legacy_unbound_page");
                            }
                            Err(error) => emit!("revocation_sync_message_rejected peer={propagation_source} error={error}"),
                        }
                        continue;
                    }
                    if !is_peer_allowed(&propagation_source, &trusted_peers, &revoked_peers)
                        || !is_peer_allowed(&authenticated_source, &trusted_peers, &revoked_peers) {
                        emit!("message_rejected peer={authenticated_source} reason=not_in_trusted_peer_allowlist");
                        continue;
                    }
                    if e2ee_enabled {
                        emit!("plaintext_message_rejected peer={propagation_source} reason=e2ee_required");
                        continue;
                    }
                    match serde_json::from_slice::<ObservationEnvelope>(&message.data) {
                        Ok(envelope) if envelope.validate_for_network(max_observation_age).is_ok() => {
                            match replay_guard.record_if_new(&envelope) {
                                Ok(true) => emit!(
                                    "observation_received peer={} instrument={} provider={} observed_at={} payload_sha256={} payload_bytes={}",
                                    propagation_source, envelope.instrument, envelope.provider,
                                    envelope.observed_at, envelope.payload_sha256, envelope.payload.len()
                                ),
                                Ok(false) => emit!("message_rejected peer={propagation_source} reason=replay_detected"),
                                Err(error) => {
                                    eprintln!("message_rejected peer={propagation_source} reason=replay_store_error error={error}");
                                    continue;
                                }
                            }
                        }
                        _ => eprintln!("rejected_message peer={propagation_source} reason=invalid_envelope"),
                    }
                }
                SwarmEvent::Behaviour(BehaviourEvent::Relay(relay::client::Event::ReservationReqAccepted { relay_peer_id, renewal, .. })) => {
                    if (relay_addresses.iter().any(|(configured, _)| *configured == relay_peer_id)
                        || relay_listeners.contains_key(&relay_peer_id))
                        && is_peer_allowed(&relay_peer_id, &trusted_peers, &revoked_peers) {
                        active_relay_reservations.insert(relay_peer_id);
                        emit!("relay_reservation_accepted=true relay_peer={relay_peer_id} renewal={renewal} active_reservations={}", active_relay_reservations.len());
                    }
                }
                SwarmEvent::Behaviour(BehaviourEvent::Relay(event)) => {
                    emit!("relay_client_event={event:?}");
                }
                SwarmEvent::Behaviour(BehaviourEvent::RelayServer(event)) => {
                    emit!("relay_server_event={event:?}");
                }
                SwarmEvent::Behaviour(BehaviourEvent::Autonat(autonat::Event::StatusChanged { old, new })) => {
                    emit!("autonat_status old={old:?} new={new:?}");
                }
                SwarmEvent::Behaviour(BehaviourEvent::Autonat(event)) => {
                    emit!("autonat_event={event:?}");
                }
                SwarmEvent::Behaviour(BehaviourEvent::Dcutr(dcutr::Event { remote_peer_id, result })) => {
                    match result {
                        Ok(_) => emit!("hole_punch_succeeded peer={remote_peer_id}"),
                        Err(error) => emit!("hole_punch_failed peer={remote_peer_id} error={error}"),
                    }
                }
                SwarmEvent::Behaviour(BehaviourEvent::Upnp(upnp::Event::NewExternalAddr(address))) => {
                    swarm.add_external_address(address.clone());
                    emit!("upnp_external_address={address}");
                }
                SwarmEvent::Behaviour(BehaviourEvent::Upnp(upnp::Event::ExpiredExternalAddr(address))) => {
                    swarm.remove_external_address(&address);
                    emit!("upnp_external_address_expired={address}");
                }
                SwarmEvent::Behaviour(BehaviourEvent::Upnp(event)) => {
                    emit!("upnp_event={event:?}");
                }
                SwarmEvent::Behaviour(BehaviourEvent::Kademlia(kad::Event::OutboundQueryProgressed {
                    result: kad::QueryResult::GetClosestPeers(Ok(found)),
                    ..
                })) => {
                    // Random-walk result: feed every discovered peer back into the
                    // DHT and dial a bounded number of new, policy-allowed peers so
                    // the mesh grows across the internet without operator action.
                    let mut dialed = 0usize;
                    for peer_info in &found.peers {
                        let discovered = peer_info.peer_id;
                        if discovered == peer_id || swarm.is_connected(&discovered) { continue; }
                        if !is_peer_allowed(&discovered, &trusted_peers, &revoked_peers) {
                            emit!("dht_peer_ignored peer={discovered} reason=not_in_trusted_peer_allowlist");
                            continue;
                        }
                        if peer_info.addrs.is_empty() { continue; }
                        for address in peer_info.addrs.iter().take(4) {
                            swarm.behaviour_mut().kademlia.add_address(&discovered, address.clone());
                        }
                        let discovered_at = SystemTime::now().duration_since(UNIX_EPOCH)?.as_secs();
                        let cache_addresses: Vec<String> = peer_info.addrs.iter().take(4).map(ToString::to_string).collect();
                        let _ = peer_cache.remember(&discovered.to_string(), &cache_addresses, "dht", discovered_at);
                        if dialed >= 4 { continue; }
                        let dial = DialOpts::peer_id(discovered)
                            .addresses(peer_info.addrs.clone())
                            .build();
                        match swarm.dial(dial) {
                            Ok(()) => { dialed += 1; emit!("dht_peer_dial_started peer={discovered}"); }
                            Err(error) => emit!("dht_peer_dial_skipped peer={discovered} error={error}"),
                        }
                    }
                    emit!("dht_walk_finished peers_found={} dialed={dialed}", found.peers.len());
                }
                SwarmEvent::Behaviour(BehaviourEvent::Kademlia(event)) => {
                    println!("dht_event={event:?}");
                }
                SwarmEvent::Behaviour(BehaviourEvent::Identify(identify::Event::Received { peer_id: identified_peer, info, .. })) => {
                    // Feed observed listen addresses into the DHT so the routing
                    // table keeps growing from real peers, not just bootstrap config.
                    if identified_peer == peer_id { continue; }
                    if !is_peer_allowed(&identified_peer, &trusted_peers, &revoked_peers) {
                        emit!("identify_ignored peer={identified_peer} reason=not_in_trusted_peer_allowlist");
                        continue;
                    }
                    let mut added = 0usize;
                    for address in info.listen_addrs.iter().take(8) {
                        swarm.behaviour_mut().kademlia.add_address(&identified_peer, address.clone());
                        added += 1;
                    }
                    let identified_at = SystemTime::now().duration_since(UNIX_EPOCH)?.as_secs();
                    let cache_addresses: Vec<String> = info.listen_addrs.iter().take(8).map(ToString::to_string).collect();
                    let _ = peer_cache.remember(&identified_peer.to_string(), &cache_addresses, "identify", identified_at);
                    emit!("identify_received peer={identified_peer} agent={} dht_addrs_added={added}", info.agent_version);
                }
                SwarmEvent::Behaviour(BehaviourEvent::Identify(event)) => {
                    println!("identify_event={event:?}");
                }
                _ => {}
            },
            line = lines.next_line(), if stdin_open => match line? {
                Some(line) if !line.trim().is_empty() => {
                    let envelope = match parse_observation(line.as_bytes(), max_observation_age) {
                        Ok(envelope) => envelope,
                        Err(reason) => {
                            eprintln!("publish_rejected reason={reason}");
                            continue;
                        }
                    };
                    if e2ee_enabled {
                        let recipients_raw = if let Some(path) = env::var_os("OSIRIS_E2EE_RECIPIENTS_FILE") {
                            match fs::read_to_string(PathBuf::from(path)) {
                                Ok(value) => value,
                                Err(error) => {
                                    emit!("publish_rejected reason=recipient_file_read_error error={error}");
                                    continue;
                                }
                            }
                        } else {
                            env::var("OSIRIS_E2EE_RECIPIENTS").unwrap_or_default()
                        };
                        if recipients_raw.len() > 16_384 {
                            emit!("publish_rejected reason=recipient_configuration_too_large");
                            continue;
                        }
                        let mut unique_recipients = HashSet::new();
                        let recipients: Vec<String> = recipients_raw
                            .split(',')
                            .map(str::trim)
                            .filter(|value| !value.is_empty())
                            .filter(|value| unique_recipients.insert((*value).to_owned()))
                            .map(str::to_owned)
                            .collect();
                        if recipients.is_empty() || recipients.len() > 128 {
                            emit!("publish_rejected reason=e2ee_recipient_count_out_of_range");
                            continue;
                        }
                        let plaintext = serde_json::to_vec(&envelope)?;
                        for recipient in recipients {
                            let recipient_peer: PeerId = match recipient.parse() {
                                Ok(peer) => peer,
                                Err(_) => {
                                    emit!("publish_rejected reason=invalid_e2ee_recipient");
                                    continue;
                                }
                            };
                            if !is_peer_allowed(&recipient_peer, &trusted_peers, &revoked_peers) {
                                emit!("publish_rejected recipient={recipient_peer} reason=recipient_not_allowed");
                                continue;
                            }
                            if delivery_receipts_enabled && !pending_receipts.has_capacity() {
                                emit!("publish_deferred recipient={recipient_peer} reason=receipt_tracking_capacity");
                                continue;
                            }
                            let Some(binding_record) = peer_key_store.get(&recipient_peer.to_string())? else {
                                emit!("publish_deferred recipient={recipient_peer} reason=recipient_key_binding_missing");
                                continue;
                            };
                            let recipient_binding = match verify_key_binding(
                                &binding_record,
                                SystemTime::now().duration_since(UNIX_EPOCH)?.as_secs(),
                                binding_record.key_epoch,
                            ) {
                                Ok(binding) => binding,
                                Err(error) => {
                                    emit!("publish_rejected recipient={recipient_peer} reason=recipient_key_binding_invalid error={error}");
                                    continue;
                                }
                            };
                            let created_at = SystemTime::now().duration_since(UNIX_EPOCH)?.as_secs();
                            let encrypted = match encrypt_for_recipient(
                                &key,
                                transport_key_epoch,
                                &recipient_peer.to_string(),
                                &recipient_binding.transport_public_key,
                                recipient_binding.key_epoch,
                                &plaintext,
                                created_at,
                                created_at + 3600,
                            ) {
                                Ok(encrypted) => encrypted,
                                Err(error) => {
                                    emit!("publish_rejected recipient={recipient_peer} reason=encryption_error error={error}");
                                    continue;
                                }
                            };
                            let message_id = encrypted.message_id.clone();
                            let bytes = serde_json::to_vec(&encrypted)?;
                            let envelope_sha256 = format!("{:x}", Sha256::digest(&bytes));
                            match swarm.behaviour_mut().gossipsub.publish(encrypted_topic.clone(), bytes) {
                                Ok(_) => {
                                    if delivery_receipts_enabled {
                                        match pending_receipts.track(
                                            recipient_peer, message_id.clone(), envelope_sha256.clone(), Instant::now(),
                                        ) {
                                            Ok(()) => emit!("encrypted_observation_published recipient={recipient_peer} message_id={message_id} envelope_sha256={envelope_sha256} receipts_tracked=true"),
                                            Err(reason) => emit!("observation_delivery_unconfirmed recipient={recipient_peer} message_id={message_id} reason={reason}"),
                                        }
                                    } else {
                                        emit!("encrypted_observation_published recipient={recipient_peer} message_id={message_id}");
                                    }
                                }
                                Err(error) => emit!("encrypted_observation_deferred recipient={recipient_peer} error={error}"),
                            }
                        }
                    } else {
                        match swarm.behaviour_mut().gossipsub.publish(topic.clone(), line.into_bytes()) {
                            Ok(_) => emit!("observation_published"),
                            Err(error) => emit!("observation_publish_deferred error={error}"),
                        }
                    }
                }
                Some(_) => {}
                None => {
                    emit!("stdin_eof=ignored_daemon_mode");
                    stdin_open = false;
                }
            }
        }
    }

    Ok(())
}
