//! Bounded, private prepublication storage for recipient observations.
//!
//! Callers provide already validated canonical observation bytes. A claim is
//! persisted before publication. Only a definitely-unsent result may be deferred;
//! successful or ambiguous publication must be completed or held, never retried.
//! Reopening holds interrupted claims. The configured absolute database path must
//! have an existing private, owner-controlled directory; no directories or weaker
//! permissions are silently repaired. Expiry and current policy are checked by
//! the node before every publication, using `enqueued_at` and the exported TTL.

use libp2p::PeerId;
use rusqlite::{params, Connection, OptionalExtension, TransactionBehavior};
use sha2::{Digest, Sha256};
use std::{
    fs::{self, File},
    path::{Path, PathBuf},
    time::Duration,
};

pub const MAX_QUEUE_AGE_SECONDS: u64 = 120;
pub const MAX_ATTEMPTS: u32 = 16;
pub const MAX_QUEUE_ROWS: usize = 1024;
pub const MAX_QUEUE_BYTES: usize = 16 * 1024 * 1024;
pub const MAX_OBSERVATION_BYTES: usize = 2 * 1024 * 1024;
const MAX_TIME: u64 = 253_402_300_799;
const SCHEMA_VERSION: i64 = 1;
const CREATE_TABLE: &str = "CREATE TABLE outbound_observations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    recipient TEXT NOT NULL,
                    payload BLOB NOT NULL,
                    digest TEXT NOT NULL,
                    enqueued_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    next_due INTEGER NOT NULL,
                    attempts INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    UNIQUE(recipient,digest)
                 )";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct QueuedObservation {
    pub id: i64,
    pub recipient: String,
    pub observation: Vec<u8>,
    pub attempts: u32,
    pub enqueued_at: u64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct QueueCounts {
    pub queued: usize,
    pub publishing: usize,
    pub held: usize,
    pub payload_bytes: usize,
}

pub struct OutboundQueue {
    connection: Connection,
    path: PathBuf,
    // Advisory lifetime lock prevents a second node from reclassifying an
    // active publisher's claim as crash recovery. SQLite owns its own locks.
    _file: File,
}

type Result<T> = std::result::Result<T, String>;

fn db_error(_: rusqlite::Error) -> String {
    "outbound_queue_database_error".into()
}

fn time(value: u64) -> Result<i64> {
    if value == 0 || value > MAX_TIME {
        return Err("outbound_queue_time_invalid".into());
    }
    Ok(value as i64)
}

fn peer(value: &str) -> Result<()> {
    if value.len() > 128
        || value
            .parse::<PeerId>()
            .map(|p| p.to_string())
            .ok()
            .as_deref()
            != Some(value)
    {
        return Err("outbound_queue_recipient_invalid".into());
    }
    Ok(())
}

fn reason(value: &str) -> Result<()> {
    if value.is_empty() || value.len() > 128 || !value.bytes().all(|b| (32..=126).contains(&b)) {
        return Err("outbound_queue_hold_reason_invalid".into());
    }
    Ok(())
}

