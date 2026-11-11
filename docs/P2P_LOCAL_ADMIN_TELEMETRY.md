# OSIRIS P2P local node telemetry for Admin Centre (T17)

The authenticated Admin Centre can optionally show a real **single-node**
`GET /health` response from the running Rust P2P process, alongside (but not
combined with) identity enrollment. This fixes the previous limitation where
the dashboard had only an enrolled-identity registry and could not distinguish
it from connected sessions.

## Setup

1. Start the Rust P2P node with `OSIRIS_HEALTH_HTTP_ADDR=127.0.0.1:9090`.
2. In the separately running Quant API process set
   `OSIRIS_P2P_LOCAL_HEALTH_PORT=9090`. Use a numeric local TCP port only.
3. Authenticate to the read-only Admin Centre and open **Network**.

The API always dials the literal `127.0.0.1`, never a user-provided
hostname/URL, follows no redirects, sends no credentials, times out after
0.3 seconds and reads at most 4096 bytes. It validates a fixed set of fields
and discards all unknown fields, including node PeerId. If the port is
unconfigured, invalid, inaccessible, or the response fails validation, the
local live node status stays **unavailable**; it does not fall back to counting
enrolled identities as currently connected peers.

These are **observations for one local process**, not a network-wide total,
time-series monitoring, independent host verification, cryptographically
authenticated attestation, public uptime, or production T17 signoff.
The source health endpoint must remain restricted to loopback.
No live trading or broker orders are enabled. PAPER-ONLY.
