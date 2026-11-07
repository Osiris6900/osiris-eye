//! Durable spool and Linux peer-credential delivery for verified receipt events.
//!
//! `enqueue` must run before in-memory pending state is consumed. A row stays
//! unacked until the connected peer's kernel credentials match the supervisor-
//! pinned listener PID and this process UID, and that peer returns `applied`
//! or `duplicate`. Any other outcome leaves the row in place.

use crate::delivery_receipt::{VerifiedReceiptEvent, MAX_EVENT_FRAME_BYTES, VERIFIED_ACK_SCHEMA};
use rusqlite::{params, Connection, OptionalExtension};
use serde::Deserialize;
use std::{
    fs,
    io::{Read, Write},
    os::unix::{
        fs::{FileTypeExt, MetadataExt, PermissionsExt},
        io::AsRawFd,
        net::UnixStream,
    },
    path::{Path, PathBuf},
    time::Duration,
};

#[repr(C)]
struct Ucred {
    pid: i32,
    uid: u32,
    gid: u32,
}

const SOL_SOCKET: i32 = 1;
const SO_PEERCRED: i32 = 17;

extern "C" {
    fn getsockopt(sockfd: i32, level: i32, optname: i32, optval: *mut u8, optlen: *mut u32) -> i32;
    fn geteuid() -> u32;
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ReceiptAck {
    schema: String,
    event_id: String,
    disposition: String,
}

pub struct VerifiedReceiptBridge {
    connection: Connection,
    socket_path: PathBuf,
    listener_pid: i32,
}

impl VerifiedReceiptBridge {
    pub fn open(socket_path: &Path, spool_path: &Path, listener_pid: i32) -> Result<Self, String> {
        if listener_pid <= 0 {
            return Err("invalid_receipt_listener_pid".into());
        }
        validate_absolute(socket_path)?;
        validate_absolute(spool_path)?;
        if socket_path == spool_path {
            return Err("receipt_socket_and_spool_must_differ".into());
        }
        let socket_dir = socket_path
            .parent()
            .ok_or("receipt_socket_directory_required")?;
        let spool_dir = spool_path
            .parent()
            .filter(|path| !path.as_os_str().is_empty())
            .ok_or("receipt_spool_directory_required")?;
        require_private_directory(socket_dir)?;
        require_private_directory(spool_dir)?;
        reject_symlink(socket_path)?;
        Ok(Self {
            connection: open_spool(spool_path)?,
            socket_path: socket_path.to_path_buf(),
            listener_pid,
        })
    }

    /// `None` keeps the historical log-only receipt path. A partial configuration
    /// is refused so a missing PID pin cannot silently downgrade to unauthenticated delivery.
    pub fn open_configured(
        receipts_enabled: bool,
        socket: Option<&str>,
        spool: Option<&str>,
        listener_pid: Option<&str>,
    ) -> Result<Option<Self>, String> {
        match (socket, spool, listener_pid) {
            (None, None, None) => Ok(None),
            (Some(socket), Some(spool), Some(pid)) => {
                if !receipts_enabled {
                    return Err(
                        "verified receipt bridge requires OSIRIS_DELIVERY_RECEIPTS=1".into(),
                    );
                }
                if !valid_config_token(socket)
                    || !valid_config_token(spool)
                    || !valid_pid_token(pid)
                {
                    return Err("invalid_receipt_bridge_configuration".into());
                }
                let pid = pid
                    .parse::<i32>()
                    .map_err(|_| "invalid_receipt_listener_pid".to_string())?;
                Ok(Some(Self::open(Path::new(socket), Path::new(spool), pid)?))
            }
            _ => Err("receipt bridge settings must be set together".into()),
        }
    }

    pub fn enqueue(&mut self, event: &VerifiedReceiptEvent) -> Result<(), String> {
        let json = canonical_json(event)?;
        let tx = self
            .connection
            .unchecked_transaction()
            .map_err(|_| "receipt_spool_transaction_failed".to_string())?;
        let existing: Option<String> = tx
            .query_row(
                "SELECT event_json FROM verified_receipt_outbox WHERE event_id=?1",
                [&event.event_id],
                |row| row.get(0),
            )
            .optional()
            .map_err(|_| "receipt_spool_read_failed".to_string())?;
        match existing {
            Some(existing) if existing == json => {
                tx.commit()
                    .map_err(|_| "receipt_spool_commit_failed".to_string())?;
                Ok(())
            }
            Some(_) => Err("receipt_event_id_conflict".into()),
            None => {
                let created_at = i64::try_from(event.accepted_at)
                    .map_err(|_| "verified_event_rejected".to_string())?;
                tx.execute(
                    "INSERT INTO verified_receipt_outbox
                        (event_id, event_json, acked, created_at) VALUES (?1, ?2, 0, ?3)",
                    params![event.event_id, json, created_at],
                )
                .map_err(|_| "receipt_spool_insert_failed".to_string())?;
                tx.commit()
                    .map_err(|_| "receipt_spool_commit_failed".to_string())?;
                Ok(())
            }
        }
    }

    pub fn transmit(
        &mut self,
        event: &VerifiedReceiptEvent,
        timeout: Duration,
    ) -> Result<String, String> {
        if timeout.is_zero() || timeout > Duration::from_secs(30) {
            return Err("invalid_receipt_io_timeout".into());
        }
        let json = canonical_json(event)?;
        let stored: Option<String> = self
            .connection
            .query_row(
                "SELECT event_json FROM verified_receipt_outbox WHERE event_id=?1",
                [&event.event_id],
                |row| row.get(0),
            )
            .optional()
            .map_err(|_| "receipt_spool_read_failed".to_string())?;
        if stored.as_deref() != Some(json.as_str()) {
            return Err("receipt_spool_event_mismatch".into());
        }
        let disposition = exchange(
            &self.socket_path,
            self.listener_pid,
            json.as_bytes(),
            &event.event_id,
            timeout,
        )?;
        self.mark_acked(&event.event_id, &disposition)?;
        Ok(disposition)
    }

    pub fn pending_events(&self, limit: usize) -> Result<Vec<VerifiedReceiptEvent>, String> {
        if !(1..=32).contains(&limit) {
            return Err("invalid_flush_limit".into());
        }
        let mut statement = self
            .connection
            .prepare(
                "SELECT event_json FROM verified_receipt_outbox
             WHERE acked=0 ORDER BY created_at, event_id LIMIT ?1",
            )
            .map_err(|_| "receipt_spool_read_failed".to_string())?;
        let rows = statement
            .query_map(params![i64::try_from(limit).unwrap_or(i64::MAX)], |row| {
                row.get::<_, String>(0)
            })
            .map_err(|_| "receipt_spool_read_failed".to_string())?;
        let mut events = Vec::new();
        for row in rows {
            let json = row.map_err(|_| "receipt_spool_read_failed".to_string())?;
            let event: VerifiedReceiptEvent =
                serde_json::from_str(&json).map_err(|_| "receipt_spool_corrupt".to_string())?;
            if canonical_json(&event)? != json {
                return Err("receipt_spool_corrupt".into());
            }
            events.push(event);
        }
        Ok(events)
    }

    pub fn unacked_count(&self) -> Result<i64, String> {
        self.connection
            .query_row(
                "SELECT COUNT(*) FROM verified_receipt_outbox WHERE acked=0",
                [],
                |row| row.get(0),
            )
            .map_err(|_| "receipt_spool_read_failed".to_string())
    }

    fn mark_acked(&mut self, event_id: &str, disposition: &str) -> Result<(), String> {
        if disposition != "applied" && disposition != "duplicate" {
            return Err("ack_not_durable".into());
        }
        let tx = self
            .connection
            .unchecked_transaction()
            .map_err(|_| "receipt_spool_transaction_failed".to_string())?;
        let updated = tx
            .execute(
                "UPDATE verified_receipt_outbox SET acked=1, disposition=?1
             WHERE event_id=?2 AND acked=0",
                params![disposition, event_id],
            )
            .map_err(|_| "receipt_spool_update_failed".to_string())?;
        if updated == 0 {
            let acked: Option<i64> = tx
                .query_row(
                    "SELECT acked FROM verified_receipt_outbox WHERE event_id=?1",
                    [event_id],
                    |row| row.get(0),
                )
                .optional()
                .map_err(|_| "receipt_spool_read_failed".to_string())?;
            if acked != Some(1) {
                return Err("receipt_spool_missing".into());
            }
        }
        tx.commit()
            .map_err(|_| "receipt_spool_commit_failed".to_string())?;
        Ok(())
    }
}

fn valid_config_token(value: &str) -> bool {
    !value.is_empty() && value == value.trim() && !value.contains('\0')
}

fn valid_pid_token(value: &str) -> bool {
    !value.is_empty() && value.bytes().all(|byte| byte.is_ascii_digit()) && value != "0"
}

fn current_uid() -> u32 {
    // SAFETY: geteuid has no preconditions and does not dereference caller memory.
    unsafe { geteuid() }
}

fn validate_absolute(path: &Path) -> Result<(), String> {
    if !path.is_absolute() {
        return Err("receipt_path_not_absolute".into());
    }
    if path
        .components()
        .any(|component| matches!(component, std::path::Component::ParentDir))
    {
        return Err("receipt_path_invalid".into());
    }
    Ok(())
}

fn require_private_directory(path: &Path) -> Result<(), String> {
    let metadata =
        fs::symlink_metadata(path).map_err(|_| "receipt_directory_unavailable".to_string())?;
    if metadata.file_type().is_symlink() || !metadata.is_dir() {
        return Err("receipt_directory_invalid".into());
    }
    if metadata.permissions().mode() & 0o777 != 0o700 || metadata.uid() != current_uid() {
        return Err("receipt_directory_permissions".into());
    }
    Ok(())
}

fn reject_symlink(path: &Path) -> Result<(), String> {
    if let Ok(metadata) = fs::symlink_metadata(path) {
        if metadata.file_type().is_symlink() {
            return Err("receipt_path_symlink".into());
        }
    }
    Ok(())
}

fn open_spool(path: &Path) -> Result<Connection, String> {
    reject_symlink(path)?;
    if let Ok(metadata) = fs::symlink_metadata(path) {
        if !metadata.is_file()
            || metadata.uid() != current_uid()
            || metadata.permissions().mode() & 0o077 != 0
        {
            return Err("receipt_spool_permissions".into());
        }
    }
    let connection = Connection::open(path).map_err(|_| "receipt_spool_open_failed".to_string())?;
    connection
        .busy_timeout(Duration::from_secs(5))
        .map_err(|_| "receipt_spool_pragma_failed".to_string())?;
    connection
        .pragma_update(None, "journal_mode", "WAL")
        .map_err(|_| "receipt_spool_pragma_failed".to_string())?;
    connection
        .pragma_update(None, "synchronous", "FULL")
        .map_err(|_| "receipt_spool_pragma_failed".to_string())?;
    connection
        .execute_batch(
            "CREATE TABLE IF NOT EXISTS verified_receipt_outbox (
            event_id TEXT PRIMARY KEY NOT NULL,
            event_json TEXT NOT NULL,
            acked INTEGER NOT NULL DEFAULT 0 CHECK(acked IN (0, 1)),
            disposition TEXT,
            created_at INTEGER NOT NULL
        );",
        )
        .map_err(|_| "receipt_spool_schema_failed".to_string())?;
    fs::set_permissions(path, fs::Permissions::from_mode(0o600))
        .map_err(|_| "receipt_spool_permissions".to_string())?;
    Ok(connection)
}