fn hash(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

#[cfg(target_os = "linux")]
fn private_directory(path: &Path) -> Result<u32> {
    use std::os::unix::fs::MetadataExt;
    unsafe extern "C" {
        fn geteuid() -> u32;
    }
    let uid = unsafe { geteuid() };
    if !path.is_absolute() || path.file_name().is_none() {
        return Err("outbound_queue_absolute_file_path_required".into());
    }
    let parent = path
        .parent()
        .ok_or("outbound_queue_private_directory_required")?;
    let directory =
        fs::symlink_metadata(parent).map_err(|_| "outbound_queue_private_directory_required")?;
    if !directory.is_dir()
        || directory.uid() != uid
        || directory.mode() & 0o077 != 0
        || parent
            .canonicalize()
            .map_err(|_| "outbound_queue_private_directory_required")?
            != parent
    {
        return Err("outbound_queue_private_directory_required".into());
    }
    Ok(uid)
}

#[cfg(target_os = "linux")]
fn check_private_storage(path: &Path, file: &File) -> Result<()> {
    use std::os::unix::fs::MetadataExt;
    let uid = private_directory(path)?;
    let metadata = file
        .metadata()
        .map_err(|_| "outbound_queue_private_file_required")?;
    let named = fs::symlink_metadata(path).map_err(|_| "outbound_queue_private_file_required")?;
    if !metadata.is_file()
        || metadata.uid() != uid
        || metadata.mode() & 0o077 != 0
        || metadata.nlink() != 1
        || !named.is_file()
        || named.uid() != uid
        || named.mode() & 0o077 != 0
        || named.nlink() != 1
        || metadata.dev() != named.dev()
        || metadata.ino() != named.ino()
    {
        return Err("outbound_queue_private_file_required".into());
    }
    Ok(())
}

#[cfg(not(target_os = "linux"))]
fn check_private_storage(_path: &Path, _file: &File) -> Result<()> {
    Err("outbound_queue_secure_storage_requires_linux".into())
}

#[cfg(target_os = "linux")]
fn private_file(path: &Path) -> Result<File> {
    use std::fs::OpenOptions;
    use std::os::unix::fs::OpenOptionsExt;
    private_directory(path)?;
    // O_NOFOLLOW closes the final-component symlink race. The containing
    // directory is private, and inode/link checks reject path substitution.
    let mut options = OpenOptions::new();
    options.read(true).write(true).custom_flags(0x20000);
    let file = match options.open(path) {
        Ok(file) => file,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => options
            .create_new(true)
            .mode(0o600)
            .open(path)
            .map_err(|_| "outbound_queue_private_file_required")?,
        Err(_) => return Err("outbound_queue_private_file_required".into()),
    };
    check_private_storage(path, &file)?;
    file.try_lock()
        .map_err(|_| "outbound_queue_already_open".to_string())?;
    Ok(file)
}

#[cfg(not(target_os = "linux"))]
fn private_file(_path: &Path) -> Result<File> {
    Err("outbound_queue_secure_storage_requires_linux".into())
}

impl OutboundQueue {
    pub fn open(path: PathBuf) -> Result<Self> {
        let file = private_file(&path)?;
        let connection = Connection::open(&path).map_err(db_error)?;
        connection
            .busy_timeout(Duration::from_secs(5))
            .map_err(db_error)?;
        let version: i64 = connection
            .pragma_query_value(None, "user_version", |r| r.get(0))
            .map_err(db_error)?;
        let tables: Vec<String> = connection.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
            .map_err(db_error)?.query_map([], |r| r.get(0)).map_err(db_error)?
            .collect::<std::result::Result<_, _>>().map_err(db_error)?;
        if version != 0 && version != SCHEMA_VERSION
            || version == 0 && !tables.is_empty()
            || version == SCHEMA_VERSION && tables != ["outbound_observations"]
        {
            return Err("outbound_queue_unsupported_schema".into());
        }
        if version == 0 {
            connection
                .execute_batch(&format!(
                    "BEGIN IMMEDIATE;{CREATE_TABLE};PRAGMA user_version=1;COMMIT;"
                ))
                .map_err(db_error)?;
        }
        let columns: Vec<String> = connection
            .prepare("PRAGMA table_info(outbound_observations)")
            .map_err(db_error)?
            .query_map([], |r| r.get(1))
            .map_err(db_error)?
            .collect::<std::result::Result<_, _>>()
            .map_err(db_error)?;
        if columns
            != [
                "id",
                "recipient",
                "payload",
                "digest",
                "enqueued_at",
                "updated_at",
                "next_due",
                "attempts",
                "state",
                "reason",
            ]
        {
            return Err("outbound_queue_unsupported_schema".into());
        }
        let table_sql: String = connection
            .query_row(
                "SELECT sql FROM sqlite_master WHERE name='outbound_observations' AND type='table'",
                [],
                |r| r.get(0),
            )
            .map_err(db_error)?;
        let objects: i64 = connection
            .query_row(
                "SELECT COUNT(*) FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'",
                [],
                |r| r.get(0),
            )
            .map_err(db_error)?;
        if table_sql != CREATE_TABLE || objects != 1 {
            return Err("outbound_queue_unsupported_schema".into());
        }
        connection
            .pragma_update(None, "journal_mode", "DELETE")
            .map_err(db_error)?;
        connection
            .pragma_update(None, "synchronous", "FULL")
            .map_err(db_error)?;
        let mut queue = Self {
            connection,
            path,
            _file: file,
        };
        queue.check_storage()?;
        Self::validate(&queue.connection)?;
        let transaction = queue
            .connection
            .transaction_with_behavior(TransactionBehavior::Immediate)
            .map_err(db_error)?;
        transaction.execute("UPDATE outbound_observations SET state='held',reason='ambiguous_after_restart' WHERE state='publishing'", []).map_err(db_error)?;
        transaction.commit().map_err(db_error)?;
        queue.check_storage()?;
        Ok(queue)
    }

    /// Namespace and permission checks must follow the locked file throughout
    /// its lifetime. Replacing/unlinking a database bypasses an inode lock for
    /// a second opener; the old handle must then stop before claiming work.
    pub fn check_storage(&self) -> Result<()> {
        check_private_storage(&self.path, &self._file)
    }

    fn validate(connection: &Connection) -> Result<()> {
        let (rows, bytes, largest): (i64, i64, i64) = connection.query_row(
            "SELECT COUNT(*),COALESCE(SUM(length(payload)),0),COALESCE(MAX(length(payload)),0) FROM outbound_observations",
            [], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?))).map_err(db_error)?;
        if rows > MAX_QUEUE_ROWS as i64
            || bytes > MAX_QUEUE_BYTES as i64
            || largest > MAX_OBSERVATION_BYTES as i64
        {
            return Err("outbound_queue_capacity_exceeded".into());
        }
        let mut statement = connection.prepare("SELECT id,recipient,payload,digest,enqueued_at,updated_at,next_due,attempts,state,reason FROM outbound_observations").map_err(db_error)?;
        let mut rows = statement.query([]).map_err(db_error)?;
        while let Some(row) = rows.next().map_err(db_error)? {
            let id: i64 = row.get(0).map_err(db_error)?;
            let recipient: String = row.get(1).map_err(db_error)?;
            let payload: Vec<u8> = row.get(2).map_err(db_error)?;
            let digest: String = row.get(3).map_err(db_error)?;
            let enqueued: i64 = row.get(4).map_err(db_error)?;
            let updated: i64 = row.get(5).map_err(db_error)?;
            let due: i64 = row.get(6).map_err(db_error)?;
            let attempts: i64 = row.get(7).map_err(db_error)?;
            let state: String = row.get(8).map_err(db_error)?;
            let held_reason: String = row.get(9).map_err(db_error)?;
            peer(&recipient)?;
            if id <= 0
                || payload.is_empty()
                || payload.len() > MAX_OBSERVATION_BYTES
                || hash(&payload) != digest
                || enqueued <= 0
                || updated < enqueued
                || due < updated
                || updated as u64 > MAX_TIME
                || due as u64 > MAX_TIME
                || !(0..=MAX_ATTEMPTS as i64).contains(&attempts)
                || !matches!(state.as_str(), "queued" | "publishing" | "held")
                || state == "publishing" && attempts == 0
                || state == "queued" && attempts >= MAX_ATTEMPTS as i64
                || state != "held" && !held_reason.is_empty()
            {
                return Err("outbound_queue_record_invalid".into());
            }
            if state == "held" {
                reason(&held_reason)?;
            }
        }
        Ok(())
    }

    /// An existing queued, claimed or held copy is never reset by a duplicate.
    pub fn enqueue(&mut self, recipient: &str, observation: &[u8], now: u64) -> Result<bool> {
        self.check_storage()?;
        peer(recipient)?;
        let now = time(now)?;
        if observation.is_empty() || observation.len() > MAX_OBSERVATION_BYTES {
            return Err("outbound_queue_observation_size_invalid".into());
        }
        let digest = hash(observation);
        let tx = self
            .connection
            .transaction_with_behavior(TransactionBehavior::Immediate)
            .map_err(db_error)?;
        let existing: Option<Vec<u8>> = tx
            .query_row(
                "SELECT payload FROM outbound_observations WHERE recipient=?1 AND digest=?2",
                params![recipient, digest],
                |r| r.get(0),
            )
            .optional()
            .map_err(db_error)?;
        if let Some(existing) = existing {
            if existing != observation {
                return Err("outbound_queue_digest_conflict".into());
            }
            tx.commit().map_err(db_error)?;
            self.check_storage()?;
            return Ok(false);
        }
        let (rows, bytes): (i64, i64) = tx
            .query_row(
                "SELECT COUNT(*),COALESCE(SUM(length(payload)),0) FROM outbound_observations",
                [],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )
            .map_err(db_error)?;
        if rows >= MAX_QUEUE_ROWS as i64
            || bytes + observation.len() as i64 > MAX_QUEUE_BYTES as i64
        {
            return Err("outbound_queue_capacity_exceeded".into());
        }
        tx.execute("INSERT INTO outbound_observations(recipient,payload,digest,enqueued_at,updated_at,next_due,attempts,state,reason) VALUES(?1,?2,?3,?4,?4,?4,0,'queued','')", params![recipient, observation, digest, now]).map_err(db_error)?;
        tx.commit().map_err(db_error)?;
        self.check_storage()?;
        Ok(true)
    }

    fn validate_write_state(tx: &Connection, now: i64) -> Result<()> {
        // Revalidate inside the same write transaction as the update. A local
        // SQLite writer must not replace payloads or retry metadata after open.
        Self::validate(tx)?;
        let table_sql: String = tx
            .query_row(
                "SELECT sql FROM sqlite_master WHERE name='outbound_observations' AND type='table'",
                [],
                |r| r.get(0),
            )
            .map_err(db_error)?;
        let objects: i64 = tx
            .query_row(
                "SELECT COUNT(*) FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'",
                [],
                |r| r.get(0),
            )
            .map_err(db_error)?;
        if table_sql != CREATE_TABLE || objects != 1 {
            return Err("outbound_queue_unsupported_schema".into());
        }
        let latest: i64 = tx
            .query_row(
                "SELECT COALESCE(MAX(updated_at),0) FROM outbound_observations",
                [],
                |r| r.get(0),
            )
            .map_err(db_error)?;
        if now < latest {
            return Err("outbound_queue_clock_rollback".into());
        }
        Ok(())
    }

    /// A newly connected recipient can bring definitely-unsent retries forward.
    /// Require a new wall-clock second after the last attempt, and preserve the
    /// attempt count and enqueue time. Claimed and held records stay untouched.
    pub fn wake_deferred_recipient(&mut self, recipient: &str, now: u64) -> Result<usize> {
        self.check_storage()?;
        peer(recipient)?;
        let now = time(now)?;
        let tx = self
            .connection
            .transaction_with_behavior(TransactionBehavior::Immediate)
            .map_err(db_error)?;
        Self::validate_write_state(&tx, now)?;
        let woken = tx.execute(
            "UPDATE outbound_observations SET next_due=MAX(?1,updated_at+1) \
             WHERE recipient=?2 AND state='queued' AND attempts>0 \
             AND next_due>MAX(?1,updated_at+1)",
            params![now, recipient],
        ).map_err(db_error)?;
        tx.commit().map_err(db_error)?;
        self.check_storage()?;
        Ok(woken)
    }

    pub fn claim_due(&mut self, now: u64, limit: usize) -> Result<Vec<QueuedObservation>> {
        self.check_storage()?;
        let now = time(now)?;
        if !(1..=32).contains(&limit) {
            return Err("outbound_queue_claim_limit_invalid".into());
        }
        let tx = self
            .connection
            .transaction_with_behavior(TransactionBehavior::Immediate)
            .map_err(db_error)?;
        Self::validate_write_state(&tx, now)?;
        let mut items = Vec::new();
        {
            let mut statement = tx.prepare("SELECT id,recipient,payload,attempts,enqueued_at FROM outbound_observations WHERE state='queued' AND next_due<=?1 ORDER BY id LIMIT ?2").map_err(db_error)?;
            let mut rows = statement
                .query(params![now, limit as i64])
                .map_err(db_error)?;
            while let Some(row) = rows.next().map_err(db_error)? {
                items.push(QueuedObservation {
                    id: row.get(0).map_err(db_error)?,
                    recipient: row.get(1).map_err(db_error)?,
                    observation: row.get(2).map_err(db_error)?,
                    attempts: row.get::<_, u32>(3).map_err(db_error)? + 1,
                    enqueued_at: row.get::<_, i64>(4).map_err(db_error)? as u64,
                });
            }
        }
        for item in &items {
            let changed = tx.execute("UPDATE outbound_observations SET state='publishing',attempts=attempts+1,updated_at=?1,next_due=?1 WHERE id=?2 AND state='queued'", params![now, item.id]).map_err(db_error)?;
            if changed != 1 {
                return Err("outbound_queue_claim_conflict".into());
            }
        }
        tx.commit().map_err(db_error)?;
        self.check_storage()?;
        for item in &items {
            if item.attempts > 1 {
                // Audit a committed retry-processing claim without exposing
                // plaintext. This is not evidence of network transmission.
                eprintln!("outbound_observation_retry id={} recipient={} attempt={} observation_sha256={}", item.id, item.recipient, item.attempts, hash(&item.observation));
            }
        }
        Ok(items)
    }

    /// Only call when publication definitely did not occur. Ambiguous sends
    /// belong in `hold`, including exceptions after an attempted publication.
    pub fn defer_unsent(&mut self, id: i64, now: u64) -> Result<bool> {
        self.check_storage()?;
        let now = time(now)?;
        let tx = self
            .connection
            .transaction_with_behavior(TransactionBehavior::Immediate)
            .map_err(db_error)?;
        let current: Option<(u32, i64)> = tx.query_row("SELECT attempts,updated_at FROM outbound_observations WHERE id=?1 AND state='publishing'", [id], |r| Ok((r.get(0)?, r.get(1)?))).optional().map_err(db_error)?;
        let (attempts, updated) = current.ok_or("outbound_queue_publishing_record_required")?;
        if now < updated {
            return Err("outbound_queue_clock_rollback".into());
        }
        let retry = attempts < MAX_ATTEMPTS;
        if retry {
            let delay = 1_i64 << attempts.saturating_sub(1).min(3);
            if now as u64 + delay as u64 > MAX_TIME {
                return Err("outbound_queue_time_invalid".into());
            }
            tx.execute("UPDATE outbound_observations SET state='queued',updated_at=?1,next_due=?2 WHERE id=?3", params![now, now + delay, id]).map_err(db_error)?;
        } else {
            tx.execute("UPDATE outbound_observations SET state='held',reason='retry_limit_reached',updated_at=?1,next_due=?1 WHERE id=?2", params![now, id]).map_err(db_error)?;
        }
        tx.commit().map_err(db_error)?;
        self.check_storage()?;
        Ok(retry)
    }

    pub fn hold(&mut self, id: i64, hold_reason: &str) -> Result<bool> {
        self.check_storage()?;
        reason(hold_reason)?;
        let changed = self.connection.execute("UPDATE outbound_observations SET state='held',reason=?1 WHERE id=?2 AND state IN ('queued','publishing')", params![hold_reason, id])
            .map_err(db_error)?;
        self.check_storage()?;
        Ok(changed == 1)
    }

    /// Only an active publishing claim can be completed. No receipt, timeout or
    /// restart automatically deletes records or makes held records retryable.
    pub fn complete(&mut self, id: i64) -> Result<bool> {
        self.check_storage()?;
        let changed = self
            .connection
            .execute(
                "DELETE FROM outbound_observations WHERE id=?1 AND state='publishing'",
                [id],
            )
            .map_err(db_error)?;
        self.check_storage()?;
        Ok(changed == 1)
    }

    pub fn counts(&self) -> Result<QueueCounts> {
        self.check_storage()?;
        self.connection.query_row("SELECT COALESCE(SUM(state='queued'),0),COALESCE(SUM(state='publishing'),0),COALESCE(SUM(state='held'),0),COALESCE(SUM(length(payload)),0) FROM outbound_observations", [], |r| Ok(QueueCounts { queued:r.get::<_, i64>(0)? as usize,publishing:r.get::<_, i64>(1)? as usize,held:r.get::<_, i64>(2)? as usize,payload_bytes:r.get::<_, i64>(3)? as usize })).map_err(db_error)
    }
}

