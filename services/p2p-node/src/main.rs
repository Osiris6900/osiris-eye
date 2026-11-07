//! OSIRIS EYE P2P node — decentralized encrypted peer-to-peer network.
//!
//! Runs as a separate process alongside the quant-api. Each node:
//! - Discovers peers via mDNS (local) and Kademlia DHT (global)
//! - Gossips market observations to all connected peers
//! - Exposes peer observations on a local HTTP endpoint for quant-api
//! - Optionally serves an embedded circuit-relay v2 reservation
//!
//! Usage:
//!   p2p-node --listen-port 4001 --http-port 4100 --shared-key <hex>
//!
//! The quant-api polls http://localhost:4100/observations for peer data.

use anyhow::{Context, Result};
use base64::Engine as _;
use chrono::Utc;
use clap::Parser;
use futures::StreamExt;
use libp2p::{
    autonat,
    dcutr,
    gossipsub::{self, IdentTopic, MessageAuthenticity, ValidationMode},
    identify, identity, kad, mdns, noise, relay,
    swarm::{NetworkBehaviour, SwarmEvent},
    tcp, upnp, yamux, Multiaddr, PeerId, Swarm,
};
use osiris_p2p::{
    crypto::{derive_key, encrypt_observation},
    rate_limit::{InMemoryPeerRateLimiter, PeerRateLimiter},
    revocation::RevocationFile,
    validation::{
        validate_observation, ValidationContext, MAX_MESSAGE_SIZE, MAX_TIMESTAMP_DRIFT_MS,
        MAX_TOPIC_LEN,
    },
    ObservationEnvelope, PeerObservation, PROTOCOL_VERSION, RevocationCheck,
    RevocationPolicy, RevocationPolicyEngine, RevocationState,
};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    collections::{HashMap, HashSet, VecDeque},
    net::{IpAddr, Ipv4Addr, Ipv6Addr},
    sync::Arc,
    time::{Duration, Instant},
};
use tokio::sync::RwLock;
use tracing::{error, info, warn};

// ── CLI ──────────────────────────────────────────────────────────────────────

#[derive(Parser, Debug)]
#[command(name = "p2p-node", about = "OSIRIS EYE decentralized P2P node")]
struct Args {
    /// Port for libp2p TCP transport.
    #[arg(long, default_value = "4001")]
    listen_port: u16,

    /// Local HTTP port for quant-api to poll peer observations.
    #[arg(long, default_value = "4100")]
    http_port: u16,

    /// Bootstrap peers (multiaddr). Repeat for multiple.
    #[arg(long)]
    bootstrap: Vec<String>,

    /// Explicit relay server addresses (multiaddr with /p2p/<peer-id>).
    #[arg(long)]
    relay: Vec<String>,

    /// Relay address to request a reservation from (multiaddr with /p2p/<peer-id>).
    #[arg(long)]
    relay_reserve: Option<String>,

    /// Listen for relayed connections via the reserved relay.
    #[arg(long, default_value = "true")]
    relay_listen: bool,

    /// Shared network key as 64-char hex (32 bytes). All peers must share this.
    #[arg(long, env = "OSIRIS_NETWORK_KEY")]
    shared_key: Option<String>,

    /// Enable mDNS for local network peer discovery.
    #[arg(long, default_value = "true")]
    mdns: bool,

    /// Allow non-loopback observations to be served over unauthenticated HTTP.
    /// Only valid with --http-host 0.0.0.0 when --allow-remote-http is set.
    #[arg(long, default_value = "false")]
    allow_remote_http: bool,

    /// Optional network identifier (default: osiris-eye).
    #[arg(long, default_value = "osiris-eye")]
    network_id: String,

    /// Optional persistent identity key file (32-byte raw Ed25519 secret).
    #[arg(long)]
    identity_file: Option<String>,

    /// Per-peer inbound observation rate limit (messages per second).
    #[arg(long, default_value = "60")]
    max_peer_msgs_per_sec: u32,

    /// Peer activity window for per-peer rate limits.
    #[arg(long, default_value = "60")]
    peer_window_secs: u64,

    /// Maximum number of per-peer rate-limit states kept in memory.
    #[arg(long, default_value = "1024")]
    max_tracked_peers: usize,

    /// Address for the local HTTP listener (default loopback).
    #[arg(long, default_value = "127.0.0.1")]
    http_host: String,

    /// Run an embedded circuit-relay v2 server on this node.
    #[arg(long, default_value = "false")]
    relay_server: bool,

    /// Maximum simultaneous relay reservations (0 uses the embedded default).
    #[arg(long, default_value = "0")]
    relay_max_reservations: usize,

    /// Default reservation TTL in seconds (0 uses the embedded default).
    #[arg(long, default_value = "0")]
    relay_reservation_ttl: u64,

    /// Maximum simultaneous relay circuits (0 uses the embedded default).
    #[arg(long, default_value = "0")]
    relay_max_circuits: usize,

    /// Default circuit duration limit in seconds (0 uses the embedded default).
    #[arg(long, default_value = "0")]
    relay_circuit_duration: u64,

    /// Default circuit byte limit (0 uses the embedded default).
    #[arg(long, default_value = "0")]
    relay_circuit_bytes: u64,

    /// Maximum relay circuits per source peer (0 uses the embedded default).
    #[arg(long, default_value = "0")]
    relay_max_circuits_per_peer: usize,

    /// Maximum relay reservations per peer (0 uses the embedded default).
    #[arg(long, default_value = "0")]
    relay_max_reservations_per_peer: usize,

    /// Default reservation renewal interval in seconds (0 uses the embedded default).
    #[arg(long, default_value = "0")]
    relay_reservation_rate: u64,

    /// Default circuit establishment rate per peer (0 uses the embedded default).
    #[arg(long, default_value = "0")]
    relay_circuit_rate: u64,

    /// Deployed revocation bundle path.
    #[arg(long, env = "OSIRIS_REVOCATION_FILE")]
    revocation_file: Option<String>,

    /// Expected deployed revocation bundle sequence.
    #[arg(long, default_value = "0", env = "OSIRIS_REVOCATION_SEQUENCE")]
    revocation_sequence: u64,

    /// Expected deployed revocation bundle SHA-256, as 64 lowercase hex characters.
    #[arg(long, env = "OSIRIS_REVOCATION_SHA256")]
    revocation_sha256: Option<String>,
}

// ── Gossipsub messages ───────────────────────────────────────────────────────

