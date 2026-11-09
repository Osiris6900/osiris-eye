//! Durable cache of recently discovered peers for restart-proof rejoin.
//!
//! Discovery data only: entries are addresses the network once claimed. Trust
//! is never inherited from this cache — every rejoin dial re-passes the live
//! trusted/revoked policy in the caller, and revoked peers are purged at
//! startup. Corrupt rows fail closed at open; delete the file to recover.

use libp2p::Multiaddr;
use rusqlite::{params, Connection, OptionalExtension, TransactionBehavior};
use std::{collections::HashSet, error::Error, path::{Path, PathBuf}};

pub const MAX_CACHED_PEERS: i64 = 4096;
const MAX_PEER_ID_BYTES: usize = 128;
const MAX_ADDRESSES_PER_PEER: usize = 16;
const MAX_ADDRESS_BYTES: usize = 512;
const MAX_SOURCE_BYTES: usize = 32;
const SCHEMA_VERSION: i64 = 1;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CachedPeer {
    pub peer_id: String,
    pub addresses: Vec<String>,
    pub last_seen: u64,
    pub source: String,
}

pub struct PeerCache {
    connection: Connection,
    path: PathBuf,
}

impl PeerCache {
    pub fn open(path: PathBuf) -> Result<Self, Box<dyn Error>> {
        if let Some(parent) = path.parent().filter(|p| !p.as_os_str().is_empty()) {
            std::fs::create_dir_all(parent)?;
        }
        let connection = Connection::open(&path)?;
        let version: i64 = connection.pragma_query_value(None, "user_version", |row| row.get(0))?;
        if version != 0 && version != SCHEMA_VERSION {
            return Err("unsupported peer cache schema version".into());
        }
        connection.busy_timeout(std::time::Duration::from_secs(5))?;
        connection.pragma_update(None, "journal_mode", "WAL")?;
        connection.pragma_update(None, "synchronous", "FULL")?;
        connection.execute_batch(
            "CREATE TABLE IF NOT EXISTS peer_cache (
                peer_id TEXT PRIMARY KEY NOT NULL,
                addresses TEXT NOT NULL,
                first_seen INTEGER NOT NULL,
                last_seen INTEGER NOT NULL,
                source TEXT NOT NULL
            );
             PRAGMA user_version = 1;"
        )?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600))?;
        }
        let cache = Self { connection, path };
        cache.validate_stored_rows()?;
        Ok(cache)
    }

    pub fn path(&self) -> &Path { &self.path }

    pub fn len(&self) -> Result<usize, Box<dyn Error>> {
        let count: i64 = self.connection.query_row(
            "SELECT COUNT(*) FROM peer_cache", [], |row| row.get(0)
        )?;
        Ok(count as usize)
    }

    /// Merge freshly observed addresses into a peer's entry. Newest addresses
    /// come first; the entry is bounded and the whole cache evicts the least
    /// recently seen peers once capacity is reached.
    pub fn remember(
        &mut self,
        peer_id: &str,
        addresses: &[String],
        source: &str,
        now: u64,
    ) -> Result<(), Box<dyn Error>> {
        if !valid_peer_id(peer_id) {
            return Err("invalid cached peer id".into());
        }
        if source.is_empty() || source.len() > MAX_SOURCE_BYTES
            || !source.bytes().all(|byte| byte.is_ascii_alphanumeric()) {
            return Err("invalid peer cache source".into());
        }
        let fresh = validate_addresses(addresses)?;
        let last_seen = i64::try_from(now)
            .map_err(|_| "peer cache timestamp exceeds SQLite signed integer range")?;

        let transaction = self.connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let existing: Option<(String, i64)> = transaction.query_row(
            "SELECT addresses, first_seen FROM peer_cache WHERE peer_id = ?1",
            params![peer_id], |row| Ok((row.get(0)?, row.get(1)?))
        ).optional()?;

        let mut merged: Vec<String> = Vec::new();
        let mut known = HashSet::new();
        for address in fresh.iter().cloned() {
            if known.insert(address.clone()) { merged.push(address); }
        }
        if let Some((stored, _)) = &existing {
            for address in decode_addresses(stored)? {
                if known.insert(address.clone()) { merged.push(address); }
            }
        }
        merged.truncate(MAX_ADDRESSES_PER_PEER);
        if merged.is_empty() {
            return Err("peer cache entry needs at least one valid address".into());
        }
        let encoded = merged.join("\n");
        let first_seen = existing.map(|(_, first_seen)| first_seen).unwrap_or(last_seen);
        transaction.execute(
            "INSERT INTO peer_cache(peer_id, addresses, first_seen, last_seen, source)
             VALUES (?1, ?2, ?3, ?4, ?5)
             ON CONFLICT(peer_id) DO UPDATE SET
                addresses = excluded.addresses,
                last_seen = excluded.last_seen,
                source = excluded.source",
            params![peer_id, encoded, first_seen, last_seen, source]
        )?;
        let count: i64 = transaction.query_row(
            "SELECT COUNT(*) FROM peer_cache", [], |row| row.get(0)
        )?;
        if count > MAX_CACHED_PEERS {
            transaction.execute(
                "DELETE FROM peer_cache WHERE peer_id IN (
                    SELECT peer_id FROM peer_cache
                    WHERE peer_id != ?1
                    ORDER BY last_seen ASC
                    LIMIT ?2
                )",
                params![peer_id, count - MAX_CACHED_PEERS]
            )?;
        }
        transaction.commit()?;
        Ok(())
    }

    /// Newest-first page of peers seen within `max_age_secs` of `now`.
    /// Individually undecodable rows are skipped: discovery data must degrade,
    /// never block a restart.
    pub fn load_recent(
        &self,
        max_peers: usize,
        max_age_secs: u64,
        now: u64,
    ) -> Result<Vec<CachedPeer>, Box<dyn Error>> {
        let cutoff = i64::try_from(now.saturating_sub(max_age_secs)).unwrap_or(i64::MAX);
        let limit = i64::try_from(max_peers.clamp(1, MAX_CACHED_PEERS as usize)).unwrap_or(MAX_CACHED_PEERS);
        let mut statement = self.connection.prepare(
            "SELECT peer_id, addresses, last_seen, source FROM peer_cache
             WHERE last_seen >= ?1
             ORDER BY last_seen DESC
             LIMIT ?2"
        )?;
        let mut rows = statement.query(params![cutoff, limit])?;
        let mut peers = Vec::new();
        while let Some(row) = rows.next()? {
            let peer_id: String = row.get(0)?;
            let addresses: String = row.get(1)?;
            let last_seen: i64 = row.get(2)?;
            let source: String = row.get(3)?;
            let Ok(addresses) = decode_addresses(&addresses) else { continue; };
            if addresses.is_empty() || !valid_peer_id(&peer_id) { continue; }
            let Ok(last_seen) = u64::try_from(last_seen) else { continue; };
            peers.push(CachedPeer { peer_id, addresses, last_seen, source });
        }
        Ok(peers)
    }

    /// Drop a peer from future rejoin plans (e.g. after revocation).
    pub fn remove(&self, peer_id: &str) -> Result<(), Box<dyn Error>> {
        self.connection.execute("DELETE FROM peer_cache WHERE peer_id = ?1", params![peer_id])?;
        Ok(())
    }

    /// A poisoned cache must fail closed at startup, never silently downgrade
    /// to an empty discovery set while looking healthy.
    fn validate_stored_rows(&self) -> Result<(), Box<dyn Error>> {
        let count: i64 = self.connection.query_row(
            "SELECT COUNT(*) FROM peer_cache", [], |row| row.get(0)
        )?;
        if count > MAX_CACHED_PEERS {
            return Err("peer cache exceeds capacity".into());
        }
        let mut statement = self.connection.prepare(
            "SELECT peer_id, addresses, first_seen, last_seen, source FROM peer_cache"
        )?;
        let mut rows = statement.query([])?;
        while let Some(row) = rows.next()? {
            let peer_id: String = row.get(0)?;
            let addresses: String = row.get(1)?;
            let first_seen: i64 = row.get(2)?;
            let last_seen: i64 = row.get(3)?;
            let source: String = row.get(4)?;
            if !valid_peer_id(&peer_id) || first_seen < 0 || last_seen < 0 || first_seen > last_seen
                || source.is_empty() || source.len() > MAX_SOURCE_BYTES {
                return Err("corrupt peer cache row; refusing to start".into());
            }
            let decoded = decode_addresses(&addresses)?;
            if decoded.is_empty() || decoded.len() > MAX_ADDRESSES_PER_PEER {
                return Err("corrupt peer cache row; refusing to start".into());
            }
        }
        Ok(())
    }
}

