use ed25519_dalek::{Signature, Verifier, VerifyingKey};
use libp2p::PeerId;
use serde::{Deserialize, Serialize};
use std::{
    collections::HashMap,
    error::Error,
    fs::{self, OpenOptions},
    io::{Read, Write},
    path::{Path, PathBuf},
    time::{SystemTime, UNIX_EPOCH},
};

const SCHEMA: &str = "osiris.revocation.v1";
// Keep both recovery and authenticated announcements bounded. The registry is
// append-only in policy: reaching capacity rejects additions, never evicts bans.
const MAX_REGISTRY_RECORDS: usize = 16_384;
const MAX_REGISTRY_BYTES: u64 = 16 * 1024 * 1024;
const MAX_REASON_BYTES: usize = 128;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct SignedRevocationRecord {
    pub schema: String,
    pub authority_key: String,
    pub peer_id: String,
    pub sequence: u64,
    pub issued_at: u64,
    pub reason_code: String,
    pub signature: String,
}

#[derive(Serialize)]
struct SigningPayload<'a> {
    schema: &'a str,
    authority_key: &'a str,
    peer_id: &'a str,
    sequence: u64,
    issued_at: u64,
    reason_code: &'a str,
}

impl SignedRevocationRecord {
    fn payload(&self) -> Result<Vec<u8>, Box<dyn Error>> {
        Ok(serde_json::to_vec(&SigningPayload {
            schema: &self.schema,
            authority_key: &self.authority_key,
            peer_id: &self.peer_id,
            sequence: self.sequence,
            issued_at: self.issued_at,
            reason_code: &self.reason_code,
        })?)
    }

    pub fn verify(&self, authority_hex: &str) -> Result<PeerId, Box<dyn Error>> {
        if self.schema != SCHEMA {
            return Err("unsupported revocation record schema".into());
        }
        if self.peer_id.len() > 128 || self.reason_code.len() > MAX_REASON_BYTES
            || self.reason_code.trim().is_empty() || self.sequence == 0 || self.issued_at == 0 {
            return Err("revocation record has invalid sequence, timestamp, peer, or reason".into());
        }
        let configured = decode_hex::<32>(authority_hex)?;
        let embedded = decode_hex::<32>(&self.authority_key)?;
        if configured != embedded {
            return Err("revocation authority key mismatch".into());
        }
        let key = VerifyingKey::from_bytes(&configured)?;
        let signature_bytes = decode_hex::<64>(&self.signature)?;
        let signature = Signature::from_bytes(&signature_bytes);
        key.verify(&self.payload()?, &signature)
            .map_err(|_| "invalid revocation signature")?;
        Ok(self.peer_id.parse::<PeerId>()?)
    }
}

fn decode_hex<const N: usize>(value: &str) -> Result<[u8; N], Box<dyn Error>> {
    if value.len() != N * 2 {
        return Err("invalid hex field length".into());
    }
    let mut output = [0u8; N];
    fn nibble(byte: u8) -> Result<u8, Box<dyn Error>> {
        match byte {
            b'0'..=b'9' => Ok(byte - b'0'),
            b'a'..=b'f' => Ok(byte - b'a' + 10),
            b'A'..=b'F' => Ok(byte - b'A' + 10),
            _ => Err("invalid hex field character".into()),
        }
    }
    // Parse bytes rather than slicing str: malformed UTF-8 boundaries in a
    // network-supplied Unicode string must return an error, never panic.
    for (byte, pair) in output.iter_mut().zip(value.as_bytes().chunks_exact(2)) {
        *byte = (nibble(pair[0])? << 4) | nibble(pair[1])?;
    }
    Ok(output)
}