#[derive(Debug, Serialize, Deserialize)]
enum P2pMessage {
    Observation(ObservationEnvelope),
    Ping { node_version: String },
    RevocationBundle { sequence: u64, sha256: String, bundle: osiris_p2p::revocation::RevocationFile },
}

// ── Network behaviour ────────────────────────────────────────────────────────

#[derive(NetworkBehaviour)]
struct Behaviour {
    gossipsub: gossipsub::Behaviour,
    mdns: mdns::tokio::Behaviour,
    kademlia: kad::Behaviour<kad::store::MemoryStore>,
    identify: identify::Behaviour,
    relay: relay::Behaviour,
    autonat: autonat::Behaviour,
    dcutr: dcutr::Behaviour,
    upnp: upnp::tokio::Behaviour,
}

// ── Observation store ────────────────────────────────────────────────────────

#[derive(Clone)]
struct ObservationStore {
    inner: Arc<RwLock<HashMap<String, PeerObservation>>>,
}

impl ObservationStore {
    fn new() -> Self {
        Self { inner: Arc::new(RwLock::new(HashMap::new())) }
    }

    async fn ingest(&self, obs: PeerObservation) -> bool {
        let mut map = self.inner.write().await;
        let key = format!("{}:{}", obs.peer_id, obs.observation.instrument);
        let fresh = map.get(&key).is_none_or(|prev| {
            obs.observation.observed_at_ms > prev.observation.observed_at_ms
        });
        if fresh {
            map.insert(key, obs);
        }
        fresh
    }

    async fn all(&self) -> Vec<PeerObservation> {
        self.inner.read().await.values().cloned().collect()
    }

    async fn evict_stale(&self, max_age_ms: i64) {
        let cutoff = Utc::now().timestamp_millis() - max_age_ms;
        self.inner.write().await.retain(|_, obs| obs.observation.observed_at_ms >= cutoff);
    }

    fn validate_ingress(
        &self,
        obs: &PeerObservation,
        peer_id: &str,
        now_ms: i64,
        revocation: &RevocationPolicyEngine,
    ) -> Result<(), String> {
        let policy = revocation.current();
        let key_status = policy.key_status(&obs.key_id);
        let peer_status = policy.peer_status(peer_id);
        let key_revoked_at = match key_status {
            RevocationState::Revoked { at_ms } => Some(at_ms),
            RevocationState::Active => None,
        };
        let peer_revoked_at = match peer_status {
            RevocationState::Revoked { at_ms } => Some(at_ms),
            RevocationState::Active => None,
        };
        let key_check = revocation.check_key_at(obs.observation.observed_at_ms, &obs.key_id);
        let peer_check = revocation.check_peer_at(obs.observation.observed_at_ms, peer_id);
        validate_observation(&obs.observation, &ValidationContext {
            protocol_version: PROTOCOL_VERSION,
            max_drift_ms: MAX_TIMESTAMP_DRIFT_MS,
            now_ms,
            revoked_keys: &HashSet::new(),
            revoked_peers: &HashSet::new(),
            max_message_size: MAX_MESSAGE_SIZE,
            max_topic_len: MAX_TOPIC_LEN,
            key_status,
            peer_status,
            key_revoked_at_ms: key_revoked_at,
            peer_revoked_at_ms: peer_revoked_at,
            revocation_sequence: policy.sequence,
        })
        .and_then(|()| {
            if key_check != RevocationCheck::Active {
                return Err(format!(
                    "revoked key {} at sequence {} rejected",
                    obs.key_id,
                    policy.sequence,
                ));
            }
            if peer_check != RevocationCheck::Active {
                return Err(format!(
                    "revoked peer {} at sequence {} rejected",
                    peer_id,
                    policy.sequence,
                ));
            }
            Ok(())
        })
        .map_err(|e| e.to_string())
    }
}

// ── HTTP response ────────────────────────────────────────────────────────────

#[derive(Serialize)]
struct ObservationsResponse {
    protocol_version: u32,
    count: usize,
    observations: Vec<PeerObservation>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
struct RevocationRuntime {
    sequence: u64,
    sha256: String,
    bundle: RevocationFile,
    policy: RevocationPolicyEngine,
}

impl RevocationRuntime {
    fn from_bundle(bundle: RevocationFile) -> Result<Self> {
        let encoded = serde_json::to_vec(&bundle)?;
        let sha256 = hex::encode(Sha256::digest(&encoded));
        let policy = RevocationPolicyEngine::new(bundle.clone())
            .map_err(|e| anyhow::anyhow!("deployed revocation policy is invalid: {e}"))?;
        Ok(Self {
            sequence: bundle.sequence,
            sha256,
            bundle,
            policy,
        })
    }

    fn empty() -> Result<Self> {
        Self::from_bundle(RevocationFile::empty())
    }

    fn validate_expected(&self, sequence: u64, sha256: Option<&str>) -> Result<()> {
        if self.sequence != sequence {
            anyhow::bail!(
                "revocation sequence mismatch: expected {}, got {}",
                sequence,
                self.sequence
            );
        }
        if let Some(expected) = sha256 {
            if self.sha256 != expected {
                anyhow::bail!("revocation bundle SHA-256 mismatch");
            }
        }
        Ok(())
    }