#[cfg(all(test, target_os = "linux"))]
mod tests {
    use super::*;
    use std::os::unix::fs::{DirBuilderExt, PermissionsExt};

    struct Directory(PathBuf);
    impl Directory {
        fn new() -> Self {
            let path = std::env::temp_dir().join(format!(
                "osiris-outbound-{}-{}",
                std::process::id(),
                PeerId::random()
            ));
            fs::DirBuilder::new().mode(0o700).create(&path).unwrap();
            Self(path)
        }
        fn database(&self) -> PathBuf {
            self.0.join("outbound.sqlite3")
        }
    }
    impl Drop for Directory {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }
    fn recipient() -> String {
        PeerId::random().to_string()
    }

    #[test]
    fn durable_claims_hold_after_restart_and_success_is_not_retried() {
        let dir = Directory::new();
        let peer = recipient();
        let mut queue = OutboundQueue::open(dir.database()).unwrap();
        assert!(queue.enqueue(&peer, b"first", 100).unwrap());
        assert!(queue.enqueue(&peer, b"second", 100).unwrap());
        assert!(queue.enqueue(&peer, b"third", 100).unwrap());
        let claimed = queue.claim_due(100, 2).unwrap();
        assert_eq!(claimed.len(), 2);
        assert!(queue.complete(claimed[0].id).unwrap());
        assert!(!queue.complete(claimed[0].id).unwrap());
        drop(queue);
        let mut reopened = OutboundQueue::open(dir.database()).unwrap();
        assert_eq!(
            reopened.counts().unwrap(),
            QueueCounts {
                queued: 1,
                publishing: 0,
                held: 1,
                payload_bytes: 11
            }
        );
        let pending = reopened.claim_due(101, 32).unwrap();
        assert_eq!(pending.len(), 1);
        assert_eq!(pending[0].observation, b"third");
        assert!(!reopened.enqueue(&peer, b"second", 102).unwrap());
        assert!(!reopened.complete(claimed[1].id).unwrap());
        assert!(reopened.defer_unsent(claimed[1].id, 103).is_err());
    }

