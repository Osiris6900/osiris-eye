mod peer_auth;
mod revocation;

use revocation::{RevocationRegistry, RevocationSyncMessage};
use peer_auth::{is_peer_allowed, parse_revoked_peers, parse_trusted_peers};
use std::{collections::HashSet, env, error::Error, fs::{self, OpenOptions}, io::{BufRead, BufReader as StdBufReader, Write}, net::SocketAddr, path::PathBuf, time::{Duration, Instant, SystemTime, UNIX_EPOCH}};

macro_rules! emit {
    ($($arg:tt)*) => {{
        println!($($arg)*);
        let _ = std::io::stdout().flush();
    }};
}

use futures::StreamExt;
use libp2p::{
    gossipsub, identify, identity,
    kad::{self, store::MemoryStore},
    mdns, noise,
    swarm::{NetworkBehaviour, SwarmEvent},
    tcp, yamux, Multiaddr, PeerId, Swarm, SwarmBuilder,
};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use tokio::{io::{self, AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader}, net::{TcpListener, TcpStream}, sync::watch};

const PROTOCOL_VERSION: &str = "/osiris-eye/p2p/1.0.0";
const TOPIC_NAME: &str = "osiris/observations/1";
const REVOCATION_TOPIC_NAME: &str = "osiris/revocations/1";
const REVOCATION_SYNC_PAGE_SIZE: usize = 128;

const MAX_REPLAY_CACHE_BYTES: u64 = 64 * 1024 * 1024;

#[derive(Debug)]
struct ReplayGuard {
    path: PathBuf,
    seen: HashSet<String>,
}

impl ReplayGuard {
    fn open(path: PathBuf) -> Result<Self, Box<dyn Error>> {
        let mut seen = HashSet::new();
        if path.exists() {
            let file = fs::File::open(&path)?;
            for line in StdBufReader::new(file).lines() {
                let line = line?;
                let (timestamp, id) = line.split_once(' ')
                    .ok_or("corrupt replay cache entry; refusing to start")?;
                timestamp.parse::<u64>()
                    .map_err(|_| "corrupt replay cache timestamp; refusing to start")?;
                if id.len() != 64 || !id.bytes().all(|b| b.is_ascii_hexdigit()) {
                    return Err("corrupt replay cache message ID; refusing to start".into());
                }
                seen.insert(id.to_ascii_lowercase());
            }
            if fs::metadata(&path)?.len() > MAX_REPLAY_CACHE_BYTES {
                return Err("replay cache exceeds 64 MiB; archive it safely before restart".into());
            }
        }
        Ok(Self { path, seen })
    }

    fn message_id(envelope: &ObservationEnvelope) -> Result<String, Box<dyn Error>> {
        let canonical = serde_json::to_vec(&(
            &envelope.schema, &envelope.instrument, &envelope.observed_at,
            &envelope.provider, &envelope.payload_sha256,
        ))?;
        Ok(format!("{:x}", Sha256::digest(canonical)))
    }