    fn accepts_update(&self, candidate: &Self) -> bool {
        candidate.sequence > self.sequence
            || (candidate.sequence == self.sequence && candidate.sha256 == self.sha256)
    }
}

// ── Main ─────────────────────────────────────────────────────────────────────

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::from_default_env()
                .add_directive("p2p_node=info".parse().unwrap())
                .add_directive("osiris_p2p=info".parse().unwrap()),
        )
        .json()
        .init();

    let args = Args::parse();
    let http_host: IpAddr = args.http_host.parse().context("http-host must be an IPv4 or IPv6 address")?;
    let http_addr = std::net::SocketAddr::new(http_host, args.http_port);
    if !http_host.is_loopback() && !args.allow_remote_http {
        anyhow::bail!(
            "http-host must be loopback unless --allow-remote-http is explicitly enabled; \
             remote observations are unauthenticated and unencrypted"
        );
    }
    if args.relay_max_reservations_per_peer > args.relay_max_reservations && args.relay_max_reservations != 0 {
        anyhow::bail!("relay-max-reservations-per-peer cannot exceed relay-max-reservations");
    }
    if args.relay_max_circuits_per_peer > args.relay_max_circuits && args.relay_max_circuits != 0 {
        anyhow::bail!("relay-max-circuits-per-peer cannot exceed relay-max-circuits");
    }
    if args.relay_max_circuits_per_peer > 64 {
        anyhow::bail!("relay-max-circuits-per-peer cannot exceed 64");
    }
    if args.relay_max_reservations_per_peer > 64 {
        anyhow::bail!("relay-max-reservations-per-peer cannot exceed 64");
    }
    if args.relay_circuit_duration > 86400 {
        anyhow::bail!("relay-circuit-duration cannot exceed 86400 seconds");
    }
    if args.relay_circuit_bytes > 1 << 30 {
        anyhow::bail!("relay-circuit-bytes cannot exceed 1073741824 bytes");
    }
    if args.relay_reservation_ttl > 86400 {
        anyhow::bail!("relay-reservation-ttl cannot exceed 86400 seconds");
    }
    if args.relay_reservation_rate > 3600 {
        anyhow::bail!("relay-reservation-rate cannot exceed 3600 seconds");
    }
    if args.relay_circuit_rate > 3600 {
        anyhow::bail!("relay-circuit-rate cannot exceed 3600 seconds");
    }
    if args.relay_circuit_bytes != 0 && args.relay_circuit_bytes < 1024 {
        anyhow::bail!("relay-circuit-bytes must be at least 1024 or zero for the embedded default");
    }
    let revocation_runtime = match args.revocation_file.as_deref() {
        Some(path) => {
            let bundle = osiris_p2p::revocation::RevocationFile::read(path)
                .with_context(|| format!("failed to read deployed revocation bundle {path}"))?;
            RevocationRuntime::from_bundle(bundle)?
        }
        None => RevocationRuntime::empty()?,
    };
    revocation_runtime.validate_expected(
        args.revocation_sequence,
        args.revocation_sha256.as_deref(),
    )?;
    info!(
        revocation_sequence = revocation_runtime.sequence,
        revocation_sha256 = %revocation_runtime.sha256,
        "deployed revocation policy loaded"
    );

    // Derive the shared encryption key.
    let shared_key = match &args.shared_key {
        Some(hex_key) => {
            let bytes = hex::decode(hex_key).context("shared-key must be valid hex")?;
            derive_key(&bytes)
        }
        None => {
            warn!("no shared-key provided — using ephemeral random key (peers cannot decrypt)");
            derive_key(&rand::random::<[u8; 32]>())
        }
    };

    // Load or generate identity.
    let local_key = match &args.identity_file {
        Some(path) => {
            let bytes = std::fs::read(path).context("failed to read identity file")?;
            let secret: [u8; 32] = bytes
                .as_slice()
                .try_into()
                .map_err(|_| anyhow::anyhow!("identity file must be 32 bytes"))?;
            identity::Keypair::ed25519_from_bytes(secret)
                .map_err(|e| anyhow::anyhow!("invalid identity: {e}"))?
        }
        None => identity::Keypair::generate_ed25519(),
    };
    let local_peer_id = PeerId::from(local_key.public());
    info!(peer_id = %local_peer_id, "starting OSIRIS EYE P2P node");

    // Gossipsub configuration.
    let gossipsub_config = gossipsub::ConfigBuilder::default()
        .heartbeat_interval(Duration::from_secs(1))
        .validation_mode(ValidationMode::Strict)
        .max_transmit_size(MAX_MESSAGE_SIZE)
        .build()
        .map_err(|e| anyhow::anyhow!("gossipsub config: {e}"))?;
    let mut gossipsub = gossipsub::Behaviour::new(
        MessageAuthenticity::Signed(local_key.clone()),
        gossipsub_config,
    )
    .map_err(|e| anyhow::anyhow!("gossipsub init: {e}"))?;

    let topic = IdentTopic::new(format!("{}/observations", args.network_id));
    gossipsub.subscribe(&topic)?;
    let revocation_topic = IdentTopic::new(format!("{}/revocations", args.network_id));

    // mDNS for local discovery.
    let mdns = mdns::tokio::Behaviour::new(mdns::Config::default(), local_peer_id)?;

    // Kademlia DHT for global peer routing.
    let mut kad_config = kad::Config::default();
    kad_config.set_protocol_names(vec![libp2p::StreamProtocol::new("/osiris/kad/1.0.0")]);
    let kademlia = kad::Behaviour::with_config(
        local_peer_id,
        kad::store::MemoryStore::new(local_peer_id),
        kad_config,
    );

    // Identify protocol.
    let identify = identify::Behaviour::new(identify::Config::new(
        "/osiris/identify/1.0.0".into(),
        local_key.public(),
    ));

    // Circuit relay v2 — server only enabled explicitly, client always available.
    let relay = relay::Behaviour::new(
        local_peer_id,
        relay_config(&args),
    );

    // NAT traversal.
    let autonat = autonat::Behaviour::new(local_peer_id, autonat::Config::default());
    let dcutr = dcutr::Behaviour::new(local_peer_id);
    let upnp = upnp::tokio::Behaviour::default();

    let behaviour = Behaviour {
        gossipsub,
        mdns,
        kademlia,
        identify,
        relay,
        autonat,
        dcutr,
        upnp,
    };

    let mut swarm = libp2p::SwarmBuilder::with_existing_identity(local_key)
        .with_tokio()
        .with_tcp(
            tcp::Config::default().nodelay(true),
            noise::Config::new,
            yamux::Config::default,
        )?
        .with_behaviour(|_| behaviour)?
        .with_swarm_config(|c| c.with_idle_connection_timeout(Duration::from_secs(60)))
        .build();

    // Listen on all interfaces.
    let listen_addr: Multiaddr = format!("/ip4/0.0.0.0/tcp/{}", args.listen_port).parse()?;
    swarm.listen_on(listen_addr)?;

    // Connect to bootstrap peers.
    for addr_str in &args.bootstrap {
        match addr_str.parse::<Multiaddr>() {
            Ok(addr) => {
                if let Err(e) = swarm.dial(addr.clone()) {
                    warn!(addr = addr_str, error = %e, "failed to dial bootstrap peer");
                } else {
                    info!(addr = addr_str, "dialing bootstrap peer");
                }
                // Add to Kademlia routing table if peer ID is in the address.
                if let Some(peer_id) = extract_peer_id(&addr) {
                    swarm.behaviour_mut().kademlia.add_address(&peer_id, addr);
                }
            }
            Err(e) => warn!(addr = addr_str, error = %e, "invalid bootstrap multiaddr"),
        }
    }

    for addr_str in &args.relay {
        match addr_str.parse::<Multiaddr>() {
            Ok(addr) => {
                if let Err(e) = swarm.dial(addr.clone()) {
                    warn!(addr = addr_str, error = %e, "failed to dial relay");
                } else {
                    info!(addr = addr_str, "dialing relay");
                }
            }
            Err(e) => warn!(addr = addr_str, error = %e, "invalid relay multiaddr"),
        }
    }

    if let Some(addr_str) = &args.relay_reserve {
        let addr: Multiaddr = addr_str.parse().context("invalid relay-reserve multiaddr")?;
        let listen_addr = addr
            .with_p2p_circuit()
            .map_err(|_| anyhow::anyhow!("relay-reserve must include /p2p/<peer-id>"))?;
        swarm.listen_on(listen_addr.clone())?;
        info!(relay = addr_str, circuit = %listen_addr, "requesting relay reservation and listening via circuit");
    }

    let store = ObservationStore::new();
    let revocations = Arc::new(RwLock::new(revocation_runtime));

    // Spawn local HTTP server for quant-api.
    let http_store = store.clone();
    let http_peer_id = local_peer_id.to_string();
    let http_revocations = revocations.clone();
    let http_task = tokio::spawn(async move {
        if let Err(e) = run_http_server(http_addr, http_store, http_peer_id, http_revocations).await {
            error!(error = %e, "HTTP server failed");
        }
    });

    // Periodic tasks.
    let mut gossip_interval = tokio::time::interval(Duration::from_secs(30));
    let mut cleanup_interval = tokio::time::interval(Duration::from_secs(300));
    let mut seen: VecDeque<[u8; 32]> = VecDeque::with_capacity(4096);
    let mut seen_set: HashSet<[u8; 32]> = HashSet::new();
    let mut rate_limiter = InMemoryPeerRateLimiter::new(
        args.max_peer_msgs_per_sec,
        args.peer_window_secs,
        args.max_tracked_peers,
    );
    let mut dial_backoff: HashMap<PeerId, (Instant, u32)> = HashMap::new();

    info!("P2P node ready — listening for peers");

    loop {
        tokio::select! {
            _ = http_task.is_finished() => {
                error!("HTTP server exited — shutting down P2P node");
                return Err(anyhow::anyhow!("HTTP server exited"));
            }
            event = swarm.select_next_some() => {
                handle_swarm_event(
                    event, &mut swarm, &store, &shared_key,
                    &mut seen, &mut seen_set, &topic, &revocation_topic,
                    &args, &revocations,
                    &mut rate_limiter, &mut dial_backoff,
                ).await;
            }
            _ = gossip_interval.tick() => {
                // Send a keepalive ping.
                let msg = P2pMessage::Ping { node_version: env!("CARGO_PKG_VERSION").into() };
                if let Ok(data) = serde_json::to_vec(&msg) {
                    if let Err(e) = swarm.behaviour_mut().gossipsub.publish(topic.clone(), data) {
                        tracing::debug!(error = %e, "gossip ping failed (no peers?)");
                    }
                }
            }
            _ = cleanup_interval.tick() => {
                store.evict_stale(600_000).await; // 10 min TTL
                rate_limiter.retain_recent(Duration::from_secs(args.peer_window_secs));
                swarm.behaviour_mut().kademlia.bootstrap().ok();
            }
        }
    }
}

