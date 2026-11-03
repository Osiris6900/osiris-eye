//! Durable storage for cryptographically verified remote E2EE key bindings.
//!
//! Callers MUST verify the binding signature, peer identity, validity window,
//! trust policy, and revocation status before calling upsert_verified.
//! Private transport key material is never stored in this database.

use crate::e2ee::KeyBindingRecord;
use rusqlite::{params, Connection, OptionalExtension, TransactionBehavior};
use std::{error::Error, path::{Path, PathBuf}};

const MAX_PEER_KEY_BINDINGS: i64 = 4096;

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
        Ok(Self { connection, path })
    }

    pub fn path(&self) -> &Path { &self.path }

    pub fn len(&self) -> Result<usize, Box<dyn Error>> {
        let count: i64 = self.connection.query_row(
            "SELECT COUNT(*) FROM peer_key_bindings", [], |row| row.get(0)
        )?;
        Ok(count as usize)
    }

    pub fn get(&self, peer_id: &str) -> Result<Option<KeyBindingRecord>, Box<dyn Error>> {
        let encoded: Option<String> = self.connection.query_row(
            "SELECT record_json FROM peer_key_bindings WHERE peer_id = ?1",
            params![peer_id], |row| row.get(0)
        ).optional()?;
        encoded.map(|value| serde_json::from_str(&value).map_err(|error| -> Box<dyn Error> { Box::new(error) })).transpose()
    }

    /// Atomically persist a verified binding, rejecting epoch rollback and
    /// conflicting key material at an already-seen epoch.
    pub fn upsert_verified(&mut self, record: &KeyBindingRecord) -> Result<(), Box<dyn Error>> {
        let encoded = serde_json::to_string(record)?;
        let epoch = i64::try_from(record.key_epoch)
            .map_err(|_| "key epoch exceeds SQLite signed integer range")?;
        if epoch <= 0 { return Err("key epoch must be positive".into()); }

        let transaction = self.connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let current: Option<(i64, String)> = transaction.query_row(
            "SELECT key_epoch, record_json FROM peer_key_bindings WHERE peer_id = ?1",
            params![record.peer_id], |row| Ok((row.get(0)?, row.get(1)?))
        ).optional()?;

        match current {
            Some((current_epoch, _current_json)) if epoch < current_epoch => {
                return Err("key epoch rollback rejected".into());
            }
            Some((current_epoch, current_json)) if epoch == current_epoch => {
                let previous: KeyBindingRecord = serde_json::from_str(&current_json)?;
                if previous.transport_public_key != record.transport_public_key
                    || previous.identity_public_key != record.identity_public_key {
                    return Err("conflicting key binding at existing epoch".into());
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
    fn fails_closed_on_corrupt_database() {
        let path = temp_path("corrupt");
        std::fs::write(&path, b"not a sqlite database").unwrap();
        assert!(PeerKeyStore::open(path.clone()).is_err());
        cleanup(&path);
    }
}