    #[test]
    fn duplicate_never_resets_attempts_age_or_held_disposition() {
        let dir = Directory::new();
        let peer = recipient();
        let mut queue = OutboundQueue::open(dir.database()).unwrap();
        queue.enqueue(&peer, b"observation", 100).unwrap();
        let first = queue.claim_due(100, 1).unwrap().remove(0);
        assert!(queue.defer_unsent(first.id, 100).unwrap());
        assert!(!queue.enqueue(&peer, b"observation", 100).unwrap());
        assert!(queue.claim_due(100, 1).unwrap().is_empty());
        let second = queue.claim_due(101, 1).unwrap().remove(0);
        assert_eq!(second.enqueued_at, 100);
        assert_eq!(second.attempts, 2);
        assert!(queue.hold(second.id, "recipient_policy_revoked").unwrap());
        assert!(!queue.enqueue(&peer, b"observation", 110).unwrap());
        assert!(!queue.hold(second.id, "replace_reason").unwrap());
        assert!(queue.claim_due(110, 1).unwrap().is_empty());
        assert_eq!(queue.counts().unwrap().held, 1);
    }

    #[test]
    fn claims_are_exclusive_and_second_writer_is_rejected() {
        let dir = Directory::new();
        let peer = recipient();
        let mut queue = OutboundQueue::open(dir.database()).unwrap();
        assert!(
            matches!(OutboundQueue::open(dir.database()), Err(e) if e == "outbound_queue_already_open")
        );
        for value in [b"one", b"two"] {
            queue.enqueue(&peer, value, 100).unwrap();
        }
        let first = queue.claim_due(100, 1).unwrap().remove(0);
        let second = queue.claim_due(100, 1).unwrap().remove(0);
        assert_ne!(first.id, second.id);
        assert!(queue.claim_due(100, 32).unwrap().is_empty());
        assert_eq!(queue.counts().unwrap().publishing, 2);
        assert!(queue.claim_due(100, 0).is_err());
        assert!(queue.claim_due(100, 33).is_err());
    }

