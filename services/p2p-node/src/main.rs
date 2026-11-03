use std::{env, error::Error, fs, io::Write, path::PathBuf, time::Duration};

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
use tokio::io::{self, AsyncBufReadExt, BufReader};

const PROTOCOL_VERSION: &str = "/osiris-eye/p2p/1.0.0";
const TOPIC_NAME: &str = "osiris/observations/1";

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
    #[test] fn accepts_valid_envelope() { assert!(valid().validate().is_ok()); }
    #[test] fn rejects_wrong_schema() { let mut v=valid(); v.schema="v0".into(); assert_eq!(v.validate(),Err("unsupported_schema")); }
    #[test] fn rejects_bad_digest() { let mut v=valid(); v.payload_sha256="xyz".into(); assert_eq!(v.validate(),Err("invalid_payload_sha256")); }
    #[test] fn rejects_empty_instrument() { let mut v=valid(); v.instrument.clear(); assert_eq!(v.validate(),Err("invalid_instrument")); }
    #[test] fn rejects_payload_hash_mismatch() { let mut v=valid(); v.payload.push_str("-tampered"); assert_eq!(v.validate(),Err("payload_hash_mismatch")); }
    #[test] fn rejects_oversized_payload() { let mut v=valid(); v.payload="x".repeat(1_048_577); assert_eq!(v.validate(),Err("payload_too_large")); }
    #[test] fn rejects_empty_provider() { let mut v=valid(); v.provider.clear(); assert_eq!(v.validate(),Err("invalid_provider")); }
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

fn load_or_create_identity() -> Result<identity::Keypair, Box<dyn Error>> {
    let path = env::var_os("OSIRIS_NODE_KEY")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(".osiris-node-key"));

    if path.exists() {
        let encoded = fs::read(&path)?;
        return Ok(identity::Keypair::from_protobuf_encoding(&encoded)?);
    }

    let key = identity::Keypair::generate_ed25519();
    let encoded = key.to_protobuf_encoding()?;
    fs::write(&path, encoded)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&path, fs::Permissions::from_mode(0o600))?;
    }
    eprintln!("Created node identity at {} (keep this file private)", path.display());
    Ok(key)
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
    let mut reconnect_tick = tokio::time::interval(Duration::from_secs(15));
    reconnect_tick.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
    emit!("OSIRIS EYE P2P node starting");
    emit!("peer_id={peer_id}");
    emit!("topic={TOPIC_NAME}");
    emit!("bootstrap_peers={}", bootstrap_peers.len());
    emit!("bootstrap_reconnect_interval_seconds=15");
    emit!("Live trading and broker order submission are not part of this node.");

    let stdin = BufReader::new(io::stdin());
    let mut lines = stdin.lines();

    loop {
        tokio::select! {
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
                SwarmEvent::NewListenAddr { address, .. } => {
                    emit!("listening={address}/p2p/{peer_id}");
                }
                SwarmEvent::ConnectionEstablished { peer_id, .. } => {
                    emit!("peer_connected={peer_id}");
                }
                SwarmEvent::ConnectionClosed { peer_id, .. } => {
                    emit!("peer_disconnected={peer_id}");
                }
                SwarmEvent::Behaviour(BehaviourEvent::Mdns(mdns::Event::Discovered(peers))) => {
                    for (peer, address) in peers {
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
                    emit!("gossipsub_subscribed peer={peer_id}");
                }
                SwarmEvent::Behaviour(BehaviourEvent::Gossipsub(gossipsub::Event::Message {
                    propagation_source, message, ..
                })) => {
                    match serde_json::from_slice::<ObservationEnvelope>(&message.data) {
                        Ok(envelope) if envelope.validate().is_ok() => {
                            emit!(
                                "observation_received peer={} instrument={} provider={} observed_at={} payload_sha256={} payload_bytes={}",
                                propagation_source, envelope.instrument, envelope.provider,
                                envelope.observed_at, envelope.payload_sha256, envelope.payload.len()
                            );
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
            line = lines.next_line() => match line? {
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
                    tokio::signal::ctrl_c().await?;
                    break;
                }
            }
        }
    }

    Ok(())
}