fn encode_hex(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

/// Outcome of applying one sync `Record` or snapshot entry.
/// Callers add `Applied` peers to runtime deny policy. `StaleOrReplay` does
/// not change the registry. Invalid signatures return `Err` and leave prior
/// revocations in place.
#[derive(Debug, PartialEq, Eq)]
pub enum SyncedRevocation {
    Applied(PeerId),
    StaleOrReplay,
}

pub fn apply_synced_revocation(
    registry: &mut RevocationRegistry,
    record: SignedRevocationRecord,
    persist: bool,
) -> Result<SyncedRevocation, Box<dyn Error>> {
    let peer_text = record.peer_id.clone();
    if registry.apply(record, persist)? {
        Ok(SyncedRevocation::Applied(peer_text.parse()?))
    } else {
        Ok(SyncedRevocation::StaleOrReplay)
    }
}


#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "kind", content = "payload", rename_all = "snake_case")]
pub enum RevocationSyncMessage {
    SyncRequest,
    SyncRequestPage { after_peer_id: Option<String> },
    SyncRequestPageV2 { request_id: String, requester_peer_id: String, after_peer_id: Option<String> },
    Record(SignedRevocationRecord),
    Snapshot(Vec<SignedRevocationRecord>),
    SnapshotPage { records: Vec<SignedRevocationRecord>, next_cursor: Option<String> },
    SnapshotPageV2 { request_id: String, requester_peer_id: String, responder_peer_id: String, records: Vec<SignedRevocationRecord>, next_cursor: Option<String> },
}

#[derive(Debug)]
pub struct RevocationRegistry {
    path: PathBuf,
    authority_hex: String,
    records: HashMap<String, SignedRevocationRecord>,
}

impl RevocationRegistry {
    pub fn open(path: PathBuf, authority_hex: String) -> Result<Self, Box<dyn Error>> {
        // Validate the configured trust root before reading any records.
        VerifyingKey::from_bytes(&decode_hex::<32>(&authority_hex)?)?;
        let mut registry = Self { path, authority_hex, records: HashMap::new() };
        if registry.path.exists() {
            let file = fs::File::open(&registry.path)?;
            if file.metadata()?.len() > MAX_REGISTRY_BYTES {
                return Err("revocation registry exceeds size limit".into());
            }
            let mut bytes = Vec::new();
            file.take(MAX_REGISTRY_BYTES + 1).read_to_end(&mut bytes)?;
            if bytes.len() as u64 > MAX_REGISTRY_BYTES {
                return Err("revocation registry exceeds size limit".into());
            }
            let loaded: Vec<SignedRevocationRecord> = serde_json::from_slice(&bytes)?;
            if loaded.len() > MAX_REGISTRY_RECORDS {
                return Err("revocation registry exceeds record limit".into());
            }
            for record in loaded {
                registry.apply(record, false)?;
            }
        }
        Ok(registry)
    }

    /// Accept only valid authority signatures and strictly increasing per-peer sequence numbers.
    pub fn apply(&mut self, record: SignedRevocationRecord, persist: bool) -> Result<bool, Box<dyn Error>> {
        let peer = record.verify(&self.authority_hex)?;
        let peer_id = peer.to_string();
        if self.records.get(&peer_id).is_some_and(|old| record.sequence <= old.sequence) {
            return Ok(false);
        }
        if !self.records.contains_key(&peer_id) && self.records.len() >= MAX_REGISTRY_RECORDS {
            return Err("revocation registry capacity exceeded; existing revocations retained".into());
        }
        if persist {
            // Persist the candidate snapshot before publishing it in memory. A disk
            // failure must not leave runtime policy stricter than restart policy.
            let mut candidate = self.records.clone();
            candidate.insert(peer_id.clone(), record);
            self.persist_records(&candidate)?;
            self.records = candidate;
        } else {
            self.records.insert(peer_id, record);
        }
        Ok(true)
    }

    pub fn records(&self) -> Vec<SignedRevocationRecord> {
        self.records.values().cloned().collect()
    }

    /// Return a stable, bounded page ordered by PeerId for anti-entropy synchronization.
    pub fn page_after(&self, after_peer_id: Option<&str>, limit: usize) -> (Vec<SignedRevocationRecord>, Option<String>) {
        let mut records: Vec<_> = self.records.values()
            .filter(|record| after_peer_id.map_or(true, |cursor| record.peer_id.as_str() > cursor))
            .cloned()
            .collect();
        records.sort_by(|a, b| a.peer_id.cmp(&b.peer_id));
        let page_limit = limit.clamp(1, 512);
        let has_more = records.len() > page_limit;
        records.truncate(page_limit);
        let next_cursor = if has_more { records.last().map(|record| record.peer_id.clone()) } else { None };
        (records, next_cursor)
    }

    pub fn revoked_peer_ids(&self) -> Result<Vec<PeerId>, Box<dyn Error>> {
        self.records.keys().map(|peer| Ok(peer.parse()?)).collect()
    }

    fn persist_records(
        &self,
        snapshot: &HashMap<String, SignedRevocationRecord>,
    ) -> Result<(), Box<dyn Error>> {
        let mut records: Vec<_> = snapshot.values().cloned().collect();
        records.sort_by(|a, b| a.peer_id.cmp(&b.peer_id));
        let bytes = serde_json::to_vec_pretty(&records)?;
        if bytes.len() as u64 > MAX_REGISTRY_BYTES {
            return Err("revocation registry exceeds size limit".into());
        }
        let parent = self.path.parent()
            .filter(|parent| !parent.as_os_str().is_empty())
            .unwrap_or_else(|| Path::new("."));
        fs::create_dir_all(parent)?;

        // A unique create_new file avoids stale-temp collisions and prevents
        // following a pre-existing symlink at the temporary path.
        let nonce = SystemTime::now().duration_since(UNIX_EPOCH)?.as_nanos();
        let temp = self.path.with_extension(format!("tmp-{}-{nonce}", std::process::id()));
        let mut options = OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let write_result = (|| -> Result<(), Box<dyn Error>> {
            let mut file = options.open(&temp)?;
            file.write_all(&bytes)?;
            file.sync_all()?;
            fs::rename(&temp, &self.path)?;
            // The rename is the commit point. The temp file was created mode
            // 0600, so no post-rename permission change can fail after commit.
            // A directory fsync failure is reported but must not reject the
            // already-visible revocation in the running process.
            if let Ok(directory) = fs::File::open(parent) {
                if let Err(error) = directory.sync_all() {
                    eprintln!("revocation_registry_directory_fsync_warning={error}");
                }
            }
            Ok(())
        })();
        if write_result.is_err() {
            let _ = fs::remove_file(&temp);
        }
        write_result
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use ed25519_dalek::{Signer, SigningKey};

    fn signed_record(key: &SigningKey, peer: PeerId, sequence: u64) -> SignedRevocationRecord {
        let public = encode_hex(key.verifying_key().as_bytes());
        let mut record = SignedRevocationRecord {
            schema: SCHEMA.into(),
            authority_key: public,
            peer_id: peer.to_string(),
            sequence,
            issued_at: SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs(),
            reason_code: "operator_compromise".into(),
            signature: String::new(),
        };
        record.signature = encode_hex(&key.sign(&record.payload().unwrap()).to_bytes());
        record
    }


    #[test]
    fn persisted_revocation_survives_restart_and_keeps_sequence_floor() {
        let key = SigningKey::from_bytes(&[31u8; 32]);
        let peer = PeerId::random();
        let path = std::env::temp_dir().join(format!("osiris-revocations-restart-{}", std::process::id()));
        let authority = encode_hex(key.verifying_key().as_bytes());
        {
            let mut registry = RevocationRegistry::open(path.clone(), authority.clone()).unwrap();
            assert!(registry.apply(signed_record(&key, peer, 4), true).unwrap());
        }
        {
            let mut registry = RevocationRegistry::open(path.clone(), authority).unwrap();
            assert_eq!(registry.revoked_peer_ids().unwrap(), vec![peer]);
            assert!(!registry.apply(signed_record(&key, peer, 3), true).unwrap());
            assert!(registry.apply(signed_record(&key, peer, 5), true).unwrap());
        }
        let _ = fs::remove_file(path);
    }

    #[test]
    fn persistence_failure_does_not_publish_revocation_in_memory() {
        let key = SigningKey::from_bytes(&[32u8; 32]);
        let peer = PeerId::random();
        let blocker = std::env::temp_dir().join(format!("osiris-revocation-blocker-{}", std::process::id()));
        fs::write(&blocker, b"not a directory").unwrap();
        let path = blocker.join("registry.json");
        let mut registry = RevocationRegistry::open(
            path,
            encode_hex(key.verifying_key().as_bytes()),
        ).unwrap();
        assert!(registry.apply(signed_record(&key, peer, 1), true).is_err());
        assert!(registry.revoked_peer_ids().unwrap().is_empty());
        let _ = fs::remove_file(blocker);
    }

    #[test]
    fn registry_pages_are_bounded_sorted_and_resume_after_cursor() {
        let key = SigningKey::from_bytes(&[11u8; 32]);
        let authority = encode_hex(key.verifying_key().as_bytes());
        let mut registry = RevocationRegistry::open(
            std::env::temp_dir().join(format!("osiris-revocations-pages-{}", std::process::id())),
            authority,
        ).unwrap();
        let peers = [PeerId::random(), PeerId::random(), PeerId::random(), PeerId::random()];
        for peer in peers {
            registry.apply(signed_record(&key, peer, 1), false).unwrap();
        }
        let mut sorted: Vec<_> = registry.records().iter().map(|record| record.peer_id.clone()).collect();
        sorted.sort();
        let (first, cursor) = registry.page_after(None, 2);
        assert_eq!(first.len(), 2);
        assert_eq!(cursor.as_deref(), Some(sorted[1].as_str()));
        let (second, next) = registry.page_after(cursor.as_deref(), 2);
        assert_eq!(second.len(), 2);
        assert_eq!(next, None);
        assert!(first.iter().all(|record| record.peer_id <= sorted[1]));
        assert!(second.iter().all(|record| record.peer_id > sorted[1]));
    }

    #[test]
    fn accepts_authority_signed_record_and_rejects_replay() {
        let key = SigningKey::from_bytes(&[7u8; 32]);
        let peer = PeerId::random();
        let record = signed_record(&key, peer, 1);
        let authority = encode_hex(key.verifying_key().as_bytes());
        let mut registry = RevocationRegistry::open(
            std::env::temp_dir().join(format!("osiris-revocations-{}", std::process::id())),
            authority,
        ).unwrap();
        assert!(registry.apply(record.clone(), false).unwrap());
        assert!(!registry.apply(record, false).unwrap());
        assert_eq!(registry.revoked_peer_ids().unwrap(), vec![peer]);
    }

    #[test]
    fn rejects_tampered_record_and_wrong_authority() {
        let key = SigningKey::from_bytes(&[8u8; 32]);
        let other = SigningKey::from_bytes(&[9u8; 32]);
        let mut record = signed_record(&key, PeerId::random(), 1);
        record.reason_code = "tampered".into();
        assert!(record.verify(&encode_hex(key.verifying_key().as_bytes())).is_err());
        let record = signed_record(&key, PeerId::random(), 1);
        assert!(record.verify(&encode_hex(other.verifying_key().as_bytes())).is_err());
    }

    #[test]
    fn rejects_non_monotonic_sequence() {
        let key = SigningKey::from_bytes(&[10u8; 32]);
        let peer = PeerId::random();
        let mut registry = RevocationRegistry::open(
            std::env::temp_dir().join(format!("osiris-revocations-seq-{}", std::process::id())),
            encode_hex(key.verifying_key().as_bytes()),
        ).unwrap();
        assert!(registry.apply(signed_record(&key, peer, 2), false).unwrap());
        assert!(!registry.apply(signed_record(&key, peer, 1), false).unwrap());
    }

    #[test]
    fn untrusted_unicode_hex_returns_error_without_panicking() {
        // These strings have the expected byte lengths, but slicing pairs as
        // UTF-8 text would split the three-byte character and panic.
        let malformed_key = format!("{}€", "a".repeat(61));
        let malformed_signature = format!("{}€", "a".repeat(125));
        assert!(decode_hex::<32>(&malformed_key).is_err());
        assert!(decode_hex::<64>(&malformed_signature).is_err());
        let key = SigningKey::from_bytes(&[61u8; 32]);
        let mut record = signed_record(&key, PeerId::random(), 1);
        record.signature = malformed_signature;
        assert!(record.verify(&encode_hex(key.verifying_key().as_bytes())).is_err());
    }

    #[test]
    fn oversized_registry_is_rejected_before_loading() {
        let key = SigningKey::from_bytes(&[62u8; 32]);
        let path = std::env::temp_dir().join(format!("osiris-revocation-oversized-{}", std::process::id()));
        let file = fs::File::create(&path).unwrap();
        file.set_len(MAX_REGISTRY_BYTES + 1).unwrap();
        drop(file);
        assert!(RevocationRegistry::open(path.clone(), encode_hex(key.verifying_key().as_bytes())).is_err());
        let _ = fs::remove_file(path);
    }

    #[test]
    fn oversized_reason_is_rejected_without_changing_registry() {
        let key = SigningKey::from_bytes(&[63u8; 32]);
        let path = std::env::temp_dir().join(format!("osiris-revocation-reason-{}", std::process::id()));
        let mut registry = RevocationRegistry::open(path, encode_hex(key.verifying_key().as_bytes())).unwrap();
        let mut record = signed_record(&key, PeerId::random(), 1);
        record.reason_code = "x".repeat(MAX_REASON_BYTES + 1);
        record.signature = encode_hex(&key.sign(&record.payload().unwrap()).to_bytes());
        assert!(registry.apply(record, true).is_err());
        assert!(registry.records().is_empty());
    }

    #[test]
    fn synced_revocation_rejects_forgery_and_stale_replay() {
        let authority = SigningKey::from_bytes(&[81u8; 32]);
        let attacker = SigningKey::from_bytes(&[82u8; 32]);
        let peer = PeerId::random();
        let extra = PeerId::random();
        let nonce = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_nanos();
        let path = std::env::temp_dir().join(format!("osiris-revocation-sync-{nonce}.json"));
        let authority_hex = encode_hex(authority.verifying_key().as_bytes());
        let record = signed_record(&authority, peer, 1);
        let bytes = serde_json::to_vec(&RevocationSyncMessage::Record(record)).unwrap();
        let mut follower = RevocationRegistry::open(path.clone(), authority_hex).unwrap();
        let decoded = serde_json::from_slice::<RevocationSyncMessage>(&bytes).unwrap();
        let RevocationSyncMessage::Record(decoded) = decoded else {
            panic!("record sync message did not round-trip");
        };
        assert_eq!(
            apply_synced_revocation(&mut follower, decoded.clone(), true).unwrap(),
            SyncedRevocation::Applied(peer),
        );
        assert_eq!(
            apply_synced_revocation(&mut follower, decoded, true).unwrap(),
            SyncedRevocation::StaleOrReplay,
        );
        assert!(apply_synced_revocation(&mut follower, signed_record(&attacker, extra, 1), true).is_err());
        let page = serde_json::to_vec(&RevocationSyncMessage::Snapshot(vec![
            signed_record(&authority, extra, 1),
            signed_record(&attacker, PeerId::random(), 1),
        ])).unwrap();
        let RevocationSyncMessage::Snapshot(records) = serde_json::from_slice(&page).unwrap() else {
            panic!("snapshot sync message did not round-trip");
        };
        let mut applied = Vec::new();
        for record in records {
            match apply_synced_revocation(&mut follower, record, true) {
                Ok(SyncedRevocation::Applied(revoked)) => applied.push(revoked),
                Ok(SyncedRevocation::StaleOrReplay) => {}
                Err(_) => {}
            }
        }
        assert_eq!(applied, vec![extra]);
        let revoked: std::collections::HashSet<_> = follower.revoked_peer_ids().unwrap().into_iter().collect();
        assert_eq!(revoked, std::collections::HashSet::from([peer, extra]));
        let _ = fs::remove_file(path);
    }
}