fn canonical_json(event: &VerifiedReceiptEvent) -> Result<String, String> {
    if !event_is_verifier_shaped(event) {
        return Err("verified_event_rejected".into());
    }
    serde_json::to_string(event)
        .map_err(|_| "receipt_event_encode_failed".to_string())
        .and_then(|json| {
            if json.len() > MAX_EVENT_FRAME_BYTES {
                Err("receipt_event_too_large".into())
            } else {
                Ok(json)
            }
        })
}

fn event_is_verifier_shaped(event: &VerifiedReceiptEvent) -> bool {
    event.matches_verifier_binding()
        && lowercase_hex(&event.envelope_sha256, 64)
        && event.message_id.len() == 22
        && peer_id_ok(&event.sender_peer_id)
        && peer_id_ok(&event.recipient_peer_id)
}

fn lowercase_hex(value: &str, len: usize) -> bool {
    value.len() == len
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn peer_id_ok(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 128
        && value
            .parse::<libp2p::PeerId>()
            .map(|peer| peer.to_string() == value)
            .unwrap_or(false)
}

fn exchange(
    path: &Path,
    listener_pid: i32,
    frame: &[u8],
    event_id: &str,
    timeout: Duration,
) -> Result<String, String> {
    if frame.is_empty() || frame.len() > MAX_EVENT_FRAME_BYTES {
        return Err("invalid_receipt_frame_size".into());
    }
    ensure_socket_file(path)?;
    let mut stream = UnixStream::connect(path)
        .map_err(|error| format!("receipt_channel_unavailable:{:?}", error.kind()))?;
    stream
        .set_read_timeout(Some(timeout))
        .map_err(|_| "receipt_channel_timeout".to_string())?;
    stream
        .set_write_timeout(Some(timeout))
        .map_err(|_| "receipt_channel_timeout".to_string())?;
    let (pid, uid) = peer_credentials(&stream)?;
    if pid != listener_pid || uid != current_uid() {
        return Err("receipt_listener_peer_mismatch".into());
    }
    let length =
        u32::try_from(frame.len()).map_err(|_| "invalid_receipt_frame_size".to_string())?;
    stream
        .write_all(&length.to_be_bytes())
        .map_err(|error| format!("receipt_channel_write_failed:{:?}", error.kind()))?;
    stream
        .write_all(frame)
        .map_err(|error| format!("receipt_channel_write_failed:{:?}", error.kind()))?;
    let mut length_buf = [0u8; 4];
    stream
        .read_exact(&mut length_buf)
        .map_err(|error| format!("receipt_channel_read_failed:{:?}", error.kind()))?;
    let ack_len = usize::try_from(u32::from_be_bytes(length_buf)).unwrap_or(usize::MAX);
    if ack_len == 0 || ack_len > MAX_EVENT_FRAME_BYTES {
        return Err("invalid_ack_frame".into());
    }
    let mut body = vec![0u8; ack_len];
    stream
        .read_exact(&mut body)
        .map_err(|error| format!("receipt_channel_read_failed:{:?}", error.kind()))?;
    let ack: ReceiptAck =
        serde_json::from_slice(&body).map_err(|_| "invalid_ack_json".to_string())?;
    if ack.schema != VERIFIED_ACK_SCHEMA || ack.event_id != event_id {
        return Err("ack_event_mismatch".into());
    }
    if ack.disposition != "applied" && ack.disposition != "duplicate" {
        return Err("ack_not_durable".into());
    }
    Ok(ack.disposition)
}

fn ensure_socket_file(path: &Path) -> Result<(), String> {
    let metadata = fs::symlink_metadata(path)
        .map_err(|error| format!("receipt_channel_unavailable:{:?}", error.kind()))?;
    if metadata.file_type().is_symlink() || !metadata.file_type().is_socket() {
        return Err("receipt_socket_invalid".into());
    }
    if metadata.uid() != current_uid() || metadata.permissions().mode() & 0o077 != 0 {
        return Err("receipt_socket_permissions".into());
    }
    Ok(())
}

fn peer_credentials(stream: &UnixStream) -> Result<(i32, u32), String> {
    let mut creds = Ucred {
        pid: 0,
        uid: 0,
        gid: 0,
    };
    let mut len = std::mem::size_of::<Ucred>() as u32;
    // SAFETY: getsockopt writes at most `len` bytes into `creds`, which is a live
    // ucred of that size. The file descriptor is an open AF_UNIX stream.
    let rc = unsafe {
        getsockopt(
            stream.as_raw_fd(),
            SOL_SOCKET,
            SO_PEERCRED,
            &mut creds as *mut Ucred as *mut u8,
            &mut len,
        )
    };
    if rc != 0 || len != std::mem::size_of::<Ucred>() as u32 || creds.pid <= 0 {
        return Err("peer_credentials_unavailable".into());
    }
    Ok((creds.pid, creds.uid))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::delivery_receipt::{
        create_receipt, AdmissionPolicy, PendingReceipts, VERIFIED_ACK_SCHEMA,
        VERIFIED_EVENT_PROVENANCE,
    };
    use base64::{engine::general_purpose::STANDARD_NO_PAD, Engine};
    use libp2p::{identity, PeerId};
    use std::{io::ErrorKind, process, thread, time::Instant};

    fn private_dir(label: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "osiris-receipt-{label}-{}-{}",
            process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos(),
        ));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir(&dir).unwrap();
        fs::set_permissions(&dir, fs::Permissions::from_mode(0o700)).unwrap();
        dir
    }

    fn sample_event() -> VerifiedReceiptEvent {
        let key = identity::Keypair::generate_ed25519();
        let recipient = PeerId::from_public_key(&key.public());
        let sender = PeerId::random();
        let message_id = STANDARD_NO_PAD.encode([7u8; 16]);
        let digest = "ab".repeat(32);
        let receipt = create_receipt(&key, &sender, &message_id, &digest, 1_000).unwrap();
        let mut pending = PendingReceipts::new();
        pending
            .track(recipient, message_id, digest, Instant::now())
            .unwrap();
        let event = pending
            .verify(
                &receipt,
                &recipient,
                &sender,
                &key.public(),
                1_000,
                Instant::now(),
                AdmissionPolicy::allow_local(),
            )
            .unwrap();
        assert_eq!(event.provenance, VERIFIED_EVENT_PROVENANCE);
        event
    }

    fn bridge_in(dir: &Path, listener_pid: i32) -> VerifiedReceiptBridge {
        VerifiedReceiptBridge::open(
            &dir.join("receipt.sock"),
            &dir.join("spool.sqlite3"),
            listener_pid,
        )
        .unwrap()
    }

    #[test]
    fn partial_configuration_and_loose_directory_fail_closed() {
        assert!(
            VerifiedReceiptBridge::open_configured(true, None, None, None)
                .unwrap()
                .is_none()
        );
        assert!(
            VerifiedReceiptBridge::open_configured(true, Some("/tmp/a.sock"), None, None).is_err()
        );
        assert!(VerifiedReceiptBridge::open_configured(
            false,
            Some("/tmp/a.sock"),
            Some("/tmp/a.sqlite3"),
            Some("1")
        )
        .is_err());
        assert!(VerifiedReceiptBridge::open_configured(
            true,
            Some("relative.sock"),
            Some("/tmp/a.sqlite3"),
            Some("1")
        )
        .is_err());
        assert!(VerifiedReceiptBridge::open_configured(
            true,
            Some("/tmp/a.sock"),
            Some("/tmp/a.sqlite3"),
            Some("0")
        )
        .is_err());
        assert!(VerifiedReceiptBridge::open_configured(
            true,
            Some("/tmp/a.sock"),
            Some("/tmp/a.sqlite3"),
            Some(" 4")
        )
        .is_err());
        let loose = private_dir("loose");
        fs::set_permissions(&loose, fs::Permissions::from_mode(0o755)).unwrap();
        let error = VerifiedReceiptBridge::open(
            &loose.join("receipt.sock"),
            &loose.join("spool.sqlite3"),
            1,
        );
        assert_eq!(
            error.err().as_deref(),
            Some("receipt_directory_permissions")
        );
        let _ = fs::remove_dir_all(loose);
    }

    #[test]
    fn enqueue_is_durable_and_rollback_keeps_the_previous_commit() {
        let dir = private_dir("spool");
        let event = sample_event();
        let mut bridge = bridge_in(&dir, 1);
        bridge.enqueue(&event).unwrap();
        bridge.enqueue(&event).unwrap();
        assert_eq!(bridge.unacked_count().unwrap(), 1);
        drop(bridge);
        let reopened = bridge_in(&dir, 1);
        assert_eq!(reopened.unacked_count().unwrap(), 1);
        assert_eq!(reopened.pending_events(8).unwrap(), vec![event.clone()]);
        drop(reopened);

        let connection = Connection::open(dir.join("spool.sqlite3")).unwrap();
        let tx = connection.unchecked_transaction().unwrap();
        tx.execute(
            "INSERT INTO verified_receipt_outbox (event_id, event_json, acked, created_at) VALUES ('rolled-back', '{}', 0, 1)",
            [],
        ).unwrap();
        drop(tx);
        drop(connection);
        let bridge = bridge_in(&dir, 1);
        assert_eq!(bridge.unacked_count().unwrap(), 1);
        let conflict = Connection::open(dir.join("spool.sqlite3")).unwrap();
        conflict
            .execute(
                "UPDATE verified_receipt_outbox SET event_json='{}' WHERE event_id=?1",
                [&event.event_id],
            )
            .unwrap();
        drop(conflict);
        let mut bridge = bridge_in(&dir, 1);
        assert_eq!(
            bridge.enqueue(&event).unwrap_err(),
            "receipt_event_id_conflict"
        );
        let _ = fs::remove_dir_all(dir);
    }

    #[test]
    fn transmit_requires_pinned_listener_and_keeps_unacked_rows_on_failure() {
        let dir = private_dir("channel");
        let event = sample_event();
        let socket = dir.join("receipt.sock");
        let mut bridge = bridge_in(&dir, i32::try_from(process::id()).unwrap());
        bridge.enqueue(&event).unwrap();
        let missing = bridge.transmit(&event, Duration::from_secs(1)).unwrap_err();
        assert!(
            missing.starts_with("receipt_channel_unavailable"),
            "{missing}"
        );
        assert_eq!(bridge.unacked_count().unwrap(), 1);

        let listener = std::os::unix::net::UnixListener::bind(&socket).unwrap();
        fs::set_permissions(&socket, fs::Permissions::from_mode(0o600)).unwrap();
        let wrong_pid = i32::try_from(process::id()).unwrap().saturating_add(1);
        let mut wrong =
            VerifiedReceiptBridge::open(&socket, &dir.join("spool.sqlite3"), wrong_pid).unwrap();
        let body = thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            stream
                .set_read_timeout(Some(Duration::from_secs(1)))
                .unwrap();
            let mut buf = [0u8; 4];
            stream.read_exact(&mut buf).err().map(|error| error.kind())
        });
        let mismatch = wrong.transmit(&event, Duration::from_secs(1)).unwrap_err();
        assert_eq!(mismatch, "receipt_listener_peer_mismatch");
        assert_eq!(body.join().unwrap(), Some(ErrorKind::UnexpectedEof));
        assert_eq!(wrong.unacked_count().unwrap(), 1);
        drop(wrong);

        let _ = fs::remove_file(&socket);
        let listener = std::os::unix::net::UnixListener::bind(&socket).unwrap();
        fs::set_permissions(&socket, fs::Permissions::from_mode(0o600)).unwrap();
        let server = thread::spawn(move || {
            for disposition in ["applied", "duplicate", "rejected"] {
                let (mut stream, _) = listener.accept().unwrap();
                let mut len_buf = [0u8; 4];
                stream.read_exact(&mut len_buf).unwrap();
                let len = u32::from_be_bytes(len_buf) as usize;
                let mut body = vec![0u8; len];
                stream.read_exact(&mut body).unwrap();
                let parsed: serde_json::Value = serde_json::from_slice(&body).unwrap();
                let ack = format!(
                    "{{\"disposition\":\"{disposition}\",\"event_id\":\"{}\",\"schema\":\"{VERIFIED_ACK_SCHEMA}\"}}",
                    parsed["event_id"].as_str().unwrap(),
                );
                let bytes = ack.into_bytes();
                stream
                    .write_all(&(bytes.len() as u32).to_be_bytes())
                    .unwrap();
                stream.write_all(&bytes).unwrap();
            }
        });
        let mut bridge = bridge_in(&dir, i32::try_from(process::id()).unwrap());
        assert_eq!(
            bridge.transmit(&event, Duration::from_secs(2)).unwrap(),
            "applied"
        );
        assert_eq!(bridge.unacked_count().unwrap(), 0);
        assert_eq!(
            bridge.transmit(&event, Duration::from_secs(2)).unwrap(),
            "duplicate"
        );
        assert_eq!(bridge.unacked_count().unwrap(), 0);
        // Redeliver once more into the rejected response without marking a new ack.
        let connection = Connection::open(dir.join("spool.sqlite3")).unwrap();
        connection
            .execute(
                "UPDATE verified_receipt_outbox SET acked=0, disposition=NULL",
                [],
            )
            .unwrap();
        drop(connection);
        let mut bridge = bridge_in(&dir, i32::try_from(process::id()).unwrap());
        assert_eq!(
            bridge.transmit(&event, Duration::from_secs(2)).unwrap_err(),
            "ack_not_durable"
        );
        assert_eq!(bridge.unacked_count().unwrap(), 1);
        server.join().unwrap();
        let _ = fs::remove_dir_all(dir);
    }
}
