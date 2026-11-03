# Cross-host P2P E2EE acceptance

This test exercises the real network path between two separate machines. It
is distinct from localhost process tests and does not claim production
certification.

## Preconditions

- Host A and Host B are separate machines with a mutually reachable TCP path.
- Host A accepts inbound TCP on the selected port (default 4001) from Host B.
- Run the controller on Host A. Host A must be able to SSH to Host B using
  non-interactive key authentication.
- Both machines run the same OSIRIS EYE release binary built from the same
  commit. Host B's binary must be present at the path passed to
  `--remote-binary`.
- Host A has Python 3 and OpenSSH client. Host B has OpenSSH server and can run
  the P2P binary. SSH key authentication should already be configured.
- Do not expose a private transport key, identity key, broker credential, or
  account token. The test creates fresh throwaway identity and transport keys.

## Build both hosts

From the repository root on both machines:

```sh
cargo build --release --manifest-path services/p2p-node/Cargo.toml
```

The default remote binary path expected by the test is
`$HOME/osiris-eye/target/release/osiris-p2p-node`. Override it when the
repository or binary is elsewhere.

## Run from Host A

```sh
python3 scripts/test_p2p_cross_host_e2ee_ssh.py \
  --ssh-target user@HOST_B \
  --public-ip HOST_A_REACHABLE_IP \
  --port 4001
```

For a private overlay network such as Tailscale, use Host A's overlay IPv4
address as `--public-ip`; the name "public-ip" means reachable address, not
necessarily an Internet-routable address. Ensure the overlay ACL permits TCP
port 4001 between the two test machines.

Optional arguments:

- `--remote-binary /absolute/path/to/osiris-p2p-node`
- `--binary /absolute/path/to/local/osiris-p2p-node`
- Repeat `--ssh-option=-i --ssh-option=/path/to/test_key` or provide other
  OpenSSH options as separate arguments.

## What a pass proves

The harness:

1. Starts Host A with E2EE enabled and a fresh local identity/transport key.
2. Starts Host B over SSH with its own fresh identity/transport key and dials
   Host A's advertised reachable address.
3. Waits for each host to verify and durably store the other's signed key
   binding.
4. Configures Host A to send only to Host B's PeerId.
5. Publishes one encrypted observation and requires Host B to decrypt the
   exact instrument and payload SHA-256 digest.

A successful run prints
`CROSS_HOST_E2EE_RECIPIENT_DELIVERY=PASS`. Capture the logs and commit SHA
with the test result.

## What a pass does not prove

- It does not test a third non-recipient observer on a separate host. That
  remains covered only by the existing local three-process E2EE acceptance.
- It does not prove resistance to a compromised endpoint, malicious
  administrator, traffic analysis, denial of service, or long-term key
  compromise.
- It is not an independent cryptographic audit, production security
  certification, or authorization for live broker credentials/order routing.
- A local CI run is not a substitute for running this harness across two
  genuinely separate hosts.

The script terminates both test processes and uses temporary key material.
If a host is forcibly terminated, inspect and remove any leftover
`/tmp/osiris-xhost-e2ee.*` directory on Host B after confirming it belongs to
this test.