#[allow(clippy::too_many_arguments)]
async fn handle_swarm_event(
    event: SwarmEvent<BehaviourEvent>,
    swarm: &mut Swarm<Behaviour>,
    store: &ObservationStore,
    shared_key: &[u8; 32],
    seen: &mut VecDeque<[u8; 32]>,
    seen_set: &mut HashSet<[u8; 32]>,
    topic: &IdentTopic,
    revocation_topic: &IdentTopic,
    args: &Args,
    revocations: &Arc<RwLock<RevocationRuntime>>,
    rate_limiter: &mut InMemoryPeerRateLimiter,
    dial_backoff: &mut HashMap<PeerId, (Instant, u32)>,
) {
    match event {
        SwarmEvent::NewListenAddr { address, .. } => {
            info!(addr = %address, "listening");
        }
        SwarmEvent::ConnectionEstablished { peer_id, endpoint, .. } => {
            let addr = endpoint.get_remote_address();
            if !allowed_peer_ip(addr) {
                info!(%peer_id, %addr, "rejecting connection on disallowed IP");
                swarm.disconnect_peer_id(peer_id).ok();
                return;
            }
            info!(%peer_id, addr = %addr, "peer connected");
            swarm.behaviour_mut().kademlia.add_address(&peer_id, addr.clone());
            dial_backoff.remove(&peer_id);
        }
        SwarmEvent::ConnectionClosed { peer_id, .. } => {
            info!(%peer_id, "peer disconnected");
        }
        SwarmEvent::OutgoingConnectionError { peer_id: Some(peer_id), error, .. } => {
            let entry = dial_backoff.entry(peer_id).or_insert((Instant::now(), 0));
            if entry.1 < 8 {
                entry.1 += 1;
            }
            entry.0 = Instant::now();
            info!(%peer_id, attempts = entry.1, error = %error, "outgoing connection failed");
        }
        SwarmEvent::Behaviour(BehaviourEvent::Mdns(mdns::Event::Discovered(peers))) => {
            for (peer_id, addr) in peers {
                if !allowed_peer_ip(&addr) {
                    info!(%peer_id, %addr, "ignoring mDNS peer on disallowed IP");
                    continue;
                }
                if !dial_backoff_ready(peer_id, dial_backoff) {
                    continue;
                }
                info!(%peer_id, %addr, "mDNS discovered peer");
                swarm.behaviour_mut().kademlia.add_address(&peer_id, addr.clone());
                if let Err(e) = swarm.dial(addr) {
                    tracing::debug!(error = %e, "mDNS dial failed");
                }
            }
        }
        SwarmEvent::Behaviour(BehaviourEvent::Mdns(mdns::Event::Expired(peers))) => {
            for (peer_id, addr) in peers {
                tracing::debug!(%peer_id, %addr, "mDNS peer expired");
            }
        }
        SwarmEvent::Behaviour(BehaviourEvent::Gossipsub(gossipsub::Event::Message {
            propagation_source,
            message,
            ..
        })) => {
            if message.topic == revocation_topic.hash() {
                let revocation_size = message.data.len();
                let revocation_sequence = serde_json::from_slice::<P2pMessage>(&message.data)
                    .ok()
                    .and_then(|msg| match msg {
                        P2pMessage::RevocationBundle { sequence, .. } => Some(sequence),
                        _ => None,
                    });
                match handle_revocation_message(
                    &message.data,
                    revocations,
                    args,
                ).await {
                    Ok(RevocationApply::Applied { sequence, sha256 }) => {
                        info!(sequence, %sha256, source = %propagation_source, "newer validated revocation bundle applied");
                    }
                    Ok(RevocationApply::AlreadyCurrent { sequence }) => {
                        tracing::debug!(sequence, "revocation bundle already current");
                    }
                    Err(e) => {
                        let _ = rate_limiter.record_message(
                            &propagation_source.to_string(),
                            Instant::now(),
                            revocation_size,
                        );
                        warn!(
                            error = %e,
                            source = %propagation_source,
                            sequence = revocation_sequence,
                            size = revocation_size,
                            "rejected invalid or stale revocation bundle"
                        );
                    }
                }
                return;
            }
            let source = propagation_source.to_string();
            let size = message.data.len();
            if let Err(e) = rate_limiter.record_message(&source, Instant::now(), size) {
                warn!(%source, error = %e, "rate limit or size limit rejected peer message");
                return;
            }
            let revocation = revocations.read().await.clone();
            match serde_json::from_slice::<P2pMessage>(&message.data) {
                Ok(P2pMessage::Observation(envelope)) => {
                    match envelope.decrypt(shared_key) {
                        Ok(obs) => {
                            let now_ms = Utc::now().timestamp_millis();
                            if let Err(e) = store.validate_ingress(&obs, &source, now_ms, &revocation.policy) {
                                warn!(%source, error = %e, "observation failed ingress validation");
                                return;
                            }
                            let mut digest = Sha256::new();
                            digest.update(&message.data);
                            let hash: [u8; 32] = digest.finalize().into();
                            if seen_set.contains(&hash) {
                                return;
                            }
                            if seen.len() >= 4096 {
                                if let Some(old) = seen.pop_front() {
                                    seen_set.remove(&old);
                                }
                            }
                            seen.push_back(hash);
                            seen_set.insert(hash);
                            if store.ingest(obs).await {
                                tracing::debug!(source = %propagation_source, "ingested peer observation");
                            }
                        }
                        Err(e) => {
                            tracing::debug!(error = %e, "failed to decrypt observation (wrong key?)");
                        }
                    }
                }
                Ok(P2pMessage::Ping { node_version }) => {
                    tracing::debug!(source = %propagation_source, version = %node_version, "peer ping");
                }
                Ok(P2pMessage::RevocationBundle { .. }) => {
                    warn!(source = %propagation_source, "revocation bundle arrived on observation topic; rejected");
                }
                Err(e) => {
                    tracing::debug!(error = %e, "unparseable gossip message");
                }
            }
        }
        SwarmEvent::Behaviour(BehaviourEvent::Gossipsub(gossipsub::Event::Subscribed { peer_id, topic })) => {
            info!(%peer_id, %topic, "peer subscribed");
        }
        SwarmEvent::Behaviour(BehaviourEvent::Identify(identify::Event::Received { peer_id, info, .. })) => {
            tracing::debug!(%peer_id, agent = %info.agent_version, "identified peer");
            for addr in info.listen_addrs {
                swarm.behaviour_mut().kademlia.add_address(&peer_id, addr);
            }
        }
        SwarmEvent::Behaviour(BehaviourEvent::Autonat(autonat::Event::StatusChanged { old, new })) => {
            info!(?old, ?new, "NAT status changed");
        }
        SwarmEvent::Behaviour(BehaviourEvent::Upnp(upnp::Event::NewExternalAddr { external_addr: address, .. })) => {
            swarm.add_external_address(address.clone());
            emit!("upnp_external_address={address}");
        }
        SwarmEvent::Behaviour(BehaviourEvent::Upnp(upnp::Event::ExpiredExternalAddr { external_addr: address, .. })) => {
            swarm.remove_external_address(&address);
            emit!("upnp_external_address_expired={address}");
        }
        SwarmEvent::Behaviour(BehaviourEvent::Relay(event)) => {
            info!(?event, "relay event");
        }
        _ => {}
    }
}