fn valid_peer_id(peer_id: &str) -> bool {
    !peer_id.is_empty() && peer_id.len() <= MAX_PEER_ID_BYTES
        && peer_id.bytes().all(|byte| byte.is_ascii_alphanumeric())
}

fn validate_addresses(addresses: &[String]) -> Result<Vec<String>, Box<dyn Error>> {
    let mut valid = Vec::new();
    let mut known = HashSet::new();
    for address in addresses {
        if address.len() > MAX_ADDRESS_BYTES || address.parse::<Multiaddr>().is_err() {
            return Err("invalid cached peer address".into());
        }
        if known.insert(address.clone()) {
            valid.push(address.clone());
        }
        if valid.len() >= MAX_ADDRESSES_PER_PEER { break; }
    }
    Ok(valid)
}

fn decode_addresses(encoded: &str) -> Result<Vec<String>, Box<dyn Error>> {
    let addresses: Vec<String> = encoded.split('\n').map(str::to_owned).collect();
    Ok(validate_addresses(&addresses)?)
}

#[cfg(test)]
mod tests {
    use super::{PeerCache, MAX_CACHED_PEERS};
    use std::{path::PathBuf, sync::atomic::{AtomicU64, Ordering}};

    static NEXT: AtomicU64 = AtomicU64::new(0);
    fn temp_path(label: &str) -> PathBuf {
        std::env::temp_dir().join(format!(
            "osiris-peer-cache-{label}-{}-{}.sqlite3",
            std::process::id(), NEXT.fetch_add(1, Ordering::Relaxed)
        ))
    }
    fn cleanup(path: &PathBuf) {
        let _ = std::fs::remove_file(path);
        let _ = std::fs::remove_file(path.with_extension("sqlite3-wal"));
        let _ = std::fs::remove_file(path.with_extension("sqlite3-shm"));
    }
    fn peer_id(tag: u8) -> String {
        format!("12D3KooWTest{:0>32}", format!("{tag:x}"))
    }
    fn addr(port: u16) -> String {
        format!("/ip4/127.0.0.1/tcp/{port}")
    }

