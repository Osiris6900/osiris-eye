use std::{env, error::Error, fs, os::unix::fs::PermissionsExt, path::PathBuf, time::Duration};

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
    fs::set_permissions(&path, fs::Permissions::from_mode(0o600))?;
    eprintln!("Created relay identity at {} (keep private)", path.display());
    Ok(key)
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn Error>> {
    let key = load_or_create_identity()?;
    let peer_id = PeerId::from(key.public());

    // libp2p's default relay config applies bounded reservation/circuit quotas
    // and per-peer/per-IP rate limits. Do not remove these limits for public use.
    let mut swarm = SwarmBuilder::with_existing_identity(key)
        .with_tokio()
        .with_tcp(tcp::Config::default(), noise::Config::new, yamux::Config::default)?
        .with_behaviour(|key| RelayBehaviour {
            relay: relay::Behaviour::new(key.public().to_peer_id(), relay::Config::default()),
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

    println!("OSIRIS EYE Circuit Relay v2 server");
    println!("peer_id={peer_id}");
    println!("relay_protocol=/libp2p/circuit/relay/0.2.0");
    println!("This process relays encrypted transport streams; it does not access application payload keys.");
    println!("Default libp2p reservation and circuit quotas remain enabled.");

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
            SwarmEvent::Behaviour(RelayBehaviourEvent::Identify(identify::Event::Received {
                peer_id,
                info,
                ..
            })) => {
                swarm.add_external_address(info.observed_addr);
                println!("relay_identified_peer={peer_id}");
            }
            SwarmEvent::Behaviour(event) => println!("relay_event={event:?}"),
            _ => {}
        }
    }
}