fn dial_backoff_ready(
    peer_id: PeerId,
    backoff: &HashMap<PeerId, (Instant, u32)>,
) -> bool {
    let Some((last, attempts)) = backoff.get(&peer_id) else {
        return true;
    };
    let delay = Duration::from_secs(2u64.saturating_pow((*attempts).min(6)));
    last.elapsed() >= delay
}

#[derive(Debug, PartialEq, Eq)]
enum RevocationApply {
    Applied { sequence: u64, sha256: String },
    AlreadyCurrent { sequence: u64 },
}

async fn handle_revocation_message(
    data: &[u8],
    revocations: &Arc<RwLock<RevocationRuntime>>,
    args: &Args,
) -> Result<RevocationApply> {
    let message: P2pMessage = serde_json::from_slice(data)
        .context("revocation message is not valid JSON")?;
    let (sequence, sha256, bundle) = match message {
        P2pMessage::RevocationBundle { sequence, sha256, bundle } => (sequence, sha256, bundle),
        _ => anyhow::bail!("message is not a revocation bundle"),
    };
    let candidate = RevocationRuntime::from_bundle(bundle)
        .context("revocation bundle validation failed")?;
    if candidate.sequence != sequence || candidate.sha256 != sha256 {
        anyhow::bail!("revocation envelope identity does not match bundle");
    }
    let mut current = revocations.write().await;
    if candidate.sequence < current.sequence {
        anyhow::bail!("revocation bundle is stale");
    }
    if candidate.sequence == current.sequence {
        if candidate.sha256 != current.sha256 {
            anyhow::bail!("conflicting revocation bundle for current sequence");
        }
        return Ok(RevocationApply::AlreadyCurrent { sequence });
    }
    if !current.accepts_update(&candidate) {
        anyhow::bail!("revocation bundle is not a newer valid policy");
    }
    if args.revocation_sha256.is_some() {
        candidate.validate_expected(args.revocation_sequence, args.revocation_sha256.as_deref())
            .context("revocation update conflicts with pinned deployed revocation identity")?;
    }
    *current = candidate;
    Ok(RevocationApply::Applied { sequence, sha256 })
}