    #[test]
    fn bundled_sqlite_meets_cve_2026_119375_fix_line() {
        let connection = rusqlite::Connection::open_in_memory().unwrap();
        let version: String = connection
            .query_row("SELECT sqlite_version()", [], |row| row.get(0))
            .unwrap();
        let mut parts = version.split('.');
        let major: u32 = parts.next().unwrap().parse().unwrap();
        let minor: u32 = parts.next().unwrap().parse().unwrap();
        assert!(
            (major, minor) >= (3, 54),
            "bundled SQLite {version} is older than 3.54.0 (RUSTSEC-2026-0459 / CVE-2026-119375)"
        );
    }

    #[test]
    fn cache_remembers_and_reloads_across_restart() {
        let path = temp_path("restart");
        {
            let mut cache = PeerCache::open(path.clone()).unwrap();
            cache.remember(&peer_id(1), &[addr(4001), addr(4002)], "identify", 100).unwrap();
        }
        {
            let cache = PeerCache::open(path.clone()).unwrap();
            let peers = cache.load_recent(10, 1000, 200).unwrap();
            assert_eq!(peers.len(), 1);
            assert_eq!(peers[0].peer_id, peer_id(1));
            assert_eq!(peers[0].addresses, vec![addr(4001), addr(4002)]);
            assert_eq!(peers[0].source, "identify");
            assert_eq!(peers[0].last_seen, 100);
        }
        cleanup(&path);
    }

    #[test]
    fn remember_merges_addresses_newest_first_without_duplicates() {
        let path = temp_path("merge");
        let mut cache = PeerCache::open(path.clone()).unwrap();
        cache.remember(&peer_id(2), &[addr(4001), addr(4002)], "dht", 100).unwrap();
        cache.remember(&peer_id(2), &[addr(4003)], "dht", 150).unwrap();
        cache.remember(&peer_id(2), &[addr(4002), addr(4004)], "identify", 200).unwrap();
        let peers = cache.load_recent(10, 1000, 300).unwrap();
        assert_eq!(peers[0].peer_id, peer_id(2));
        assert_eq!(peers[0].addresses, vec![addr(4002), addr(4004), addr(4003), addr(4001)]);
        assert_eq!(peers[0].last_seen, 200);
        cleanup(&path);
    }

    #[test]
    fn remember_caps_addresses_per_peer() {
        let path = temp_path("cap");
        let mut cache = PeerCache::open(path.clone()).unwrap();
        let many: Vec<String> = (0..40).map(|index| addr(5000 + index)).collect();
        cache.remember(&peer_id(3), &many, "dht", 100).unwrap();
        let peers = cache.load_recent(10, 1000, 200).unwrap();
        assert_eq!(peers[0].addresses.len(), super::MAX_ADDRESSES_PER_PEER);
        cleanup(&path);
    }

