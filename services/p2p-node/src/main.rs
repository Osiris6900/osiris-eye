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
}

impl ObservationEnvelope {
    fn validate(&self) -> Result<(), &'static str> {
        if self.schema != "osiris.observation.v1" { return Err("unsupported_schema"); }
        if self.instrument.trim().is_empty() || self.instrument.len() > 64 { return Err("invalid_instrument"); }
        if self.provider.trim().is_empty() || self.provider.len() > 64 { return Err("invalid_provider"); }
        if self.observed_at.trim().is_empty() || self.observed_at.len() > 40 { return Err("invalid_observed_at"); }
        let digest = self.payload_sha256.as_bytes();
        if digest.len() != 64 || !digest.iter().all(u8::is_ascii_hexdigit) { return Err("invalid_payload_sha256"); }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::ObservationEnvelope;
    fn valid() -> ObservationEnvelope {
        ObservationEnvelope { schema: "osiris.observation.v1".into(), instrument: "BTC-USD".into(),
            observed_at: "2026-11-03T00:00:00Z".into(), provider: "coinbase-public".into(),
            payload_sha256: "a".repeat(64) }
    }
    #[test] fn accepts_valid_envelope() { assert!(valid().validate().is_ok()); }
    #[test] fn rejects_wrong_schema() { let mut v=valid(); v.schema="v0".into(); assert_eq!(v.validate(),Err("unsupported_schema")); }
    #[test] fn rejects_bad_digest() { let mut v=valid(); v.payload_sha256="xyz".into(); assert_eq!(v.validate(),Err("invalid_payload_sha256")); }
    #[test] fn rejects_empty_instrument() { let mut v=valid(); v.instrument.clear(); assert_eq!(v.validate(),Err("invalid_instrument")); }
    #[test] fn rejects_empty_provider() { let mut v=valid(); v.provider.clear(); assert_eq!(v.validate(),Err("invalid_provider")); }
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

fn parse_bootstrap_peers(
    swarm: &mut Swarm<Behaviour>,
) -> Result<usize, Box<dyn Error>> {
    let Some(raw) = env::var_os("OSIRIS_BOOTSTRAP_PEERS") else {
        return Ok(0);
    };

    let mut count = 0;
    for entry in raw.to_string_lossy().split(',').map(str::trim).filter(|s| !s.is_empty()) {
        let address: Multiaddr = entry.parse()?;
        let peer = address.iter().find_map(|protocol| match protocol {
            libp2p::multiaddr::Protocol::P2p(peer_id) => Some(peer_id),
            _ => None,
        }).ok_or("bootstrap multiaddr must end with /p2p/<peer-id>")?;
        swarm.behaviour_mut().kademlia.add_address(&peer, address.clone());
        swarm.dial(address)?;
        count += 1;
    }
    if count > 0 {
        let _ = swarm.behaviour_mut().kademlia.bootstrap();
    }
    Ok(count)
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn Error>> {
    let key = load_or_create_identity()?;
    let peer_id = PeerId::from(key.public());

    let mut gossipsub_config = gossipsub::ConfigBuilder::default()
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

    let listen: Multiaddr = env::var("OSIRIS_LISTEN_ADDR")
        .unwrap_or_else(|_| "/ip4/0.0.0.0/tcp/0".to_owned())
        .parse()?;
    swarm.listen_on(listen)?;

    let bootstrap_count = parse_bootstrap_peers(&mut swarm)?;
    emit!("OSIRIS EYE P2P node starting");
    emit!("peer_id={peer_id}");
    emit!("topic={TOPIC_NAME}");
    emit!("bootstrap_peers={bootstrap_count}");
    emit!("Live trading and broker order submission are not part of this node.");

    let stdin = BufReader::new(io::stdin());
    let mut lines = stdin.lines();

    loop {
        tokio::select! {
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
                        swarm.behaviour_mut().kademlia.add_address(&peer, address);
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
                                "observation_received peer={} instrument={} provider={} observed_at={} payload_sha256={}",
                                propagation_source, envelope.instrument, envelope.provider,
                                envelope.observed_at, envelope.payload_sha256
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