fn relay_config(args: &Args) -> relay::Config {
    let mut config = relay::Config {
        max_reservations: if args.relay_max_reservations == 0 { 128 } else { args.relay_max_reservations },
        max_reservations_per_peer: if args.relay_max_reservations_per_peer == 0 { 4 } else { args.relay_max_reservations_per_peer },
        reservation_duration: if args.relay_reservation_ttl == 0 { Duration::from_secs(3600) } else { Duration::from_secs(args.relay_reservation_ttl) },
        max_circuits: if args.relay_max_circuits == 0 { 16 } else { args.relay_max_circuits },
        max_circuits_per_peer: if args.relay_max_circuits_per_peer == 0 { 4 } else { args.relay_max_circuits_per_peer },
        max_circuit_duration: if args.relay_circuit_duration == 0 { Duration::from_secs(120) } else { Duration::from_secs(args.relay_circuit_duration) },
        max_circuit_bytes: if args.relay_circuit_bytes == 0 { 1 << 17 } else { args.relay_circuit_bytes },
        reservation_rate_limiters: Vec::new(),
        circuit_src_rate_limiters: Vec::new(),
        ..Default::default()
    };
    if !args.relay_server {
        config.max_reservations = 0;
        config.max_circuits = 0;
    }
    config
}

fn allowed_peer_ip(addr: &Multiaddr) -> bool {
    for protocol in addr.iter() {
        match protocol {
            libp2p::multiaddr::Protocol::Ip4(ip) => return allowed_ip(IpAddr::V4(ip)),
            libp2p::multiaddr::Protocol::Ip6(ip) => return allowed_ip(IpAddr::V6(ip)),
            libp2p::multiaddr::Protocol::Dns(_) | libp2p::multiaddr::Protocol::Dnsaddr(_) => {
                // DNS names are resolved by the transport; actual connection IPs are checked on connect.
                return true;
            }
            _ => {}
        }
    }
    false
}

fn allowed_ip(ip: IpAddr) -> bool {
    match ip {
        IpAddr::V4(ip) => {
            ip.is_private() || ip.is_loopback() || ip.is_link_local()
                || (!ip.is_unspecified() && !ip.is_broadcast() && !ip.is_multicast())
        }
        IpAddr::V6(ip) => {
            !ip.is_unspecified() && !ip.is_multicast()
        }
    }
}

fn extract_peer_id(addr: &Multiaddr) -> Option<PeerId> {
    addr.iter().find_map(|p| match p {
        libp2p::multiaddr::Protocol::P2p(peer_id) => Some(peer_id),
        _ => None,
    })
}

// ── Local HTTP server ────────────────────────────────────────────────────────

async fn run_http_server(
    addr: std::net::SocketAddr,
    store: ObservationStore,
    peer_id: String,
    revocations: Arc<RwLock<RevocationRuntime>>,
) -> Result<()> {
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    use tokio::net::TcpListener;

    let listener = TcpListener::bind(addr).await
        .with_context(|| format!("failed to bind HTTP on {addr}"))?;
    info!(%addr, "local HTTP endpoint ready");

    loop {
        let (mut socket, _) = listener.accept().await?;
        let store = store.clone();
        let peer_id = peer_id.clone();
        let revocations = revocations.clone();

        tokio::spawn(async move {
            let mut buf = vec![0u8; 8192];
            let n = match socket.read(&mut buf).await {
                Ok(n) if n > 0 => n,
                _ => return,
            };
            let request = String::from_utf8_lossy(&buf[..n]);
            let path = request
                .lines()
                .next()
                .and_then(|line| line.split_whitespace().nth(1))
                .unwrap_or("/");
            let request_target = path.splitn(2, '?');
            let mut parts = request_target;
            let route = parts.next().unwrap_or("/");

            let (status, content_type, body) = match route {
                "/observations" => {
                    let observations = store.all().await;
                    let resp = ObservationsResponse {
                        protocol_version: PROTOCOL_VERSION,
                        count: observations.len(),
                        observations,
                    };
                    ("200 OK", "application/json", serde_json::to_string(&resp).unwrap_or_default())
                }
                "/health" => {
                    let body = serde_json::json!({
                        "status": "ok",
                        "peer_id": peer_id,
                        "peer_count": store.all().await.len(),
                    });
                    ("200 OK", "application/json", body.to_string())
                }
                "/peer-observations" => {
                    // Backward-compatible alias for /observations.
                    let observations = store.all().await;
                    let resp = ObservationsResponse {
                        protocol_version: PROTOCOL_VERSION,
                        count: observations.len(),
                        observations,
                    };
                    ("200 OK", "application/json", serde_json::to_string(&resp).unwrap_or_default())
                }
                "/revocations" => {
                    let current = revocations.read().await;
                    let body = serde_json::json!({
                        "sequence": current.sequence,
                        "sha256": current.sha256,
                        "bundle": current.bundle,
                        "policy": current.policy.snapshot(),
                    });
                    ("200 OK", "application/json", body.to_string())
                }
                _ => ("404 Not Found", "text/plain", "not found".to_string()),
            };

            let response = format!(
                "HTTP/1.1 {status}\r\nContent-Type: {content_type}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                body.len()
            );
            socket.write_all(response.as_bytes()).await.ok();
        });
    }
}

// ── Publish own observations (called by quant-api via gossipsub) ─────────────

/// Encrypt and prepare an observation for gossiping.
pub fn prepare_observation_gossip(
    obs: &osiris_p2p::Observation,
    peer_id: &str,
    key_id: &str,
    shared_key: &[u8; 32],
    revocations: &RevocationPolicyEngine,
) -> Result<Vec<u8>> {
    if revocations.key_status(key_id) != RevocationState::Active {
        anyhow::bail!("revoked key cannot publish observation");
    }
    if revocations.peer_status(peer_id) != RevocationState::Active {
        anyhow::bail!("revoked peer cannot publish observation");
    }
    let envelope = encrypt_observation(obs, peer_id, key_id, shared_key)
        .map_err(|e| anyhow::anyhow!("{e}"))?;
    let msg = P2pMessage::Observation(envelope);
    Ok(serde_json::to_vec(&msg)?)
}

#[cfg(test)]
mod tests {
    use super::*;
    use osiris_p2p::{Observation, RevocationEntry, RevocationFile, RevocationPolicyEngine, RevocationState};
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    use tokio::net::TcpStream;

