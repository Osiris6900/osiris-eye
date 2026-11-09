//! LAN peer discovery and signed market-event gossip demo.
//! Discovery is local-only; no claim of internet-wide self-healing or trusted data.
use futures::StreamExt;
use libp2p::{
    gossipsub::{self, MessageAuthenticity},
    identity, mdns, noise,
    swarm::{NetworkBehaviour, SwarmEvent},
    tcp, yamux, Multiaddr, SwarmBuilder,
};
use std::time::Duration;

const TOPIC: &str = "osiris-market-pulse";

#[derive(NetworkBehaviour)]
struct ShadowBehaviour {
    gossip: gossipsub::Behaviour,
    discovery: mdns::tokio::Behaviour,
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let keys = identity::Keypair::generate_ed25519();
    let peer_id = keys.public().to_peer_id();
    let gossip_config = gossipsub::ConfigBuilder::default()
        .heartbeat_interval(Duration::from_secs(10))
        .validation_mode(gossipsub::ValidationMode::Strict)
        .build()
        .map_err(|error| std::io::Error::other(error.to_string()))?;
    let mut swarm = SwarmBuilder::with_existing_identity(keys.clone())
        .with_tokio()
        .with_tcp(tcp::Config::default(), noise::Config::new, yamux::Config::default)?
        .with_behaviour(|_| {
            let gossip = gossipsub::Behaviour::new(
                MessageAuthenticity::Signed(keys.clone()), gossip_config
            ).map_err(|error| std::io::Error::other(error.to_string()))?;
            let discovery = mdns::tokio::Behaviour::new(mdns::Config::default(), peer_id)?;
            Ok(ShadowBehaviour { gossip, discovery })
        })?
        .build();

    let topic = gossipsub::IdentTopic::new(TOPIC);
    swarm.behaviour_mut().gossip.subscribe(&topic)?;
    let listen: Multiaddr = "/ip4/0.0.0.0/tcp/0".parse()?;
    swarm.listen_on(listen)?;
    println!("Shadow LAN peer={peer_id} topic={TOPIC}");

    loop {
        tokio::select! {
            _ = tokio::signal::ctrl_c() => break,
            event = swarm.select_next_some() => match event {
                SwarmEvent::NewListenAddr { address, .. } => println!("listening={address}"),
                SwarmEvent::Behaviour(ShadowBehaviourEvent::Discovery(mdns::Event::Discovered(peers))) => {
                    for (peer, address) in peers {
                        swarm.behaviour_mut().gossip.add_explicit_peer(&peer);
                        if let Err(error) = swarm.dial(address) {
                            eprintln!("peer dial failed: {error}");
                        }
                    }
                }
                SwarmEvent::Behaviour(ShadowBehaviourEvent::Discovery(mdns::Event::Expired(peers))) => {
                    for (peer, _) in peers {
                        swarm.behaviour_mut().gossip.remove_explicit_peer(&peer);
                    }
                }
                SwarmEvent::Behaviour(ShadowBehaviourEvent::Gossip(gossipsub::Event::Message {
                    propagation_source, message, ..
                })) => {
                    // Untrusted peer content: log metadata only; never execute trades.
                    println!("peer={propagation_source} bytes={}", message.data.len());
                }
                SwarmEvent::ConnectionClosed { peer_id, cause, .. } => {
                    eprintln!("peer={peer_id} disconnected cause={cause:?}");
                }
                _ => {}
            }
        }
    }
    Ok(())
}
