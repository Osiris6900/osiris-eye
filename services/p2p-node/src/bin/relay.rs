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

/// Translate an exact operator limit to libp2p-relay 0.21's raw configuration.
/// Upstream denies only when `current > configured` before insertion, so the
/// raw value must be one lower. Zero operator limits fail closed: setting the
/// global limit to zero is the explicit way to deny all reservations/circuits.
fn libp2p_per_peer_limit(requested: usize) -> Result<usize, &'static str> {
    requested.checked_sub(1).ok_or(
        "per-peer limit must be at least 1; set the corresponding global limit to 0 to deny all",
    )
}

fn env_per_peer_limit(name: &str, default: usize) -> Result<usize, Box<dyn Error>> {
    let requested = env_usize(name, default)?;
    libp2p_per_peer_limit(requested)
        .map_err(|error| format!("{name}: {error} (requested {requested})").into())
}

#[cfg(test)]
mod tests {
    use super::libp2p_per_peer_limit;

    #[test]
    fn exact_operator_limits_account_for_libp2p_pre_insertion_comparison() {
        assert_eq!(libp2p_per_peer_limit(1), Ok(0));
        assert_eq!(libp2p_per_peer_limit(2), Ok(1));
        assert_eq!(libp2p_per_peer_limit(64), Ok(63));
        assert_eq!(libp2p_per_peer_limit(usize::MAX), Ok(usize::MAX - 1));
    }

    #[test]
    fn zero_per_peer_limit_fails_closed_instead_of_admitting_one() {
        assert!(libp2p_per_peer_limit(0).is_err());
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
        env_per_peer_limit("OSIRIS_RELAY_MAX_RESERVATIONS_PER_PEER", 2)?;
    relay_config.max_circuits = env_usize("OSIRIS_RELAY_MAX_CIRCUITS", 8)?;
    relay_config.max_circuits_per_peer =
        env_per_peer_limit("OSIRIS_RELAY_MAX_CIRCUITS_PER_PEER", 2)?;
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
                // Relay reservations include the relay's externally reachable
                // addresses. A localhost test relay has no identify-observed
                // address yet, so explicitly advertise its bound listen address.
                swarm.add_external_address(address.clone());
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