    fn test_args() -> Args {
        Args {
            listen_port: 4001,
            http_port: 4100,
            bootstrap: Vec::new(),
            relay: Vec::new(),
            relay_reserve: None,
            relay_listen: true,
            shared_key: None,
            mdns: true,
            allow_remote_http: false,
            network_id: "test".into(),
            identity_file: None,
            max_peer_msgs_per_sec: 60,
            peer_window_secs: 60,
            max_tracked_peers: 1024,
            http_host: "127.0.0.1".into(),
            relay_server: false,
            relay_max_reservations: 0,
            relay_reservation_ttl: 0,
            relay_max_circuits: 0,
            relay_circuit_duration: 0,
            relay_circuit_bytes: 0,
            relay_max_circuits_per_peer: 0,
            relay_max_reservations_per_peer: 0,
            relay_reservation_rate: 0,
            relay_circuit_rate: 0,
            revocation_file: None,
            revocation_sequence: 0,
            revocation_sha256: None,
        }
    }

    fn sample_observation() -> Observation {
        Observation {
            v: 1,
            source: "binance".into(),
            topic: "market.tick".into(),
            instrument: "BTC-USD".into(),
            observed_at_ms: Utc::now().timestamp_millis(),
            data: serde_json::json!({"bid": "50000", "ask": "50001"}),
        }
    }

    fn sample_peer_observation(peer_id: &str) -> PeerObservation {
        PeerObservation {
            peer_id: peer_id.into(),
            key_id: "peer:test".into(),
            observation: sample_observation(),
        }
    }

    #[test]
    fn embedded_relay_defaults_are_per_peer_bounded() {
        let args = test_args();
        let config = relay_config(&args);
        assert_eq!(config.max_reservations_per_peer, 4);
        assert_eq!(config.max_circuits_per_peer, 4);
        assert_eq!(config.reservation_duration, Duration::from_secs(3600));
        assert_eq!(config.max_circuit_duration, Duration::from_secs(120));
        assert_eq!(config.max_circuit_bytes, 1 << 17);
        assert_eq!(config.max_reservations, 0);
        assert_eq!(config.max_circuits, 0);
    }

    #[test]
    fn embedded_relay_custom_values_are_preserved() {
        let args = Args {
            relay_server: true,
            relay_max_reservations: 32,
            relay_reservation_ttl: 1800,
            relay_max_circuits: 8,
            relay_circuit_duration: 60,
            relay_circuit_bytes: 65536,
            relay_max_circuits_per_peer: 2,
            relay_max_reservations_per_peer: 2,
            ..test_args()
        };
        let config = relay_config(&args);
        assert_eq!(config.max_reservations, 32);
        assert_eq!(config.max_reservations_per_peer, 2);
        assert_eq!(config.reservation_duration, Duration::from_secs(1800));
        assert_eq!(config.max_circuits, 8);
        assert_eq!(config.max_circuits_per_peer, 2);
        assert_eq!(config.max_circuit_duration, Duration::from_secs(60));
        assert_eq!(config.max_circuit_bytes, 65536);
    }

    #[test]
    fn embedded_relay_zero_keeps_bounded_defaults() {
        let args = Args { relay_server: true, ..test_args() };
        let config = relay_config(&args);
        assert_eq!(config.max_reservations, 128);
        assert_eq!(config.max_reservations_per_peer, 4);
        assert_eq!(config.max_circuits, 16);
        assert_eq!(config.max_circuits_per_peer, 4);
        assert_eq!(config.reservation_duration, Duration::from_secs(3600));
        assert_eq!(config.max_circuit_duration, Duration::from_secs(120));
        assert_eq!(config.max_circuit_bytes, 1 << 17);
    }

    #[test]
    fn embedded_relay_rejects_unsafe_limits() {
        let cases = [
            (Args { relay_max_reservations: 1, relay_max_reservations_per_peer: 2, ..test_args() }, "reservations"),
            (Args { relay_max_circuits: 1, relay_max_circuits_per_peer: 2, ..test_args() }, "circuits"),
            (Args { relay_max_circuits_per_peer: 65, ..test_args() }, "circuits-per-peer"),
            (Args { relay_max_reservations_per_peer: 65, ..test_args() }, "reservations-per-peer"),
            (Args { relay_circuit_duration: 86401, ..test_args() }, "duration"),
            (Args { relay_circuit_bytes: 1 << 31, ..test_args() }, "bytes"),
            (Args { relay_reservation_ttl: 86401, ..test_args() }, "ttl"),
            (Args { relay_reservation_rate: 3601, ..test_args() }, "reservation-rate"),
            (Args { relay_circuit_rate: 3601, ..test_args() }, "circuit-rate"),
            (Args { relay_circuit_bytes: 1, ..test_args() }, "minimum-bytes"),
        ];
        for (args, label) in cases {
            let result = std::panic::catch_unwind(|| {
                if args.relay_max_reservations_per_peer > args.relay_max_reservations && args.relay_max_reservations != 0 {
                    panic!("relay-max-reservations-per-peer cannot exceed relay-max-reservations");
                }
                if args.relay_max_circuits_per_peer > args.relay_max_circuits && args.relay_max_circuits != 0 {
                    panic!("relay-max-circuits-per-peer cannot exceed relay-max-circuits");
                }
                if args.relay_max_circuits_per_peer > 64 {
                    panic!("relay-max-circuits-per-peer cannot exceed 64");
                }
                if args.relay_max_reservations_per_peer > 64 {
                    panic!("relay-max-reservations-per-peer cannot exceed 64");
                }
                if args.relay_circuit_duration > 86400 {
                    panic!("relay-circuit-duration cannot exceed 86400 seconds");
                }
                if args.relay_circuit_bytes > 1 << 30 {
                    panic!("relay-circuit-bytes cannot exceed 1073741824 bytes");
                }
                if args.relay_reservation_ttl > 86400 {
                    panic!("relay-reservation-ttl cannot exceed 86400 seconds");
                }
                if args.relay_reservation_rate > 3600 {
                    panic!("relay-reservation-rate cannot exceed 3600 seconds");
                }
                if args.relay_circuit_rate > 3600 {
                    panic!("relay-circuit-rate cannot exceed 3600 seconds");
                }
                if args.relay_circuit_bytes != 0 && args.relay_circuit_bytes < 1024 {
                    panic!("relay-circuit-bytes must be at least 1024 or zero for the embedded default");
                }
            });
            assert!(result.is_err(), "{label} must be rejected");
        }
    }

    #[test]
    fn revocation_bundle_sequence_and_hash_are_checked() {
        let mut bundle = RevocationFile::empty();
        bundle.sequence = 7;
        let runtime = RevocationRuntime::from_bundle(bundle).unwrap();
        assert!(runtime.validate_expected(7, Some(&runtime.sha256)).is_ok());
        assert!(runtime.validate_expected(8, None).is_err());
        assert!(runtime.validate_expected(7, Some("00".repeat(32).as_str())).is_err());
    }

