use ed25519_dalek::{Signature, Verifier, VerifyingKey};
use libp2p::PeerId;
use serde::{Deserialize, Serialize};
use std::{
    collections::HashMap,
    error::Error,
    fs::{self, OpenOptions},
    io::Write,
    path::{Path, PathBuf},
};

const SCHEMA: &str = "osiris.revocation.v1";

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
        if self.sequence == 0 || self.issued_at == 0 || self.reason_code.trim().is_empty() {
            return Err("revocation record has invalid sequence, timestamp, or reason".into());
        }
        Ok(self.peer_id.parse::<PeerId>()?)
    }
}

fn decode_hex<const N: usize>(value: &str) -> Result<[u8; N], Box<dyn Error>> {
    if value.len() != N * 2 {
        return Err("invalid hex field length".into());
    }
    let mut output = [0u8; N];
    for (index, byte) in output.iter_mut().enumerate() {
        *byte = u8::from_str_radix(&value[index * 2..index * 2 + 2], 16)?;
    }
    Ok(output)
}

fn encode_hex(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}


#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "kind", content = "payload", rename_all = "snake_case")]
pub enum RevocationSyncMessage {
    SyncRequest,
    Record(SignedRevocationRecord),
    Snapshot(Vec<SignedRevocationRecord>),
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
            let bytes = fs::read(&registry.path)?;
            let loaded: Vec<SignedRevocationRecord> = serde_json::from_slice(&bytes)?;
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
        self.records.insert(peer_id, record);
        if persist {
            self.persist()?;
        }
        Ok(true)
    }

    pub fn records(&self) -> Vec<SignedRevocationRecord> {
        self.records.values().cloned().collect()
    }

    pub fn revoked_peer_ids(&self) -> Result<Vec<PeerId>, Box<dyn Error>> {
        self.records.keys().map(|peer| Ok(peer.parse()?)).collect()
    }

    fn persist(&self) -> Result<(), Box<dyn Error>> {
        let mut records: Vec<_> = self.records.values().cloned().collect();
        records.sort_by(|a, b| a.peer_id.cmp(&b.peer_id));
        let bytes = serde_json::to_vec_pretty(&records)?;
        let parent = self.path.parent().unwrap_or_else(|| Path::new("."));
        fs::create_dir_all(parent)?;
        let temp = self.path.with_extension(format!("tmp-{}", std::process::id()));
        let mut file = OpenOptions::new().write(true).create_new(true).open(&temp)?;
        file.write_all(&bytes)?;
        file.sync_all()?;
        fs::rename(&temp, &self.path)?;
        if let Ok(directory) = fs::File::open(parent) {
            let _ = directory.sync_all();
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use ed25519_dalek::{Signer, SigningKey};
    use std::time::{SystemTime, UNIX_EPOCH};

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
}
