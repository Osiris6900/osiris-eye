use std::time::Duration;

use futures::StreamExt;
use libp2p::{
    core::{transport::OrTransport, upgrade, Transport},
    identity, noise, relay,
    swarm::{
        dial_opts::{DialOpts, PeerCondition},
        Config, NetworkBehaviour, Swarm, SwarmEvent,
    },
    tcp, yamux, Multiaddr, PeerId, SwarmBuilder,
};

const TEST_TIMEOUT: Duration = Duration::from_secs(20);

#[derive(NetworkBehaviour)]
struct RelayBehaviour {
    relay: relay::Behaviour,
}

#[derive(NetworkBehaviour)]
struct ClientBehaviour {
    relay: relay::client::Behaviour,
}

fn build_relay(config: relay::Config) -> Swarm<RelayBehaviour> {
    let key = identity::Keypair::generate_ed25519();
    SwarmBuilder::with_existing_identity(key)
        .with_tokio()
        .with_tcp(tcp::Config::default(), noise::Config::new, yamux::Config::default)
        .expect("relay TCP transport")
        .with_behaviour(|key| RelayBehaviour {
            relay: relay::Behaviour::new(key.public().to_peer_id(), config),
        })
        .expect("relay behaviour")
        .with_swarm_config(|config| config.with_idle_connection_timeout(TEST_TIMEOUT))
        .build()
}

fn build_client() -> Swarm<ClientBehaviour> {
    let key = identity::Keypair::generate_ed25519();
    let peer_id = PeerId::from(key.public());
    let (relay_transport, relay_behaviour) = relay::client::new(peer_id);
    let transport = OrTransport::new(
        relay_transport,
        tcp::tokio::Transport::new(tcp::Config::default()),
    )
    .upgrade(upgrade::Version::V1)
    .authenticate(noise::Config::new(&key).expect("client Noise config"))
    .multiplex(yamux::Config::default())
    .boxed();
    Swarm::new(
        transport,
        ClientBehaviour {
            relay: relay_behaviour,
        },
        peer_id,
        Config::with_tokio_executor().with_idle_connection_timeout(TEST_TIMEOUT),
    )
}

async fn relay_listen_address(relay_swarm: &mut Swarm<RelayBehaviour>) -> Multiaddr {
    relay_swarm
        .listen_on("/ip4/127.0.0.1/tcp/0".parse().expect("loopback listen address"))
        .expect("relay listens on loopback");
    let address = loop {
        if let SwarmEvent::NewListenAddr { address, .. } = relay_swarm.select_next_some().await {
            break address;
        }
    };
    relay_swarm.add_external_address(address.clone());
    address
}

fn relay_multiaddr(address: &Multiaddr, relay_peer: PeerId) -> Multiaddr {
    address
        .clone()
        .with(libp2p::multiaddr::Protocol::P2p(relay_peer))
}

async fn wait_for_reservation(
    relay_swarm: &mut Swarm<RelayBehaviour>,
    client: &mut Swarm<ClientBehaviour>,
    client_peer: PeerId,
) {
    let mut relay_accepted = false;
    let mut client_accepted = false;
    while !(relay_accepted && client_accepted) {
        tokio::select! {
            event = relay_swarm.select_next_some() => {
                if let SwarmEvent::Behaviour(RelayBehaviourEvent::Relay(
                    relay::Event::ReservationReqAccepted { src_peer_id, .. },
                )) = event {
                    assert_eq!(src_peer_id, client_peer);
                    relay_accepted = true;
                }
            }
            event = client.select_next_some() => {
                if let SwarmEvent::Behaviour(ClientBehaviourEvent::Relay(
                    relay::client::Event::ReservationReqAccepted { renewal: false, .. },
                )) = event {
                    client_accepted = true;
                }
            }
        }
    }
}

#[tokio::test]
async fn reservation_quota_denies_client_and_closes_only_failed_listener() {
    tokio::time::timeout(TEST_TIMEOUT, async {
        let mut relay_swarm = build_relay(relay::Config {
            max_reservations: 0,
            reservation_duration: Duration::from_secs(60),
            ..relay::Config::default()
        });
        let relay_peer = *relay_swarm.local_peer_id();
        let listen_address = relay_listen_address(&mut relay_swarm).await;
        let relay_address = relay_multiaddr(&listen_address, relay_peer);

        let mut client = build_client();
        let client_peer = *client.local_peer_id();
        let listener = client
            .listen_on(relay_address.with(libp2p::multiaddr::Protocol::P2pCircuit))
            .expect("reservation listener starts");

        let mut denial = false;
        let mut listener_failed = false;
        while !(denial && listener_failed) {
            tokio::select! {
                event = relay_swarm.select_next_some() => {
                    match event {
                        SwarmEvent::Behaviour(RelayBehaviourEvent::Relay(
                            relay::Event::ReservationReqDenied {
                                src_peer_id,
                                status: relay::StatusCode::ResourceLimitExceeded,
                            },
                        )) => {
                            assert_eq!(src_peer_id, client_peer);
                            denial = true;
                        }
                        SwarmEvent::Behaviour(RelayBehaviourEvent::Relay(
                            relay::Event::ReservationReqAccepted { .. },
                        )) => panic!("reservation above quota was accepted"),
                        _ => {}
                    }
                }
                event = client.select_next_some() => {
                    if let SwarmEvent::ListenerClosed {
                        listener_id,
                        reason: Err(_),
                        ..
                    } = event {
                        assert_eq!(listener_id, listener);
                        listener_failed = true;
                    }
                }
            }
        }
        assert!(client.is_connected(&relay_peer));
    })
    .await
    .expect("reservation quota denial must complete within the test timeout");
}