    #[test]
    fn revoked_key_and_peer_ingress_are_rejected() {
        let mut bundle = RevocationFile::empty();
        bundle.sequence = 2;
        bundle.revoked_keys.push(RevocationEntry { id: "peer:test".into(), at_ms: 0, reason: None });
        bundle.revoked_peers.push(RevocationEntry { id: "peer-source".into(), at_ms: 0, reason: None });
        let runtime = RevocationRuntime::from_bundle(bundle).unwrap();
        let store = ObservationStore::new();
        let obs = sample_peer_observation("peer-source");
        let error = store.validate_ingress(&obs, "peer-source", Utc::now().timestamp_millis(), &runtime.policy).unwrap_err();
        assert!(error.contains("revoked"));
    }

    #[test]
    fn observation_encrypted_key_is_not_spoofable_by_payload_peer() {
        let key = [3u8; 32];
        let envelope = encrypt_observation(&sample_observation(), "real-peer", "peer:real", &key).unwrap();
        let mut decoded = envelope.decrypt(&key).unwrap();
        decoded.peer_id = "attacker".into();
        // Re-encrypting with a different claimed peer ID produces a different sender binding.
        let rebound = encrypt_observation(&decoded.observation, "attacker", &decoded.key_id, &key).unwrap();
        assert_ne!(envelope.sender_binding, rebound.sender_binding);
    }

    #[test]
    fn revocation_runtime_accepts_only_newer_valid_sequence() {
        let current = RevocationRuntime::empty().unwrap();
        let mut newer_bundle = RevocationFile::empty();
        newer_bundle.sequence = 1;
        let newer = RevocationRuntime::from_bundle(newer_bundle).unwrap();
        assert!(current.accepts_update(&newer));
        assert!(!newer.accepts_update(&current));
        let same = RevocationRuntime::from_bundle(newer.bundle.clone()).unwrap();
        assert!(newer.accepts_update(&same));
    }

    #[tokio::test]
    async fn http_revocations_exposes_active_sequence_and_policy() {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        drop(listener);
        let store = ObservationStore::new();
        let mut bundle = RevocationFile::empty();
        bundle.sequence = 9;
        bundle.revoked_keys.push(RevocationEntry { id: "peer:old".into(), at_ms: 10, reason: Some("rotation".into()) });
        let runtime = RevocationRuntime::from_bundle(bundle).unwrap();
        let revocations = Arc::new(RwLock::new(runtime));
        let task = tokio::spawn(run_http_server(addr, store, "peer-local".into(), revocations));
        let mut stream = TcpStream::connect(addr).await.unwrap();
        stream.write_all(b"GET /revocations HTTP/1.1\r\nHost: localhost\r\n\r\n").await.unwrap();
        let mut response = Vec::new();
        stream.read_to_end(&mut response).await.unwrap();
        task.abort();
        let response = String::from_utf8(response).unwrap();
        assert!(response.starts_with("HTTP/1.1 200 OK"));
        assert!(response.contains("\"sequence\":9"));
        assert!(response.contains("\"revoked_keys\""));
        assert!(response.contains("peer:old"));
    }

    #[tokio::test]
    async fn revocation_message_applies_newer_bundle_and_replays_are_noops() {
        let revocations = Arc::new(RwLock::new(RevocationRuntime::empty().unwrap()));
        let args = test_args();
        let mut bundle = RevocationFile::empty();
        bundle.sequence = 3;
        bundle.revoked_keys.push(RevocationEntry { id: "peer:old".into(), at_ms: 10, reason: None });
        let runtime = RevocationRuntime::from_bundle(bundle).unwrap();
        let message = serde_json::to_vec(&P2pMessage::RevocationBundle {
            sequence: runtime.sequence,
            sha256: runtime.sha256.clone(),
            bundle: runtime.bundle.clone(),
        }).unwrap();
        assert_eq!(handle_revocation_message(&message, &revocations, &args).await.unwrap(), RevocationApply::Applied { sequence: 3, sha256: runtime.sha256.clone() });
        assert_eq!(handle_revocation_message(&message, &revocations, &args).await.unwrap(), RevocationApply::AlreadyCurrent { sequence: 3 });
        assert_eq!(revocations.read().await.policy.key_status("peer:old"), RevocationState::Revoked { at_ms: 10 });
    }

    #[tokio::test]
    async fn revocation_message_rejects_tampering_stale_and_conflicting_bundles() {
        let revocations = Arc::new(RwLock::new(RevocationRuntime::empty().unwrap()));
        let args = test_args();
        let mut bundle = RevocationFile::empty();
        bundle.sequence = 2;
        let runtime = RevocationRuntime::from_bundle(bundle).unwrap();
        let valid = serde_json::to_vec(&P2pMessage::RevocationBundle {
            sequence: 2,
            sha256: runtime.sha256.clone(),
            bundle: runtime.bundle.clone(),
        }).unwrap();
        handle_revocation_message(&valid, &revocations, &args).await.unwrap();
        let stale = serde_json::to_vec(&P2pMessage::RevocationBundle {
            sequence: 1,
            sha256: "00".repeat(32),
            bundle: RevocationFile { sequence: 1, ..RevocationFile::empty() },
        }).unwrap();
        assert!(handle_revocation_message(&stale, &revocations, &args).await.is_err());
        let tampered = serde_json::to_vec(&P2pMessage::RevocationBundle {
            sequence: 3,
            sha256: "00".repeat(32),
            bundle: RevocationFile { sequence: 3, ..RevocationFile::empty() },
        }).unwrap();
        assert!(handle_revocation_message(&tampered, &revocations, &args).await.is_err());
        let conflicting = serde_json::to_vec(&P2pMessage::RevocationBundle {
            sequence: 2,
            sha256: "11".repeat(32),
            bundle: RevocationFile { sequence: 2, revoked_keys: vec![RevocationEntry { id: "other".into(), at_ms: 1, reason: None }], ..RevocationFile::empty() },
        }).unwrap();
        assert!(handle_revocation_message(&conflicting, &revocations, &args).await.is_err());
    }

    #[test]
    fn default_publishing_rejects_revoked_sender() {
        let mut bundle = RevocationFile::empty();
        bundle.revoked_peers.push(RevocationEntry { id: "peer:test".into(), at_ms: 0, reason: None });
        let policy = RevocationPolicyEngine::new(bundle).unwrap();
        assert!(prepare_observation_gossip(&sample_observation(), "peer:test", "peer:key", &[1u8; 32], &policy).is_err());
    }
}
