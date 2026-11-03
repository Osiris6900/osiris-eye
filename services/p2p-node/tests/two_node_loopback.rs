use std::time::Duration;

use futures::StreamExt;
use libp2p::{
    gossipsub, identity, noise,
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
    let config = gossipsub::ConfigBuilder::default()
        .heartbeat_interval(Duration::from_millis(100))
        .validation_mode(gossipsub::ValidationMode::Strict)
        .build()
        .expect("valid GossipSub config");
    let gossip = gossipsub::Behaviour::new(
        gossipsub::MessageAuthenticity::Signed(key.clone()),
        config,
    )
    .expect("GossipSub behaviour");
    let mut behaviour = TestBehaviour { gossipsub: gossip };
    behaviour
        .gossipsub
        .subscribe(&gossipsub::IdentTopic::new(TOPIC_NAME))
        .expect("subscribe to observation topic");

    SwarmBuilder::with_existing_identity(key)
        .with_tokio()
        .with_tcp(tcp::Config::default(), noise::Config::new, yamux::Config::default)
        .expect("TCP transport")
        .with_behaviour(|_| behaviour)
        .expect("test behaviour")
        .build()
}

#[tokio::test]
async fn two_local_nodes_exchange_signed_observation() {
    let key_a = identity::Keypair::generate_ed25519();
    let key_b = identity::Keypair::generate_ed25519();
    let peer_a = PeerId::from(key_a.public());
    let peer_b = PeerId::from(key_b.public());
    let mut node_a = build_swarm(key_a);
    let mut node_b = build_swarm(key_b);

    node_a
        .listen_on("/ip4/127.0.0.1/tcp/0".parse::<Multiaddr>().unwrap())
        .expect("node A listens");

    let listen_address = tokio::time::timeout(Duration::from_secs(5), async {
        loop {
            if let SwarmEvent::NewListenAddr { address, .. } = node_a.select_next_some().await {
                break address;
            }
        }
    })
    .await
    .expect("node A listen address");

    let mut dial_address = listen_address;
    dial_address.push(libp2p::multiaddr::Protocol::P2p(peer_a));
    node_b.dial(dial_address).expect("node B dials node A");

    let expected = ObservationEnvelope {
        schema: "osiris.observation.v1".into(),
        instrument: "BTC-USD".into(),
        observed_at: "2026-11-03T01:00:00Z".into(),
        provider: "loopback-test".into(),
        payload_sha256: "a".repeat(64),
    };
    let payload = serde_json::to_vec(&expected).unwrap();
    let topic = gossipsub::IdentTopic::new(TOPIC_NAME);
    let mut a_saw_b_subscription = false;
    let mut b_saw_a_subscription = false;
    let mut published = false;

    tokio::time::timeout(Duration::from_secs(15), async {
        loop {
            tokio::select! {
                event = node_a.select_next_some() => {
                    if let SwarmEvent::Behaviour(TestBehaviourEvent::Gossipsub(
                        gossipsub::Event::Subscribed { peer_id, .. }
                    )) = event {
                        if peer_id == peer_b { a_saw_b_subscription = true; }
                    }
                }
                event = node_b.select_next_some() => {
                    if let SwarmEvent::Behaviour(TestBehaviourEvent::Gossipsub(
                        gossipsub::Event::Subscribed { peer_id, .. }
                    )) = event {
                        if peer_id == peer_a { b_saw_a_subscription = true; }
                    }
                    if let SwarmEvent::Behaviour(TestBehaviourEvent::Gossipsub(
                        gossipsub::Event::Message { message, .. }
                    )) = event {
                        if let Ok(received) = serde_json::from_slice::<ObservationEnvelope>(&message.data) {
                            if received == expected { break; }
                        }
                    }
                }
                _ = tokio::time::sleep(Duration::from_millis(100)),
                    if a_saw_b_subscription && b_saw_a_subscription && !published => {
                    match node_a.behaviour_mut().gossipsub.publish(topic.clone(), payload.clone()) {
                        Ok(_) => published = true,
                        Err(gossipsub::PublishError::InsufficientPeers) => {}
                        Err(error) => panic!("unexpected publish error: {error:?}"),
                    }
                }
            }
        }
    })
    .await
    .expect("two nodes should exchange the signed observation within 15 seconds");
}
