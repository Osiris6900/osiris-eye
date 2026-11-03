# OSIRIS EYE Circuit Relay v2 Server

This is an opt-in, standalone libp2p Circuit Relay v2 server process. It is separate from the observation node so ordinary peers do not silently become public relays.

## Build and run

From the repository root:

```bash
cargo build --release --manifest-path services/p2p-node/Cargo.toml --bin osiris-p2p-relay
OSIRIS_RELAY_LISTEN_ADDR=/ip4/0.0.0.0/tcp/4001 \
  OSIRIS_RELAY_NODE_KEY=/var/lib/osiris/relay-key \
  services/p2p-node/target/release/osiris-p2p-relay
```

The default listen address is `/ip4/0.0.0.0/tcp/4001`. Keep the key file private and persistent. The relay prints its PeerId and full listening multiaddress; clients need that exact address, including `/p2p/<peer-id>`.

## Safety and operational limits

- The process uses libp2p 0.56 Circuit Relay v2 and Noise-encrypted libp2p connections.
- It uses libp2p's default relay reservation/circuit quotas and per-peer/per-IP rate limiters. Do not remove these limits when exposing a relay publicly.
- The relay forwards encrypted transport streams. It is not an application-layer decryptor and must never be given broker credentials, exchange API secrets, account data, or trading orders.
- A relay server is an availability/bandwidth service. Production deployment still needs host firewalling, monitoring, abuse response, capacity limits, patching, and a stable public address.
- This binary alone does not prove that clients can traverse NAT or reach each other over the public Internet. Client-side relay transport, reservation, relayed dial, and multi-network acceptance tests are the next implementation stage.
- GitHub Actions localhost tests are not Internet connectivity tests. Vercel serverless is not a suitable always-on libp2p relay host.

No public relay has been deployed by this change. No paid infrastructure is provisioned.
