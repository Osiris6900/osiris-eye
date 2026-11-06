# No-central-authority P2P bootstrap gate

OSIRIS already contains libp2p Noise, GossipSub, Kademlia, optional local mDNS, peer identities and an optional relay client. These do **not** automatically guarantee Internet discovery or traversal of restrictive NATs.

The bootstrap addresses in `OSIRIS_BOOTSTRAP_PEERS` are **entry points, not authorities**. The intended topology has at least two independent operators, with distinct PeerIds, and nodes can exchange peer addresses through Kademlia after initial contact. The existing relay **client** is not proof that ordinary nodes can act as relay servers.

Run `python -m unittest discover -s scripts -p 'test_check_decentralized_bootstrap.py' -v` and validate a private, operator-maintained JSON configuration using `python scripts/check_decentralized_bootstrap.py config.json`. Config shape: `{"central_authority_required":false,"bootstrap_peers":[{"operator":"operator-a","multiaddr":"/dns4/node-a.example.org/tcp/4001/p2p/PEER_ID_A"},{"operator":"operator-b","multiaddr":"/dns4/node-b.example.org/tcp/4001/p2p/PEER_ID_B"}]}`. Example addresses are placeholders, never evidence of live peers.

This is a fail-closed **configuration gate only**. It cannot attest that operators, networks, or keys are independent. Before release, test independent public bootstrap connectivity, restart of one bootstrap node, recovery using remaining peers, identity validation, NAT/relay behavior and recipient-only E2EE. Keep production gates BLOCKED until signed evidence exists. Never commit private keys or operator secrets.