    #[test]
    fn unsent_retries_have_bounded_backoff_and_attempts() {
        let dir = Directory::new();
        let peer = recipient();
        let mut queue = OutboundQueue::open(dir.database()).unwrap();
        queue.enqueue(&peer, b"observation", 100).unwrap();
        let mut now = 100;
        for attempt in 1..=MAX_ATTEMPTS {
            let claimed = queue.claim_due(now, 1).unwrap().remove(0);
            assert_eq!(claimed.attempts, attempt);
            assert!(queue.defer_unsent(claimed.id, now - 1).is_err());
            assert_eq!(
                queue.defer_unsent(claimed.id, now).unwrap(),
                attempt < MAX_ATTEMPTS
            );
            if attempt < MAX_ATTEMPTS {
                let delay = 1_u64 << attempt.saturating_sub(1).min(3);
                assert!(queue.claim_due(now + delay - 1, 1).unwrap().is_empty());
                now += delay;
            }
        }
        assert_eq!(queue.counts().unwrap().held, 1);
        assert!(queue.claim_due(now + 100, 1).unwrap().is_empty());
    }

    #[test]
    fn recipient_wakeup_advances_only_its_deferred_rows_without_resetting_attempts() {
        let dir = Directory::new();
        let recipient = recipient();
        let other = PeerId::random().to_string();
        let mut queue = OutboundQueue::open(dir.database()).unwrap();
        for payload in [b"deferred".as_slice(), b"held", b"claimed"] {
            queue.enqueue(&recipient, payload, 100).unwrap();
        }
        queue.enqueue(&other, b"other", 100).unwrap();
        let claimed = queue.claim_due(100, 4).unwrap();
        assert!(queue.defer_unsent(claimed[0].id, 100).unwrap());
        assert!(queue.hold(claimed[1].id, "publication_uncertain").unwrap());
        assert!(queue.defer_unsent(claimed[3].id, 100).unwrap());
        for item in queue.claim_due(101, 4).unwrap() {
            assert!(queue.defer_unsent(item.id, 101).unwrap());
        }
        assert_eq!(queue.wake_deferred_recipient(&recipient, 100).unwrap_err(),
            "outbound_queue_clock_rollback");
        assert_eq!(queue.wake_deferred_recipient(&recipient, 101).unwrap(), 1);
        assert!(queue.claim_due(101, 4).unwrap().is_empty());
        assert_eq!(queue.wake_deferred_recipient(&recipient, 102).unwrap(), 0);

        let retry = queue.claim_due(102, 4).unwrap();
        assert_eq!(retry.len(), 1);
        assert_eq!(retry[0].id, claimed[0].id);
        assert_eq!(retry[0].enqueued_at, 100);
        assert_eq!(retry[0].attempts, 3);
        assert_eq!(queue.counts().unwrap(), QueueCounts {
            queued: 1, publishing: 2, held: 1,
            payload_bytes: b"deferredheldclaimedother".len(),
        });
        let other_retry = queue.claim_due(103, 4).unwrap();
        assert_eq!(other_retry.len(), 1);
        assert_eq!(other_retry[0].recipient, other);
    }