    /// Persist before accepting. A crash after sync may reject a retry, never accept a duplicate.
    fn record_if_new(&mut self, envelope: &ObservationEnvelope) -> Result<bool, Box<dyn Error>> {
        let id = Self::message_id(envelope)?;
        if self.seen.contains(&id) {
            return Ok(false);
        }
        let timestamp = SystemTime::now().duration_since(UNIX_EPOCH)?.as_secs();
        let entry = format!("{timestamp} {id}\n");
        let current_size = fs::metadata(&self.path).map(|m| m.len()).unwrap_or(0);
        if current_size.saturating_add(entry.len() as u64) > MAX_REPLAY_CACHE_BYTES {
            return Err("replay cache reached 64 MiB capacity; refusing new observations".into());
        }
        let mut file = OpenOptions::new().create(true).append(true).open(&self.path)?;
        file.write_all(entry.as_bytes())?;
        file.sync_data()?;
        self.seen.insert(id);
        Ok(true)
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
        if self.payload.len() > 1_048_576 { return Err("payload_too_large"); }
        let digest = self.payload_sha256.as_bytes();
        if digest.len() != 64 || !digest.iter().all(u8::is_ascii_hexdigit) { return Err("invalid_payload_sha256"); }
        let actual = format!("{:x}", Sha256::digest(self.payload.as_bytes()));
        if !actual.eq_ignore_ascii_case(&self.payload_sha256) { return Err("payload_hash_mismatch"); }
        Ok(())
    }
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
    #[test] fn replay_guard_fails_closed_on_corrupt_cache() {
        let path = std::env::temp_dir().join(format!("osiris-replay-corrupt-{}.cache", std::process::id()));
        std::fs::write(&path, "not-a-valid-entry\\n").unwrap();
        assert!(super::ReplayGuard::open(path.clone()).is_err());
        let _ = std::fs::remove_file(path);
    }
    #[test] fn accepts_valid_envelope() { assert!(valid().validate().is_ok()); }
    #[test] fn rejects_wrong_schema() { let mut v=valid(); v.schema="v0".into(); assert_eq!(v.validate(),Err("unsupported_schema")); }
    #[test] fn rejects_bad_digest() { let mut v=valid(); v.payload_sha256="xyz".into(); assert_eq!(v.validate(),Err("invalid_payload_sha256")); }
    #[test] fn rejects_empty_instrument() { let mut v=valid(); v.instrument.clear(); assert_eq!(v.validate(),Err("invalid_instrument")); }
    #[test] fn rejects_payload_hash_mismatch() { let mut v=valid(); v.payload.push_str("-tampered"); assert_eq!(v.validate(),Err("payload_hash_mismatch")); }
    #[test] fn rejects_oversized_payload() { let mut v=valid(); v.payload="x".repeat(1_048_577); assert_eq!(v.validate(),Err("payload_too_large")); }
    #[test] fn rejects_empty_provider() { let mut v=valid(); v.provider.clear(); assert_eq!(v.validate(),Err("invalid_provider")); }
    #[test] fn reconnect_interval_uses_default_when_unset_or_invalid() {
        assert_eq!(super::parse_interval_secs(None, 15, 5, 300), 15);
        assert_eq!(super::parse_interval_secs(Some("bad"), 15, 5, 300), 15);
    }
    #[test] fn reconnect_interval_is_clamped_to_safe_bounds() {
        assert_eq!(super::parse_interval_secs(Some("1"), 15, 5, 300), 5);
        assert_eq!(super::parse_interval_secs(Some("999"), 15, 5, 300), 300);
        assert_eq!(super::parse_interval_secs(Some("45"), 15, 5, 300), 45);
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
}

#[derive(NetworkBehaviour)]
#[behaviour(to_swarm = "BehaviourEvent")]
struct Behaviour {
    gossipsub: gossipsub::Behaviour,
    kademlia: kad::Behaviour<MemoryStore>,
    mdns: mdns::tokio::Behaviour,
    identify: identify::Behaviour,
}