    #[test]
    fn rejects_invalid_peer_ids_and_addresses_without_writing() {
        let path = temp_path("invalid");
        let mut cache = PeerCache::open(path.clone()).unwrap();
        for bad in ["", "has space", "has/slash", "é", &"x".repeat(129)] {
            assert!(cache.remember(bad, &[addr(4001)], "dht", 100).is_err(), "accepted {bad:?}");
        }
        assert!(cache.remember(&peer_id(4), &["not a multiaddr".to_owned()], "dht", 100).is_err());
        assert!(cache.remember(&peer_id(4), &[addr(4001)], "bad source", 100).is_err());
        assert_eq!(cache.len().unwrap(), 0);
        cleanup(&path);
    }

    #[test]
    fn stale_peers_are_not_loaded() {
        let path = temp_path("stale");
        let mut cache = PeerCache::open(path.clone()).unwrap();
        cache.remember(&peer_id(5), &[addr(4001)], "dht", 100).unwrap();
        cache.remember(&peer_id(6), &[addr(4002)], "dht", 900).unwrap();
        let peers = cache.load_recent(10, 300, 1000).unwrap();
        assert_eq!(peers.len(), 1);
        assert_eq!(peers[0].peer_id, peer_id(6));
        cleanup(&path);
    }

    #[test]
    fn load_recent_orders_newest_first_and_limits() {
        let path = temp_path("order");
        let mut cache = PeerCache::open(path.clone()).unwrap();
        for tag in 0..5u8 {
            cache.remember(&peer_id(tag), &[addr(6000 + u16::from(tag))], "dht", 100 + u64::from(tag)).unwrap();
        }
        let peers = cache.load_recent(2, 10_000, 200).unwrap();
        assert_eq!(peers.len(), 2);
        assert_eq!(peers[0].peer_id, peer_id(4));
        assert_eq!(peers[1].peer_id, peer_id(3));
        cleanup(&path);
    }

    #[test]
    fn cache_evicts_least_recently_seen_when_full() {
        let path = temp_path("evict");
        let mut cache = PeerCache::open(path.clone()).unwrap();
        for index in 0..(MAX_CACHED_PEERS as u16 + 5) {
            let tag = (index % 8) as u8;
            cache.remember(
                &format!("{}{:x}", peer_id(tag), index),
                &[addr(4000 + index % 500)],
                "dht",
                1000 + index as u64,
            ).unwrap();
        }
        assert_eq!(cache.len().unwrap(), MAX_CACHED_PEERS as usize);
        let recent = cache.load_recent(MAX_CACHED_PEERS as usize, 10_000, 2_000).unwrap();
        assert_eq!(recent.len(), MAX_CACHED_PEERS as usize);
        assert!(recent.iter().all(|peer| peer.last_seen >= 1005));
        cleanup(&path);
    }

    #[test]
    fn remove_drops_peer_from_future_rejoins() {
        let path = temp_path("remove");
        let mut cache = PeerCache::open(path.clone()).unwrap();
        cache.remember(&peer_id(7), &[addr(4001)], "dht", 100).unwrap();
        cache.remove(&peer_id(7)).unwrap();
        assert!(cache.load_recent(10, 1000, 200).unwrap().is_empty());
        assert_eq!(cache.len().unwrap(), 0);
        cleanup(&path);
    }

    #[test]
    fn fails_closed_on_corrupt_database_file() {
        let path = temp_path("corrupt");
        std::fs::write(&path, b"not a sqlite database").unwrap();
        assert!(PeerCache::open(path.clone()).is_err());
        cleanup(&path);
    }

    #[test]
    fn fails_closed_on_tampered_row() {
        let path = temp_path("tampered");
        {
            let mut cache = PeerCache::open(path.clone()).unwrap();
            cache.remember(&peer_id(8), &[addr(4001)], "dht", 100).unwrap();
        }
        {
            let connection = rusqlite::Connection::open(&path).unwrap();
            connection.execute(
                "UPDATE peer_cache SET addresses = 'junk not a multiaddr'", []
            ).unwrap();
        }
        assert!(PeerCache::open(path.clone()).is_err());
        cleanup(&path);
    }

    #[test]
    fn refuses_future_schema_version() {
        let path = temp_path("schema");
        let connection = rusqlite::Connection::open(&path).unwrap();
        connection.pragma_update(None, "user_version", 99).unwrap();
        drop(connection);
        assert!(PeerCache::open(path.clone()).is_err());
        cleanup(&path);
    }
}
