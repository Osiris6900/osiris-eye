use std::{env, error::Error, fs, path::PathBuf, time::Duration};

use futures::StreamExt;
use libp2p::{
    identify, identity, noise, relay,
    swarm::{NetworkBehaviour, SwarmEvent},
    tcp, yamux, Multiaddr, PeerId, SwarmBuilder,
};

const PROTOCOL_VERSION: &str = "/osiris-eye/relay/1.0.0";

#[derive(NetworkBehaviour)]
struct RelayBehaviour {
    relay: relay::Behaviour,
    identify: identify::Behaviour,
}

fn load_or_create_identity() -> Result<identity::Keypair, Box<dyn Error>> {
    let path = env::var_os("OSIRIS_RELAY_NODE_KEY")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(".osiris-relay-key"));

    if path.exists() {
        return Ok(identity::Keypair::from_protobuf_encoding(&fs::read(path)?)?);
    }

    let key = identity::Keypair::generate_ed25519();
    fs::write(&path, key.to_protobuf_encoding()?)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&path, fs::Permissions::from_mode(0o600))?;
    }
    eprintln!("Created relay identity at {} (keep this file private)", path.display());
    Ok(key)
}

fn env_usize(name: &str, default: usize) -> Result<usize, Box<dyn Error>> {
    match env::var(name) {
        Ok(value) => Ok(value.parse()?),
        Err(env::VarError::NotPresent) => Ok(default),
        Err(error) => Err(error.into()),
    }
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn Error>> {
    if env::var("OSIRIS_RELAY_SERVER_ENABLED").as_deref() != Ok("true") {
        return Err("relay server disabled; set OSIRIS_RELAY_SERVER_ENABLED=true to opt in".into());
    }

    let key = load_or_create_identity()?;
    let peer_id = PeerId::from(key.public());

    // Conservative local defaults. The libp2p relay protocol also applies
    // built-in per-peer and per-IP reservation/circuit rate limiters.
    let mut relay_config = relay::Config::default();
    relay_config.max_reservations = env_usize("OSIRIS_RELAY_MAX_RESERVATIONS", 64)?;
    relay_config.max_reservations_per_peer =
        env_usize("OSIRIS_RELAY_MAX_RESERVATIONS_PER_PEER", 2)?;
    relay_config.max_circuits = env_usize("OSIRIS_RELAY_MAX_CIRCUITS", 8)?;
    relay_config.max_circuits_per_peer =
        env_usize("OSIRIS_RELAY_MAX_CIRCUITS_PER_PEER", 2)?;
    relay_config.reservation_duration = Duration::from_secs(30 * 60);
    relay_config.max_circuit_duration = Duration::from_secs(60);
    relay_config.max_circuit_bytes = 1_048_576;

    let mut swarm = SwarmBuilder::with_existing_identity(key)
        .with_tokio()
        .with_tcp(tcp::Config::default(), noise::Config::new, yamux::Config::default)?
        .with_behaviour(|key| RelayBehaviour {
            relay: relay::Behaviour::new(key.public().to_peer_id(), relay_config),
            identify: identify::Behaviour::new(identify::Config::new(
                PROTOCOL_VERSION.to_owned(),
                key.public(),
            )),
        })?
        .with_swarm_config(|config| {
            config.with_idle_connection_timeout(Duration::from_secs(120))
        })
        .build();

    let listen: Multiaddr = env::var("OSIRIS_RELAY_LISTEN_ADDR")
        .unwrap_or_else(|_| "/ip4/0.0.0.0/tcp/4001".to_owned())
        .parse()?;
    swarm.listen_on(listen)?;

    println!("OSIRIS EYE Circuit Relay v2 server starting");
    println!("peer_id={peer_id}");
    println!("relay_server_enabled=true");
    println!("limits reservations={} reservations_per_peer={} circuits={} circuits_per_peer={} circuit_seconds=60 circuit_bytes=1048576",
        env_usize("OSIRIS_RELAY_MAX_RESERVATIONS", 64)?,
        env_usize("OSIRIS_RELAY_MAX_RESERVATIONS_PER_PEER", 2)?,
        env_usize("OSIRIS_RELAY_MAX_CIRCUITS", 8)?,
        env_usize("OSIRIS_RELAY_MAX_CIRCUITS_PER_PEER", 2)?);
    println!("WARNING: public exposure requires firewalling, monitoring, abuse response and a reachable host.");

    loop {
        match swarm.select_next_some().await {
            SwarmEvent::NewListenAddr { address, .. } => {
                println!("relay_listening={address}/p2p/{peer_id}");
            }
            SwarmEvent::ConnectionEstablished { peer_id, .. } => {
                println!("relay_peer_connected={peer_id}");
            }
            SwarmEvent::ConnectionClosed { peer_id, .. } => {
                println!("relay_peer_disconnected={peer_id}");
            }
            SwarmEvent::Behaviour(RelayBehaviourEvent::Relay(event)) => {
                println!("relay_event={event:?}");
            }
            SwarmEvent::Behaviour(RelayBehaviourEvent::Identify(
                identify::Event::Received { peer_id, info, .. }
            )) => {
                println!("identify_received peer={peer_id} observed_addr={}", info.observed_addr);
                swarm.add_external_address(info.observed_addr);
            }
            _ => {}
        }
    }
}
