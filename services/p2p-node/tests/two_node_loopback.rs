use std::time::Duration;

use futures::StreamExt;
use libp2p::{
    gossipsub, identity,
    noise,
    swarm::{NetworkBehaviour, SwarmEvent},
    tcp, yamux, Multiaddr, PeerId, Swarm, SwarmBuilder,
};
use serde::{Deserialize, Serialize};

const TOPIC_NAME: &str = "osiris/observations/1";

#[derive(Debug, Serialize, Deserialize, PartialEq, Eq)]
struct ObservationEnvelope {
    schema: String,
    instrument: String,
    observed_at: String,
    provider: String,
    payload_sha256: String,
}

#[derive(NetworkBehaviour)]
struct TestBehaviour {
    gossipsub: gossipsub::Behaviour,
}

fn build_swarm(key: identity::Keypair) -> Swarm<TestBehaviour> {
    let peer_id = PeerId::from(key.public());
    let config = gossipsub::ConfigBuilder::default()
        .heartbeat_interval(Duration::from_millis(100))
        .validation_mode(gossipsub::ValidationMode::Strict)
        .build()
        .expect("valid GossipSub config");
    let gossip = gossipsub::Behaviour::new(
        gossipsub::MessageAuthenticity::Signed(key),
        config,
    )
    .expect("GossipSub behaviour");
    let mut behaviour = TestBehaviour { gossipsub: gossip };
    behaviour
        .gossipsub
        .subscribe(&gossipsub::IdentTopic::new(TOPIC_NAME))
        .expect("subscribe to observation topic");

    SwarmBuilder::with_existing_identity(key_for_transport(peer_id, &behaviour))
        .with_tokio()
        .with_tcp(tcp::Config::default(), noise::Config::new, yamux::Config::default)
        .expect("TCP transport")
        .with_behaviour(|_| behaviour)
        .expect("test behaviour")
        .build()
}

// Keep the transport identity paired with the signing identity.
fn key_for_transport(_peer_id: PeerId, _behaviour: &TestBehaviour) -> identity::Keypair {
    // The signing key is retained separately by the constructor below.
    unreachable!("replaced by build_swarm_with_key")
}

