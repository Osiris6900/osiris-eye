# P2P Resilience: Self-Sustaining Mesh (No VPS Required)

The OSIRIS EYE node is designed so the network runs on the internet by itself:
every participant's own machine is the infrastructure. There is no required
server, no VPS, and no single point whose loss disconnects the network.

## The four layers

1. **Durable peer cache** (`OSIRIS_PEER_CACHE`, SQLite, WAL). Every peer the
   node meets through connections, mDNS, identify, or DHT walks is remembered
   with its addresses. On restart the node re-dials recent cached peers, so a
   reboot never means starting from zero. Revoked peers are purged from the
   cache at startup and whenever a signed revocation arrives; the cache is
   discovery data only — every rejoin dial re-passes live trust/revocation
   policy, and a corrupt cache file fails closed instead of silently
   downgrading to empty discovery.

2. **Relay failover + auto-relay.** `OSIRIS_RELAY_ADDRESSES` accepts a
   comma-separated list of relays; the node holds reservations on several at
   once, so losing one relay does not make it unreachable. With no configured
   relay, `OSIRIS_AUTO_RELAY=1` (default) borrows circuit reservations from
   already-connected, policy-allowed bootstrap peers.

3. **Self-reachability without a VPS.** Three mechanisms remove the need for
   any hosted relay or public-IP server:
   - **UPnP** (`OSIRIS_UPNP_ENABLED=1`, default): the node asks the home
     router for a port mapping, making a laptop behind NAT publicly reachable
     with zero manual firewall work.
   - **AutoNAT**: continuously measures whether the node is publicly reachable
     and reports status changes (`autonat_status`).
   - **DCUtR hole punching**: two NATted nodes that share a relayed connection
     upgrade it to a direct connection automatically
     (`hole_punch_succeeded` / `hole_punch_failed`).
   - **Opt-in relay server** (`OSIRIS_RELAY_SERVER=1`): any node can also
     *serve* as a relay for others, with the same conservative limits as the
     standalone relay binary (64 reservations, 8 circuits, 60 s circuit
     duration, 1 MiB per circuit). With enough nodes opted in, the mesh
     carries its own relay capacity — no dedicated relay machine needed.

4. **Public bootstrap seed file.** A node with neither bootstrap config nor a
   warm cache fetches a small community seed list over HTTPS (default:
   `deploy/p2p/bootstrap-peers.txt` served from this repository; override with
   `OSIRIS_BOOTSTRAP_SEED_URL`, disable with `off`). The fetch is HTTPS-only,
   follows no redirects, and is capped at 64 KiB; invalid lines are skipped.
   Seeds are entry points only — they still pass trust/revocation policy
   before any dial.

Once a node is in the mesh, DHT random walks (`OSIRIS_DHT_WALK_INTERVAL_SECS`)
and identify exchanges keep discovering new peers forever, and the peer cache
carries that knowledge across restarts.

## Honest limits

- **First contact needs one entry point.** A brand-new node with an empty
  cache, no `OSIRIS_BOOTSTRAP_PEERS`, and an unreachable seed file cannot
  find the mesh by itself — that is inherent to any permissionless overlay.
  After the first successful connection, layers 1–4 make it self-sustaining.
- **UPnP depends on the router.** Some routers disable it; the node then
  falls back to relays and DCUtR and still works, just not as a public peer.
- **Seed file hosting** (GitHub raw) is a convenience, not a dependency of
  the protocol: any HTTPS host works, and operators can share seed addresses
  over any authenticated channel.
- Relay capacity donated by nodes is best-effort. Operators who need
  guaranteed reachability can still run the standalone relay binary
  (`src/bin/relay.rs`) on any always-on machine — it remains fully supported.

## Operating checklist

- Leave `OSIRIS_UPNP_ENABLED=1` and `OSIRIS_AUTO_RELAY=1` on.
- Set `OSIRIS_RELAY_SERVER=1` on machines with spare bandwidth.
- Configure `OSIRIS_RELAY_ADDRESSES` with two or more relays for failover.
- Back up `OSIRIS_PEER_CACHE` alongside the identity keys.
- Contribute long-lived public peers to `deploy/p2p/bootstrap-peers.txt`.