    #[test]
    fn total_byte_and_single_record_bounds_include_held_rows() {
        let dir = Directory::new();
        let peer = recipient();
        let mut queue = OutboundQueue::open(dir.database()).unwrap();
        assert!(queue.enqueue(&peer, &[], 100).is_err());
        assert!(queue
            .enqueue(&peer, &vec![0; MAX_OBSERVATION_BYTES + 1], 100)
            .is_err());
        for index in 0..8 {
            let mut payload = vec![0; MAX_OBSERVATION_BYTES];
            payload[0] = index;
            queue.enqueue(&peer, &payload, 100).unwrap();
        }
        let claimed = queue.claim_due(100, 1).unwrap().remove(0);
        queue.hold(claimed.id, "ambiguous_send").unwrap();
        assert_eq!(queue.counts().unwrap().payload_bytes, MAX_QUEUE_BYTES);
        assert!(
            matches!(queue.enqueue(&peer, b"more", 100), Err(e) if e == "outbound_queue_capacity_exceeded")
        );
        drop(queue);
        assert_eq!(
            OutboundQueue::open(dir.database())
                .unwrap()
                .counts()
                .unwrap()
                .held,
            1
        );
    }

    #[test]
    fn row_capacity_is_atomic_and_duplicate_at_capacity_is_harmless() {
        let dir = Directory::new();
        let peer = recipient();
        let mut queue = OutboundQueue::open(dir.database()).unwrap();
        for index in 0..MAX_QUEUE_ROWS as u32 {
            queue.enqueue(&peer, &index.to_be_bytes(), 100).unwrap();
        }
        assert!(!queue.enqueue(&peer, &0_u32.to_be_bytes(), 101).unwrap());
        assert!(
            matches!(queue.enqueue(&peer, b"extra", 100), Err(e) if e == "outbound_queue_capacity_exceeded")
        );
        assert_eq!(queue.counts().unwrap().queued, MAX_QUEUE_ROWS);
    }