#[derive(Debug)]
enum BehaviourEvent {
    Gossipsub(gossipsub::Event),
    Kademlia(kad::Event),
    Mdns(mdns::Event),
    Identify(identify::Event),
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

fn parse_interval_secs(raw: Option<&str>, default: u64, min: u64, max: u64) -> u64 {
    raw.and_then(|value| value.parse::<u64>().ok())
        .unwrap_or(default)
        .clamp(min, max)
}

fn peer_id_from_multiaddr(address: &Multiaddr) -> Option<PeerId> {
    address.iter().find_map(|protocol| match protocol {
        libp2p::multiaddr::Protocol::P2p(peer_id) => Some(peer_id),
        _ => None,
    })
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

fn parse_bootstrap_peers(
    swarm: &mut Swarm<Behaviour>,
) -> Result<Vec<(PeerId, Multiaddr)>, Box<dyn Error>> {
    let Some(raw) = env::var_os("OSIRIS_BOOTSTRAP_PEERS") else {
        return Ok(Vec::new());
    };

    let mut peers = Vec::new();
    for entry in raw.to_string_lossy().split(',').map(str::trim).filter(|s| !s.is_empty()) {
        let address: Multiaddr = entry.parse()?;
        let peer = peer_id_from_multiaddr(&address)
            .ok_or("bootstrap multiaddr must include /p2p/<peer-id>")?;
        swarm.behaviour_mut().kademlia.add_address(&peer, address.clone());
        if !peers.iter().any(|(known, _)| *known == peer) {
            swarm.dial(address.clone())?;
            peers.push((peer, address));
        }
    }
    if !peers.is_empty() {
        let _ = swarm.behaviour_mut().kademlia.bootstrap();
    }
    Ok(peers)
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
}

fn build_health_snapshot(
    peer_id: &PeerId,
    started_at: Instant,
    swarm: &Swarm<Behaviour>,
    bootstrap_peers: &[(PeerId, Multiaddr)],
) -> HealthSnapshot {
    let connected_peers = swarm.connected_peers().count();
    let bootstrap_peers_connected = bootstrap_peers
        .iter()
        .filter(|(bootstrap_peer, _)| swarm.is_connected(bootstrap_peer))
        .count();
    HealthSnapshot {
        status: "alive",
        ready: bootstrap_peers.is_empty() || bootstrap_peers_connected == bootstrap_peers.len(),
        peer_id: peer_id.to_string(),
        uptime_seconds: started_at.elapsed().as_secs(),
        connected_peers,
        bootstrap_peers_connected,
        bootstrap_peers_total: bootstrap_peers.len(),
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
    let key = load_or_create_identity()?;
    let peer_id = PeerId::from(key.public());

    let gossipsub_config = gossipsub::ConfigBuilder::default()
        .heartbeat_interval(Duration::from_secs(10))
        .validation_mode(gossipsub::ValidationMode::Strict)
        .build()?;
    gossipsub_config.validate_messages();

    let gossipsub = gossipsub::Behaviour::new(
        gossipsub::MessageAuthenticity::Signed(key.clone()),
        gossipsub_config,
    )?;
    let topic = gossipsub::IdentTopic::new(TOPIC_NAME);
    let revocation_topic = gossipsub::IdentTopic::new(REVOCATION_TOPIC_NAME);
    let mut behaviour = Behaviour {
        gossipsub,
        kademlia: kad::Behaviour::new(peer_id, MemoryStore::new(peer_id)),
        mdns: mdns::tokio::Behaviour::new(mdns::Config::default(), peer_id)?,
        identify: identify::Behaviour::new(
            identify::Config::new(PROTOCOL_VERSION.to_owned(), key.public()),
        ),
    };
    behaviour.kademlia.set_mode(Some(kad::Mode::Server));
    behaviour.gossipsub.subscribe(&topic)?;
    behaviour.gossipsub.subscribe(&revocation_topic)?;

    let mut swarm = SwarmBuilder::with_existing_identity(key)
        .with_tokio()
        .with_tcp(tcp::Config::default(), noise::Config::new, yamux::Config::default)?
        .with_behaviour(|_| behaviour)?
        .with_swarm_config(|config| config.with_idle_connection_timeout(Duration::from_secs(90)))
        .build();

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

    let bootstrap_peers = parse_bootstrap_peers(&mut swarm)?;
    let trusted_peers = parse_trusted_peers(env::var("OSIRIS_TRUSTED_PEERS").ok().as_deref())?;
    let mut revoked_peer_set = parse_revoked_peers(env::var("OSIRIS_REVOKED_PEERS").ok().as_deref())?
        .unwrap_or_default();
    let authority_key = env::var("OSIRIS_REVOCATION_AUTHORITY_PUBLIC_KEY").ok();
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
    emit!("replay_protection=durable cache_entries={} cache_path={}", replay_guard.seen.len(), replay_path.display());
    for (bootstrap_peer, _) in &bootstrap_peers {
        if !is_peer_allowed(bootstrap_peer, &trusted_peers, &revoked_peers) {
            return Err(format!(
                "configured bootstrap peer {bootstrap_peer} is not allowed by local trust/revocation policy"
            ).into());
        }
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
    let revocation_sync_interval_secs = parse_interval_secs(
        env::var("OSIRIS_REVOCATION_SYNC_INTERVAL_SECS").ok().as_deref(),
        60,
        30,
        3600,
    );
    let mut revocation_sync_tick = tokio::time::interval(Duration::from_secs(revocation_sync_interval_secs));
    revocation_sync_tick.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
    let mut reconnect_tick = tokio::time::interval(Duration::from_secs(reconnect_interval_secs));
    reconnect_tick.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
    let health_interval_secs = env::var("OSIRIS_HEALTH_INTERVAL_SECS")
        .ok()
        .and_then(|value| value.parse::<u64>().ok())
        .unwrap_or(30)
        .clamp(5, 300);
    let mut health_tick = tokio::time::interval(Duration::from_secs(health_interval_secs));
    health_tick.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
    let started_at = Instant::now();
    let health_address: SocketAddr = env::var("OSIRIS_HEALTH_HTTP_ADDR")
        .unwrap_or_else(|_| "127.0.0.1:9090".to_owned())
        .parse()?;
    let initial_health = build_health_snapshot(&peer_id, started_at, &swarm, &bootstrap_peers);
    let (health_tx, health_rx) = watch::channel(initial_health);
    tokio::spawn(async move {
        if let Err(error) = serve_health_endpoint(health_address, health_rx).await {
            eprintln!("health_http_server_error={error}");
        }
    });
    emit!("OSIRIS EYE P2P node starting");
    emit!("peer_id={peer_id}");
    emit!("topic={TOPIC_NAME}");
    emit!("bootstrap_peers={}", bootstrap_peers.len());
    emit!("bootstrap_reconnect_interval_seconds={reconnect_interval_secs}");
    emit!("revocation_sync_interval_seconds={revocation_sync_interval_secs}");
    emit!("health_interval_seconds={health_interval_secs}");
    emit!("Live trading and broker order submission are not part of this node.");

    let stdin = BufReader::new(io::stdin());
    let mut lines = stdin.lines();
    // EOF is normal for daemonized nodes (stdin is often /dev/null).
    // Disable only the stdin branch; keep polling the swarm for incoming peers.
    let mut stdin_open = true;

    loop {
        tokio::select! {
            _ = tokio::signal::ctrl_c() => {
                emit!("shutdown_signal=ctrl_c");
                break;
            }
            _ = health_tick.tick() => {
                let snapshot = build_health_snapshot(&peer_id, started_at, &swarm, &bootstrap_peers);
                let connected_peers = snapshot.connected_peers;
                let connected_bootstrap_peers = snapshot.bootstrap_peers_connected;
                health_tx.send_replace(snapshot);
                emit!(
                    "node_health status=alive ready={} peer_id={} uptime_seconds={} connected_peers={} bootstrap_peers_connected={}/{}",
                    build_health_snapshot(&peer_id, started_at, &swarm, &bootstrap_peers).ready,
                    peer_id,
                    started_at.elapsed().as_secs(),
                    connected_peers,
                    connected_bootstrap_peers,
                    bootstrap_peers.len()
                );
            }
            _ = revocation_sync_tick.tick(), if revocation_registry.is_some() => {
                let request = serde_json::to_vec(&RevocationSyncMessage::SyncRequestPage { after_peer_id: None })?;
                match swarm.behaviour_mut().gossipsub.publish(revocation_topic.clone(), request) {
                    Ok(message_id) => emit!("revocation_anti_entropy_request_sent message_id={message_id}"),
                    Err(error) => emit!("revocation_anti_entropy_request_deferred error={error}"),
                }
            }
            _ = reconnect_tick.tick() => {
                for (bootstrap_peer, address) in &bootstrap_peers {
                    if !swarm.is_connected(bootstrap_peer) {
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
            event = swarm.select_next_some() => match event {
                SwarmEvent::NewListenAddr { listener_id, address } => {
                    emit!("listener_ready listener={listener_id:?} address={address}/p2p/{peer_id}");
                }
                SwarmEvent::ListenerError { listener_id, error } => {
                    emit!("listener_error listener={listener_id:?} error={error}");
                }
                SwarmEvent::ListenerClosed { listener_id, addresses, reason } => {
                    emit!("listener_closed listener={listener_id:?} addresses={addresses:?} reason={reason:?}");
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
                }
                SwarmEvent::ConnectionClosed { peer_id, cause, num_established, .. } => {
                    emit!("peer_disconnected={peer_id} cause={cause:?} remaining={num_established}");
                }
                SwarmEvent::Behaviour(BehaviourEvent::Mdns(mdns::Event::Discovered(peers))) => {
                    for (peer, address) in peers {
                        if !is_peer_allowed(&peer, &trusted_peers, &revoked_peers) {
                            emit!("mdns_peer_ignored={peer} reason=not_in_trusted_peer_allowlist");
                            continue;
                        }
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
                SwarmEvent::Behaviour(BehaviourEvent::Gossipsub(gossipsub::Event::Subscribed { peer_id, .. })) => {
                    if !is_peer_allowed(&peer_id, &trusted_peers, &revoked_peers) {
                        emit!("peer_rejected={peer_id} reason=not_in_trusted_peer_allowlist");
                        let _ = swarm.disconnect_peer_id(peer_id);
                        continue;
                    }
                    emit!("gossipsub_subscribed peer={peer_id}");
                    if revocation_registry.is_some() {
                        let request = serde_json::to_vec(&RevocationSyncMessage::SyncRequestPage { after_peer_id: None })?;
                        let _ = swarm.behaviour_mut().gossipsub.publish(revocation_topic.clone(), request);
                    }
                }
                SwarmEvent::Behaviour(BehaviourEvent::Gossipsub(gossipsub::Event::Message {
                    propagation_source, message, ..
                })) => {
                    if message.topic.as_str() == REVOCATION_TOPIC_NAME {
                        if !is_peer_allowed(&propagation_source, &trusted_peers, &revoked_peers) {
                            emit!("revocation_sync_message_rejected peer={propagation_source} reason=peer_not_allowed");
                            continue;
                        }
                        let Some(registry) = revocation_registry.as_mut() else {
                            emit!("revocation_sync_disabled reason=authority_key_not_configured");
                            continue;
                        };
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
                            Ok(RevocationSyncMessage::Record(record)) => {
                                match registry.apply(record.clone(), true) {
                                    Ok(true) => {
                                        let peer = record.peer_id.parse::<PeerId>()?;
                                        revoked_peers.get_or_insert_with(HashSet::new).insert(peer);
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
                            Ok(RevocationSyncMessage::SnapshotPage { records, next_cursor }) => {
                                let mut applied = 0usize;
                                for record in records {
                                    match registry.apply(record.clone(), true) {
                                        Ok(true) => {
                                            let peer = record.peer_id.parse::<PeerId>()?;
                                            revoked_peers.get_or_insert_with(HashSet::new).insert(peer);
                                            let bytes = serde_json::to_vec(&RevocationSyncMessage::Record(record))?;
                                            let _ = swarm.behaviour_mut().gossipsub.publish(revocation_topic.clone(), bytes);
                                            let _ = swarm.disconnect_peer_id(peer);
                                            applied += 1;
                                        }
                                        Ok(false) => {}
                                        Err(error) => emit!("revocation_snapshot_record_rejected source={propagation_source} error={error}"),
                                    }
                                }
                                emit!("revocation_sync_page_applied source={propagation_source} applied={applied} has_more={}", next_cursor.is_some());
                                if let Some(cursor) = next_cursor {
                                    let request = RevocationSyncMessage::SyncRequestPage { after_peer_id: Some(cursor) };
                                    let bytes = serde_json::to_vec(&request)?;
                                    let _ = swarm.behaviour_mut().gossipsub.publish(revocation_topic.clone(), bytes);
                                }
                            }
                            Err(error) => emit!("revocation_sync_message_rejected peer={propagation_source} error={error}"),
                        }
                        continue;
                    }
                    if !is_peer_allowed(&propagation_source, &trusted_peers, &revoked_peers) {
                        emit!("message_rejected peer={propagation_source} reason=not_in_trusted_peer_allowlist");
                        continue;
                    }
                    match serde_json::from_slice::<ObservationEnvelope>(&message.data) {
                        Ok(envelope) if envelope.validate().is_ok() => {
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
                SwarmEvent::Behaviour(BehaviourEvent::Kademlia(event)) => {
                    println!("dht_event={event:?}");
                }
                SwarmEvent::Behaviour(BehaviourEvent::Identify(event)) => {
                    println!("identify_event={event:?}");
                }
                _ => {}
            },
            line = lines.next_line(), if stdin_open => match line? {
                Some(line) if !line.trim().is_empty() => {
                    let envelope: ObservationEnvelope = serde_json::from_str(&line)?;
                    if let Err(reason) = envelope.validate() {
                        eprintln!("publish_rejected reason={reason}");
                        continue;
                    }
                    swarm.behaviour_mut().gossipsub.publish(topic.clone(), line.into_bytes())?;
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
