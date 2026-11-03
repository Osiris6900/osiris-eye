use std::{env, error::Error, fs, io::Write, path::PathBuf, time::Duration};

use futures::StreamExt;
use libp2p::{
    core::{transport::OrTransport, upgrade, Transport},
    gossipsub, identity, noise, relay,
    swarm::{Config, NetworkBehaviour, Swarm, SwarmEvent},
    tcp, yamux, Multiaddr, PeerId,
};

const TOPIC: &str = "osiris/observations/1";

fn load_or_create_identity(role: &str) -> Result<identity::Keypair, Box<dyn Error>> {
    let default_name = format!(".osiris-relay-{role}-key");
    let path = env::var_os("OSIRIS_RELAY_CLIENT_KEY")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(default_name));

    if path.exists() {
        let encoded = fs::read(&path)?;
        return Ok(identity::Keypair::from_protobuf_encoding(&encoded)?);
    }

    let key = load_or_create_identity(&role)?;
    fs::write(&path, key.to_protobuf_encoding()?)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&path, fs::Permissions::from_mode(0o600))?;
    }
    eprintln!("Created relay-client identity at {} (keep this file private)", path.display());
    Ok(key)
}

macro_rules! emit {
    ($($arg:tt)*) => {{
        println!($($arg)*);
        let _ = std::io::stdout().flush();
    }};
}