    #[test]
    fn invalid_records_fail_closed_on_reopen() {
        for assignment in [
            "digest='incorrect'",
            "recipient='invalid'",
            "enqueued_at=-1",
            "updated_at=99",
            "next_due=99",
            "attempts=17",
            "attempts=16",
            "state='unknown'",
            "state='publishing'",
            "state='held'",
            "payload=X''",
        ] {
            let dir = Directory::new();
            let mut queue = OutboundQueue::open(dir.database()).unwrap();
            queue.enqueue(&recipient(), b"observation", 100).unwrap();
            drop(queue);
            let connection = Connection::open(dir.database()).unwrap();
            connection
                .execute(
                    &format!("UPDATE outbound_observations SET {assignment}"),
                    [],
                )
                .unwrap();
            drop(connection);
            assert!(OutboundQueue::open(dir.database()).is_err(), "{assignment}");
        }
    }

    #[test]
    fn unsupported_schema_is_preserved() {
        let dir = Directory::new();
        let queue = OutboundQueue::open(dir.database()).unwrap();
        drop(queue);
        let connection = Connection::open(dir.database()).unwrap();
        connection.pragma_update(None, "user_version", 99).unwrap();
        drop(connection);
        let original = fs::read(dir.database()).unwrap();
        assert!(
            matches!(OutboundQueue::open(dir.database()), Err(e) if e == "outbound_queue_unsupported_schema")
        );
        assert_eq!(fs::read(dir.database()).unwrap(), original);
    }

    #[test]
    fn unexpected_schema_objects_are_refused_without_running_them() {
        let dir = Directory::new();
        let mut queue = OutboundQueue::open(dir.database()).unwrap();
        queue.enqueue(&recipient(), b"observation", 100).unwrap();
        drop(queue);
        let connection = Connection::open(dir.database()).unwrap();
        connection.execute_batch("CREATE TRIGGER ignore_claim BEFORE UPDATE ON outbound_observations BEGIN SELECT RAISE(IGNORE); END;").unwrap();
        drop(connection);
        let original = fs::read(dir.database()).unwrap();
        assert!(
            matches!(OutboundQueue::open(dir.database()), Err(e) if e == "outbound_queue_unsupported_schema")
        );
        assert_eq!(fs::read(dir.database()).unwrap(), original);
    }

    #[test]
    fn mid_run_payload_peer_attempt_and_time_tampering_cannot_be_claimed() {
        for assignment in [
            "payload=X'6368616e676564'",
            "recipient='invalid'",
            "enqueued_at=-1",
            "enqueued_at=200",
            "updated_at=99",
            "next_due=99",
            "attempts=4294967295",
            "attempts=-1",
            "attempts=16",
            "reason='unexpected'",
        ] {
            let dir = Directory::new();
            let mut queue = OutboundQueue::open(dir.database()).unwrap();
            queue.enqueue(&recipient(), b"observation", 100).unwrap();
            let connection = Connection::open(dir.database()).unwrap();
            connection
                .execute(
                    &format!("UPDATE outbound_observations SET {assignment}"),
                    [],
                )
                .unwrap();
            assert!(queue.claim_due(100, 1).is_err(), "{assignment}");
            let state: String = connection
                .query_row("SELECT state FROM outbound_observations", [], |r| r.get(0))
                .unwrap();
            assert_eq!(state, "queued");
        }
    }

    #[test]
    fn mid_run_schema_tampering_and_clock_rollback_refuse_retry_updates() {
        let dir = Directory::new();
        let recipient = recipient();
        let mut queue = OutboundQueue::open(dir.database()).unwrap();
        queue.enqueue(&recipient, b"observation", 100).unwrap();
        assert!(matches!(queue.claim_due(99, 1), Err(e) if e == "outbound_queue_clock_rollback"));
        let connection = Connection::open(dir.database()).unwrap();
        connection.execute_batch("CREATE TRIGGER reset_claim AFTER UPDATE ON outbound_observations BEGIN UPDATE outbound_observations SET state='queued' WHERE id=NEW.id; END;").unwrap();
        assert!(
            matches!(queue.claim_due(100, 1), Err(e) if e == "outbound_queue_unsupported_schema")
        );
        assert!(matches!(queue.wake_deferred_recipient(&recipient, 100),
            Err(e) if e == "outbound_queue_unsupported_schema"));
        assert_eq!(queue.counts().unwrap().queued, 1);
    }