#[tokio::test]
async fn circuit_quota_denies_dial_without_establishing_destination_connection() {
    tokio::time::timeout(TEST_TIMEOUT, async {
        let mut relay_swarm = build_relay(relay::Config {
            max_circuits: 0,
            reservation_duration: Duration::from_secs(60),
            ..relay::Config::default()
        });
        let relay_peer = *relay_swarm.local_peer_id();
        let listen_address = relay_listen_address(&mut relay_swarm).await;
        let relay_address = relay_multiaddr(&listen_address, relay_peer);

        let mut receiver = build_client();
        let receiver_peer = *receiver.local_peer_id();
        receiver
            .listen_on(
                relay_address
                    .clone()
                    .with(libp2p::multiaddr::Protocol::P2pCircuit),
            )
            .expect("receiver reservation listener starts");
        wait_for_reservation(&mut relay_swarm, &mut receiver, receiver_peer).await;

        let mut sender = build_client();
        let sender_peer = *sender.local_peer_id();
        let circuit_address = relay_address
            .with(libp2p::multiaddr::Protocol::P2pCircuit)
            .with(libp2p::multiaddr::Protocol::P2p(receiver_peer));
        let dial = DialOpts::from(circuit_address);
        let circuit_connection = dial.connection_id();
        sender.dial(dial).expect("circuit dial starts");

        let mut denial = false;
        let mut dial_failed = false;
        while !(denial && dial_failed) {
            tokio::select! {
                event = relay_swarm.select_next_some() => {
                    match event {
                        SwarmEvent::Behaviour(RelayBehaviourEvent::Relay(
                            relay::Event::CircuitReqDenied {
                                src_peer_id,
                                dst_peer_id,
                                status: relay::StatusCode::ResourceLimitExceeded,
                            },
                        )) => {
                            assert_eq!(src_peer_id, sender_peer);
                            assert_eq!(dst_peer_id, receiver_peer);
                            denial = true;
                        }
                        SwarmEvent::Behaviour(RelayBehaviourEvent::Relay(
                            relay::Event::CircuitReqAccepted { .. },
                        )) => panic!("circuit above quota was accepted"),
                        _ => {}
                    }
                }
                event = sender.select_next_some() => {
                    match event {
                        SwarmEvent::OutgoingConnectionError { connection_id, .. }
                            if connection_id == circuit_connection => {
                                dial_failed = true;
                            }
                        SwarmEvent::ConnectionEstablished { peer_id, .. }
                            if peer_id == receiver_peer => {
                                panic!("sender connected to receiver despite circuit quota");
                            }
                        _ => {}
                    }
                }
                event = receiver.select_next_some() => {
                    match event {
                        SwarmEvent::Behaviour(ClientBehaviourEvent::Relay(
                            relay::client::Event::InboundCircuitEstablished { .. },
                        )) => panic!("receiver accepted an inbound circuit above quota"),
                        SwarmEvent::ConnectionEstablished { peer_id, .. }
                            if peer_id == sender_peer => {
                                panic!("receiver connected to sender despite circuit quota");
                            }
                        _ => {}
                    }
                }
            }
        }
        assert!(!sender.is_connected(&receiver_peer));
        assert!(!receiver.is_connected(&sender_peer));
    })
    .await
    .expect("circuit quota denial must complete within the test timeout");
}