#[derive(NetworkBehaviour)]
struct Behaviour {
    relay: relay::client::Behaviour,
    gossipsub: gossipsub::Behaviour,
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn Error>> {
    let role = env::var("OSIRIS_RELAY_CLIENT_ROLE").unwrap_or_else(|_| "receiver".into());
    if role != "receiver" && role != "sender" {
        return Err("OSIRIS_RELAY_CLIENT_ROLE must be receiver or sender".into());
    }

    let key = identity::Keypair::generate_ed25519();
    let peer_id = PeerId::from(key.public());
    let (relay_transport, relay_behaviour) = relay::client::new(peer_id);
    let tcp_transport = tcp::tokio::Transport::new(tcp::Config::default());
    let transport = OrTransport::new(relay_transport, tcp_transport)
        .upgrade(upgrade::Version::V1)
        .authenticate(noise::Config::new(&key)?)
        .multiplex(yamux::Config::default())
        .boxed();

    let config = gossipsub::ConfigBuilder::default()
        .heartbeat_interval(Duration::from_secs(1))
        .validation_mode(gossipsub::ValidationMode::Strict)
        .build()?;
    let mut gossipsub = gossipsub::Behaviour::new(
        gossipsub::MessageAuthenticity::Signed(key.clone()),
        config,
    )?;
    let topic = gossipsub::IdentTopic::new(TOPIC);
    gossipsub.subscribe(&topic)?;

    let behaviour = Behaviour { relay: relay_behaviour, gossipsub };
    let mut swarm = Swarm::new(
        transport,
        behaviour,
        peer_id,
        Config::with_tokio_executor()
            .with_idle_connection_timeout(Duration::from_secs(120)),
    );
    let relay_addr: Multiaddr = env::var("OSIRIS_RELAY_MULTIADDR")?.parse()?;
    let relay_peer = relay_addr.iter().find_map(|p| match p {
        libp2p::multiaddr::Protocol::P2p(id) => Some(id),
        _ => None,
    }).ok_or("relay multiaddr must include /p2p/<relay-peer-id>")?;

    // Establish the ordinary relay-server connection first. Only request a
    // reservation or circuit after that connection is established. This is
    // the ordering used by libp2p's reference Circuit Relay client example.
    swarm.dial(relay_addr.clone())?;
    emit!("relay_client_role={role}");
    emit!("peer_id={peer_id}");
    emit!("relay_peer_id={relay_peer}");

    let target = if role == "sender" {
        Some(env::var("OSIRIS_RELAY_TARGET_PEER")?.parse::<PeerId>()?)
    } else {
        None
    };
    let mut relay_connected = false;
    let mut receiver_reservation_accepted = false;
    let mut sender_circuit_started = false;
    let mut sender_dial_attempts = 0_u8;
    let mut published = false;

    loop {
        match swarm.select_next_some().await {
            SwarmEvent::ConnectionEstablished { peer_id: connected_peer, endpoint, .. } => {
                emit!("peer_connected={connected_peer} relayed={}", endpoint.is_relayed());
                if connected_peer == relay_peer && !relay_connected {
                    relay_connected = true;
                    if role == "receiver" {
                        let circuit_addr = relay_addr.clone()
                            .with(libp2p::multiaddr::Protocol::P2pCircuit);
                        swarm.listen_on(circuit_addr)?;
                        emit!("relay_reservation_requested=true");
                    } else if let Some(target_peer) = target {
                        let circuit_addr = relay_addr.clone()
                            .with(libp2p::multiaddr::Protocol::P2pCircuit)
                            .with(libp2p::multiaddr::Protocol::P2p(target_peer));
                        swarm.dial(circuit_addr)?;
                        sender_circuit_started = true;
                        emit!("relay_circuit_dial_started=true");
                    }
                }
            }
            SwarmEvent::NewListenAddr { address, .. } => {
                emit!("relay_reserved_address={address}");
            }
            SwarmEvent::Behaviour(BehaviourEvent::Relay(
                relay::client::Event::ReservationReqAccepted { relay_peer_id: accepted_peer, renewal, .. }
            )) => {
                emit!("relay_client_event=ReservationReqAccepted relay_peer_id={accepted_peer} renewal={renewal}");
                if role == "receiver" && accepted_peer == relay_peer && !renewal {
                    receiver_reservation_accepted = true;
                    emit!("relay_reservation_accepted=true");
                }
            }
            SwarmEvent::Behaviour(BehaviourEvent::Relay(event)) => {
                emit!("relay_client_event={event:?}");
            }
            SwarmEvent::Behaviour(BehaviourEvent::Gossipsub(gossipsub::Event::Subscribed { peer_id, .. })) => {
                emit!("gossipsub_subscribed peer={peer_id}");
                if role == "sender" && !published {
                    let payload = r#"{ "schema":"osiris.observation.v1","instrument":"BTC-USD","provider":"relay-test","payload":"three-process-relay-ok" }"#.to_string();
                    swarm.behaviour_mut().gossipsub.publish(topic.clone(), payload.into_bytes())?;
                    published = true;
                    emit!("relay_observation_published=true");
                }
            }
            SwarmEvent::Behaviour(BehaviourEvent::Gossipsub(gossipsub::Event::Message { propagation_source, message, .. })) => {
                let payload = String::from_utf8_lossy(&message.data);
                if payload.contains("three-process-relay-ok") {
                    emit!("relay_observation_received peer={propagation_source} payload={payload}");
                    return Ok(());
                }
            }
            SwarmEvent::ListenerError { listener_id, error } => {
                emit!("relay_listener_error listener_id={listener_id:?} error={error}");
            }
            SwarmEvent::ListenerClosed { listener_id, reason, .. } => {
                emit!("relay_listener_closed listener_id={listener_id:?} reason={reason:?}");
            }
            SwarmEvent::ConnectionClosed { peer_id: closed_peer, cause, .. } => {
                emit!("connection_closed peer={closed_peer} cause={cause:?}");
            }
            SwarmEvent::OutgoingConnectionError { peer_id: failed_peer, error, .. } => {
                emit!("connection_error peer={failed_peer:?} error={error}");
                if role == "sender" && sender_circuit_started && sender_dial_attempts < 3 {
                    sender_dial_attempts += 1;
                    emit!("relay_dial_retry={sender_dial_attempts}");
                    tokio::time::sleep(Duration::from_millis(500)).await;
                    let circuit_addr = relay_addr.clone()
                        .with(libp2p::multiaddr::Protocol::P2pCircuit)
                        .with(libp2p::multiaddr::Protocol::P2p(target.ok_or("missing target peer")?));
                    swarm.dial(circuit_addr)?;
                } else {
                    return Err("relay client connection failed".into());
                }
            }
            _ => {
                let _ = receiver_reservation_accepted;
            }
        }
    }
}