    #[test]
    fn insecure_paths_symlinks_and_hardlinks_are_refused() {
        let dir = Directory::new();
        let queue = OutboundQueue::open(dir.database()).unwrap();
        drop(queue);
        assert_eq!(
            fs::metadata(dir.database()).unwrap().permissions().mode() & 0o777,
            0o600
        );
        let linked = dir.0.join("linked.sqlite3");
        std::os::unix::fs::symlink(dir.database(), &linked).unwrap();
        assert!(OutboundQueue::open(linked.clone()).is_err());
        fs::remove_file(&linked).unwrap();
        fs::hard_link(dir.database(), &linked).unwrap();
        assert!(OutboundQueue::open(linked.clone()).is_err());
        fs::remove_file(&linked).unwrap();
        fs::set_permissions(dir.database(), fs::Permissions::from_mode(0o644)).unwrap();
        assert!(OutboundQueue::open(dir.database()).is_err());
        fs::set_permissions(dir.database(), fs::Permissions::from_mode(0o600)).unwrap();
        fs::set_permissions(&dir.0, fs::Permissions::from_mode(0o755)).unwrap();
        assert!(OutboundQueue::open(dir.database()).is_err());
        assert!(OutboundQueue::open(PathBuf::from("relative.sqlite3")).is_err());
    }

    fn assert_stale_handle_refuses_operations(queue: &mut OutboundQueue, id: i64) {
        assert!(queue.check_storage().is_err());
        assert!(queue
            .enqueue(&recipient(), b"must-not-append", 101)
            .is_err());
        assert!(queue.claim_due(101, 32).is_err());
        assert!(queue.defer_unsent(id, 101).is_err());
        assert!(queue.hold(id, "must-not-update").is_err());
        assert!(queue.complete(id).is_err());
        assert!(queue.counts().is_err());
    }

    #[test]
    fn replacing_locked_file_cannot_leave_an_old_handle_publishing() {
        let dir = Directory::new();
        let peer = recipient();
        let mut queue = OutboundQueue::open(dir.database()).unwrap();
        queue.enqueue(&peer, b"claimed", 100).unwrap();
        queue.enqueue(&peer, b"queued", 100).unwrap();
        let id = queue.claim_due(100, 1).unwrap().remove(0).id;
        let retired = dir.0.join("retired.sqlite3");
        fs::rename(dir.database(), &retired).unwrap();
        // The old flock follows the retired inode. A replacement at the same
        // path really can be opened by another writer, so namespace checks are
        // required in addition to the lifetime lock.
        let mut replacement = OutboundQueue::open(dir.database()).unwrap();
        replacement.enqueue(&peer, b"replacement", 100).unwrap();
        assert_stale_handle_refuses_operations(&mut queue, id);
        assert_eq!(replacement.counts().unwrap().queued, 1);
        let retired_connection = Connection::open(&retired).unwrap();
        let states: Vec<String> = retired_connection
            .prepare("SELECT state FROM outbound_observations ORDER BY id")
            .unwrap()
            .query_map([], |r| r.get(0))
            .unwrap()
            .collect::<std::result::Result<_, _>>()
            .unwrap();
        assert_eq!(states, ["publishing", "queued"]);
    }

    #[test]
    fn unlinking_locked_file_refuses_every_operation() {
        let dir = Directory::new();
        let mut queue = OutboundQueue::open(dir.database()).unwrap();
        queue.enqueue(&recipient(), b"observation", 100).unwrap();
        let id = queue.claim_due(100, 1).unwrap().remove(0).id;
        fs::remove_file(dir.database()).unwrap();
        assert_stale_handle_refuses_operations(&mut queue, id);
        assert!(!dir.database().exists());
    }

    #[test]
    fn runtime_permission_and_link_changes_refuse_every_operation() {
        for change in ["file_mode", "directory_mode", "symlink", "hardlink"] {
            let dir = Directory::new();
            let mut queue = OutboundQueue::open(dir.database()).unwrap();
            queue.enqueue(&recipient(), b"observation", 100).unwrap();
            let id = queue.claim_due(100, 1).unwrap().remove(0).id;
            match change {
                "file_mode" => {
                    fs::set_permissions(dir.database(), fs::Permissions::from_mode(0o644)).unwrap()
                }
                "directory_mode" => {
                    fs::set_permissions(&dir.0, fs::Permissions::from_mode(0o755)).unwrap()
                }
                "symlink" => {
                    let retired = dir.0.join("retired.sqlite3");
                    fs::rename(dir.database(), &retired).unwrap();
                    std::os::unix::fs::symlink(retired, dir.database()).unwrap();
                }
                "hardlink" => fs::hard_link(dir.database(), dir.0.join("linked.sqlite3")).unwrap(),
                _ => unreachable!(),
            }
            assert_stale_handle_refuses_operations(&mut queue, id);
        }
    }

    #[test]
    fn peer_time_and_reason_inputs_are_bounded() {
        let dir = Directory::new();
        let mut queue = OutboundQueue::open(dir.database()).unwrap();
        for invalid in ["", "not-a-peer", " 12D3KooW"] {
            assert!(queue.enqueue(invalid, b"x", 100).is_err());
        }
        let peer = recipient();
        for invalid in [0, u64::MAX] {
            assert!(queue.enqueue(&peer, b"x", invalid).is_err());
        }
        queue.enqueue(&peer, b"x", 100).unwrap();
        let claimed = queue.claim_due(100, 1).unwrap().remove(0);
        for invalid in ["", "control\n", "nonasciié"] {
            assert!(queue.hold(claimed.id, invalid).is_err());
        }
    }
}