#[tokio::test]
async fn per_peer_circuit_quota_preserves_first_and_denies_second_dial() {
    tokio::time::timeout(TEST_TIMEOUT, async {
        let mut relay_swarm = build_relay(relay::Config {
            max_circuits: 8,
            max_circuits_per_peer: 1,
            max_circuit_duration: Duration::from_secs(60),
            reservation_duration: Duration::from_secs(60),
            ..relay::Config::default()
        });
        let relay_peer = *relay_swarm.local_peer_id();
        let listen_address = relay_listen_address(&mut relay_swarm).await;
        let relay_address = relay_multiaddr(&listen_address, relay_peer);

        let mut receiver = build_client();
        let receiver_peer = *receiver.local_peer_id();
        receiver
            .listen_on(
                relay_address
                    .clone()
                    .with(libp2p::multiaddr::Protocol::P2pCircuit),
            )
            .expect("receiver reservation listener starts");
        wait_for_reservation(&mut relay_swarm, &mut receiver, receiver_peer).await;

        let mut sender = build_client();
        let sender_peer = *sender.local_peer_id();
        let circuit_address = relay_address
            .with(libp2p::multiaddr::Protocol::P2pCircuit)
            .with(libp2p::multiaddr::Protocol::P2p(receiver_peer));
        let first_dial = DialOpts::peer_id(receiver_peer)
            .condition(PeerCondition::Always)
            .addresses(vec![circuit_address.clone()])
            .build();
        let first_connection = first_dial.connection_id();
        sender.dial(first_dial).expect("first circuit dial starts");

        let mut relay_accepted = false;
        let mut sender_connected = false;
        let mut receiver_accepted = false;
        while !(relay_accepted && sender_connected && receiver_accepted) {
            tokio::select! {
                event = relay_swarm.select_next_some() => {
                    match event {
                        SwarmEvent::Behaviour(RelayBehaviourEvent::Relay(
                            relay::Event::CircuitReqAccepted { src_peer_id, dst_peer_id },
                        )) => {
                            assert_eq!(src_peer_id, sender_peer);
                            assert_eq!(dst_peer_id, receiver_peer);
                            relay_accepted = true;
                        }
                        SwarmEvent::Behaviour(RelayBehaviourEvent::Relay(
                            relay::Event::CircuitReqDenied { .. },
                        )) => panic!("first circuit was denied below the per-peer quota"),
                        _ => {}
                    }
                }
                event = sender.select_next_some() => {
                    match event {
                        SwarmEvent::ConnectionEstablished { connection_id, peer_id, .. }
                            if connection_id == first_connection && peer_id == receiver_peer => {
                                sender_connected = true;
                            }
                        SwarmEvent::OutgoingConnectionError { connection_id, error, .. }
                            if connection_id == first_connection => {
                                panic!("first circuit dial failed: {error}");
                            }
                        _ => {}
                    }
                }
                event = receiver.select_next_some() => {
                    if let SwarmEvent::Behaviour(ClientBehaviourEvent::Relay(
                        relay::client::Event::InboundCircuitEstablished { src_peer_id, .. },
                    )) = event {
                        assert_eq!(src_peer_id, sender_peer);
                        receiver_accepted = true;
                    }
                }
            }
        }

        let second_dial = DialOpts::peer_id(receiver_peer)
            .condition(PeerCondition::Always)
            .addresses(vec![circuit_address])
            .build();
        let second_connection = second_dial.connection_id();
        sender.dial(second_dial).expect("second circuit dial starts");

        let mut denial = false;
        let mut dial_failed = false;
        while !(denial && dial_failed) {
            tokio::select! {
                event = relay_swarm.select_next_some() => {
                    match event {
                        SwarmEvent::Behaviour(RelayBehaviourEvent::Relay(
                            relay::Event::CircuitReqDenied {
                                src_peer_id,
                                dst_peer_id,
                                status: relay::StatusCode::ResourceLimitExceeded,
                            },
                        )) => {
                            assert_eq!(src_peer_id, sender_peer);
                            assert_eq!(dst_peer_id, receiver_peer);
                            denial = true;
                        }
                        SwarmEvent::Behaviour(RelayBehaviourEvent::Relay(
                            relay::Event::CircuitReqAccepted { .. },
                        )) => panic!("second circuit exceeded the per-peer quota but was accepted"),
                        _ => {}
                    }
                }
                event = sender.select_next_some() => {
                    match event {
                        SwarmEvent::OutgoingConnectionError { connection_id, .. }
                            if connection_id == second_connection => {
                                dial_failed = true;
                            }
                        SwarmEvent::ConnectionEstablished { connection_id, .. }
                            if connection_id == second_connection => {
                                panic!("second circuit connected despite the per-peer quota");
                            }
                        _ => {}
                    }
                }
                event = receiver.select_next_some() => {
                    if let SwarmEvent::Behaviour(ClientBehaviourEvent::Relay(
                        relay::client::Event::InboundCircuitEstablished { .. },
                    )) = event {
                        panic!("receiver accepted a second inbound circuit above the per-peer quota");
                    }
                }
            }
        }
        assert!(sender.is_connected(&receiver_peer));
        assert!(receiver.is_connected(&sender_peer));
    })
    .await
    .expect("per-peer circuit quota denial must complete within the test timeout");
}
