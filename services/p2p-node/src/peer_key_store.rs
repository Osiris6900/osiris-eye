//! Durable storage for cryptographically verified remote E2EE key bindings.
//!
//! Callers MUST verify the binding signature, peer identity, validity window,
//! trust policy, and revocation status before calling upsert_verified.
//! Private transport key material is never stored in this database.

use crate::e2ee::KeyBindingRecord;
use rusqlite::{params, Connection, OptionalExtension, TransactionBehavior};
use std::{error::Error, path::{Path, PathBuf}};

const MAX_PEER_KEY_BINDINGS: i64 = 4096;
const MAX_BINDING_RECORD_BYTES: usize = 8192;
const SCHEMA_VERSION: i64 = 1;

pub struct PeerKeyStore {
    connection: Connection,
    path: PathBuf,
}

impl PeerKeyStore {
    pub fn open(path: PathBuf) -> Result<Self, Box<dyn Error>> {
        if let Some(parent) = path.parent().filter(|p| !p.as_os_str().is_empty()) {
            std::fs::create_dir_all(parent)?;
        }
        let connection = Connection::open(&path)?;
        let version: i64 = connection.pragma_query_value(None, "user_version", |row| row.get(0))?;
        if version != 0 && version != SCHEMA_VERSION {
            return Err("unsupported peer key store schema version".into());
        }
        connection.busy_timeout(std::time::Duration::from_secs(5))?;
        connection.pragma_update(None, "journal_mode", "WAL")?;
        connection.pragma_update(None, "synchronous", "FULL")?;
        connection.pragma_update(None, "foreign_keys", "ON")?;
        connection.execute_batch(
            "CREATE TABLE IF NOT EXISTS peer_key_bindings (
                peer_id TEXT PRIMARY KEY NOT NULL,
                key_epoch INTEGER NOT NULL CHECK(key_epoch > 0),
                record_json TEXT NOT NULL,
                updated_at INTEGER NOT NULL
            );
             PRAGMA user_version = 1;"
        )?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600))?;
        }
        let store = Self { connection, path };
        // An authenticated record's JSON and its indexed rollback floor must
        // agree on restart. A corrupt row must never silently weaken the floor.
        store.validate_stored_records()?;
        Ok(store)
    }

    pub fn path(&self) -> &Path { &self.path }

    pub fn len(&self) -> Result<usize, Box<dyn Error>> {
        let count: i64 = self.connection.query_row(
            "SELECT COUNT(*) FROM peer_key_bindings", [], |row| row.get(0)
        )?;
        Ok(count as usize)
    }

    pub fn get(&self, peer_id: &str) -> Result<Option<KeyBindingRecord>, Box<dyn Error>> {
        let row: Option<(i64, String)> = self.connection.query_row(
            "SELECT key_epoch, record_json FROM peer_key_bindings WHERE peer_id = ?1",
            params![peer_id], |row| Ok((row.get(0)?, row.get(1)?))
        ).optional()?;
        row.map(|(epoch, encoded)| Self::decode_stored_record(peer_id, epoch, &encoded)).transpose()
    }

    fn decode_stored_record(peer_id: &str, epoch: i64, encoded: &str) -> Result<KeyBindingRecord, Box<dyn Error>> {
        if encoded.len() > MAX_BINDING_RECORD_BYTES {
            return Err("stored key binding exceeds size limit".into());
        }
        let record: KeyBindingRecord = serde_json::from_str(encoded)?;
        if epoch <= 0 || record.key_epoch != epoch as u64 || record.peer_id != peer_id {
            return Err("stored key binding disagrees with indexed peer or epoch".into());
        }
        Ok(record)
    }

    fn validate_stored_records(&self) -> Result<(), Box<dyn Error>> {
        let (count, max_bytes): (i64, i64) = self.connection.query_row(
            "SELECT COUNT(*), COALESCE(MAX(length(CAST(record_json AS BLOB))), 0) FROM peer_key_bindings",
            [], |row| Ok((row.get(0)?, row.get(1)?))
        )?;
        if count > MAX_PEER_KEY_BINDINGS || max_bytes > MAX_BINDING_RECORD_BYTES as i64 {
            return Err("peer key store exceeds capacity or record size limit".into());
        }
        let mut statement = self.connection.prepare("SELECT peer_id, key_epoch, record_json FROM peer_key_bindings")?;
        let mut rows = statement.query([])?;
        while let Some(row) = rows.next()? {
            let peer_id: String = row.get(0)?;
            let epoch: i64 = row.get(1)?;
            let encoded: String = row.get(2)?;
            Self::decode_stored_record(&peer_id, epoch, &encoded)?;
        }
        Ok(())
    }

    /// Atomically persist a verified binding, rejecting epoch rollback and
    /// conflicting key material at an already-seen epoch.
    pub fn upsert_verified(&mut self, record: &KeyBindingRecord) -> Result<(), Box<dyn Error>> {
        let encoded = serde_json::to_string(record)?;
        if encoded.len() > MAX_BINDING_RECORD_BYTES {
            return Err("key binding exceeds size limit".into());
        }
        let epoch = i64::try_from(record.key_epoch)
            .map_err(|_| "key epoch exceeds SQLite signed integer range")?;
        if epoch <= 0 { return Err("key epoch must be positive".into()); }

        let transaction = self.connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let current: Option<(i64, String)> = transaction.query_row(
            "SELECT key_epoch, record_json FROM peer_key_bindings WHERE peer_id = ?1",
            params![record.peer_id], |row| Ok((row.get(0)?, row.get(1)?))
        ).optional()?;
        let current = current.map(|(epoch, encoded)| {
            Self::decode_stored_record(&record.peer_id, epoch, &encoded).map(|previous| (epoch, previous))
        }).transpose()?;

        match current {
            Some((current_epoch, _)) if epoch < current_epoch => {
                return Err("key epoch rollback rejected".into());
            }
            Some((current_epoch, previous)) if epoch == current_epoch => {
                if previous.transport_public_key != record.transport_public_key
                    || previous.identity_public_key != record.identity_public_key {
                    return Err("conflicting key binding at existing epoch".into());
                }
                if record.issued_at < previous.issued_at {
                    return Err("key binding refresh timestamp rollback rejected".into());
                }
                // An identical authenticated announcement must not append a WAL
                // update or fsync. Decode/index and rollback checks above still run.
                if serde_json::to_string(&previous)? == encoded {
                    transaction.commit()?;
                    return Ok(());
                }
                let now = std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)?.as_secs() as i64;
                transaction.execute(
                    "UPDATE peer_key_bindings SET record_json = ?2, updated_at = ?3 WHERE peer_id = ?1",
                    params![record.peer_id, encoded, now]
                )?;
            }
            None => {
                let count: i64 = transaction.query_row(
                    "SELECT COUNT(*) FROM peer_key_bindings", [], |row| row.get(0)
                )?;
                if count >= MAX_PEER_KEY_BINDINGS {
                    return Err("peer key store capacity exceeded".into());
                }
                let now = std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)?.as_secs() as i64;
                transaction.execute(
                    "INSERT INTO peer_key_bindings(peer_id, key_epoch, record_json, updated_at)
                     VALUES (?1, ?2, ?3, ?4)",
                    params![record.peer_id, epoch, encoded, now]
                )?;
            }
            Some(_) => {
                let now = std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)?.as_secs() as i64;
                transaction.execute(
                    "UPDATE peer_key_bindings SET key_epoch = ?2, record_json = ?3, updated_at = ?4
                     WHERE peer_id = ?1",
                    params![record.peer_id, epoch, encoded, now]
                )?;
            }
        }
        transaction.commit()?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::PeerKeyStore;
    use crate::e2ee::KeyBindingRecord;
    use std::{path::PathBuf, sync::atomic::{AtomicU64, Ordering}};

    static NEXT: AtomicU64 = AtomicU64::new(0);
    fn temp_path(label: &str) -> PathBuf {
        std::env::temp_dir().join(format!(
            "osiris-peer-keys-{label}-{}-{}.sqlite",
            std::process::id(), NEXT.fetch_add(1, Ordering::Relaxed)
        ))
    }
    fn record(epoch: u64, public_key: &str) -> KeyBindingRecord {
        KeyBindingRecord {
            schema: "osiris.p2p.key-binding.v1".into(),
            peer_id: "12D3KooWTestPeer".into(),
            identity_public_key: "identity".into(),
            transport_public_key: public_key.into(),
            key_epoch: epoch,
            issued_at: 1,
            expires_at: 100,
            signature: "signature".into(),
        }
    }
    fn cleanup(path: &PathBuf) {
        let _ = std::fs::remove_file(path);
        let _ = std::fs::remove_file(path.with_extension("sqlite-wal"));
        let _ = std::fs::remove_file(path.with_extension("sqlite-shm"));
    }

    #[test]
    fn binding_survives_restart() {
        let path = temp_path("restart");
        let expected = record(2, "transport-key-2");
        {
            let mut store = PeerKeyStore::open(path.clone()).unwrap();
            store.upsert_verified(&expected).unwrap();
        }
        {
            let store = PeerKeyStore::open(path.clone()).unwrap();
            let actual = store.get(&expected.peer_id).unwrap().unwrap();
            assert_eq!(actual.key_epoch, 2);
            assert_eq!(actual.transport_public_key, expected.transport_public_key);
        }
        cleanup(&path);
    }

    #[test]
    fn rejects_epoch_rollback_after_restart() {
        let path = temp_path("rollback");
        {
            let mut store = PeerKeyStore::open(path.clone()).unwrap();
            store.upsert_verified(&record(3, "key-3")).unwrap();
        }
        let mut restarted = PeerKeyStore::open(path.clone()).unwrap();
        assert!(restarted.upsert_verified(&record(2, "key-2")).is_err());
        assert_eq!(restarted.get("12D3KooWTestPeer").unwrap().unwrap().key_epoch, 3);
        cleanup(&path);
    }

    #[test]
    fn rejects_conflicting_key_at_same_epoch() {
        let path = temp_path("same-epoch");
        let mut store = PeerKeyStore::open(path.clone()).unwrap();
        store.upsert_verified(&record(1, "key-a")).unwrap();
        assert!(store.upsert_verified(&record(1, "key-b")).is_err());
        cleanup(&path);
    }

    #[test]
    fn accepts_higher_epoch_rotation() {
        let path = temp_path("rotation");
        let mut store = PeerKeyStore::open(path.clone()).unwrap();
        store.upsert_verified(&record(1, "key-a")).unwrap();
        store.upsert_verified(&record(2, "key-b")).unwrap();
        assert_eq!(store.get("12D3KooWTestPeer").unwrap().unwrap().transport_public_key, "key-b");
        cleanup(&path);
    }

    #[test]
    fn identical_reannouncement_is_read_only_but_refresh_is_durable() {
        let path = temp_path("idempotent-refresh");
        let mut store = PeerKeyStore::open(path.clone()).unwrap();
        let original = record(1, "key");
        store.upsert_verified(&original).unwrap();
        let changes = store.connection.total_changes();
        for _ in 0..100 {
            store.upsert_verified(&original).unwrap();
        }
        assert_eq!(store.connection.total_changes(), changes);
        let mut refreshed = original.clone();
        refreshed.issued_at += 1;
        refreshed.expires_at += 1;
        store.upsert_verified(&refreshed).unwrap();
        assert_eq!(store.connection.total_changes(), changes + 1);
        drop(store);
        let reopened = PeerKeyStore::open(path.clone()).unwrap();
        assert_eq!(reopened.get(&original.peer_id).unwrap().unwrap().issued_at, refreshed.issued_at);
        drop(reopened);
        cleanup(&path);
    }

    #[test]
    fn fails_closed_on_corrupt_database() {
        let path = temp_path("corrupt");
        std::fs::write(&path, b"not a sqlite database").unwrap();
        assert!(PeerKeyStore::open(path.clone()).is_err());
        cleanup(&path);
    }

    #[test]
    fn rejects_indexed_epoch_corruption_on_read_and_restart() {
        let path = temp_path("indexed-corruption");
        let mut store = PeerKeyStore::open(path.clone()).unwrap();
        let binding = record(3, "key-3");
        store.upsert_verified(&binding).unwrap();
        store.connection.execute(
            "UPDATE peer_key_bindings SET record_json = ?1",
            [serde_json::to_string(&record(1, "key-1")).unwrap()],
        ).unwrap();
        assert!(store.get(&binding.peer_id).is_err());
        // Rotation must not overwrite evidence of a corrupt rollback floor.
        assert!(store.upsert_verified(&record(4, "key-4")).is_err());
        drop(store);
        assert!(PeerKeyStore::open(path.clone()).is_err());
        cleanup(&path);
    }

    #[test]
    fn rejects_stored_peer_substitution() {
        let path = temp_path("peer-substitution");
        let mut store = PeerKeyStore::open(path.clone()).unwrap();
        let binding = record(1, "key");
        store.upsert_verified(&binding).unwrap();
        let mut substituted = binding.clone();
        substituted.peer_id = "different-peer".into();
        store.connection.execute(
            "UPDATE peer_key_bindings SET record_json = ?1",
            [serde_json::to_string(&substituted).unwrap()],
        ).unwrap();
        assert!(store.get(&binding.peer_id).is_err());
        drop(store);
        cleanup(&path);
    }

    #[test]
    fn refuses_future_schema_without_rewriting_version() {
        let path = temp_path("future-schema");
        let connection = rusqlite::Connection::open(&path).unwrap();
        connection.pragma_update(None, "user_version", 99).unwrap();
        assert!(PeerKeyStore::open(path.clone()).is_err());
        let version: i64 = connection.pragma_query_value(None, "user_version", |row| row.get(0)).unwrap();
        assert_eq!(version, 99);
        drop(connection);
        cleanup(&path);
    }

    #[test]
    fn same_epoch_refresh_cannot_restore_older_announcement() {
        let path = temp_path("refresh-rollback");
        let mut store = PeerKeyStore::open(path.clone()).unwrap();
        let original = record(1, "key");
        let mut refreshed = original.clone();
        refreshed.issued_at = 50;
        refreshed.expires_at = 150;
        store.upsert_verified(&original).unwrap();
        store.upsert_verified(&refreshed).unwrap();
        drop(store);
        let mut store = PeerKeyStore::open(path.clone()).unwrap();
        assert!(store.upsert_verified(&original).is_err());
        assert_eq!(store.get(&original.peer_id).unwrap().unwrap().issued_at, 50);
        drop(store);
        cleanup(&path);
    }

    #[test]
    fn oversized_binding_is_not_persisted() {
        let path = temp_path("oversized");
        let mut store = PeerKeyStore::open(path.clone()).unwrap();
        let oversized = record(1, &"x".repeat(super::MAX_BINDING_RECORD_BYTES));
        assert!(store.upsert_verified(&oversized).is_err());
        assert_eq!(store.len().unwrap(), 0);
        drop(store);
        cleanup(&path);
    }
}
