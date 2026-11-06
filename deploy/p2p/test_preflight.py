"""Profile regression tests; run: python deploy/p2p/test_preflight.py."""
import unittest

from preflight import check


class ProfileTests(unittest.TestCase):
    def valid(self):
        peer = "12D3KooW" + "A" * 44
        return {
            "OSIRIS_E2EE_ENABLED": "1", "OSIRIS_MDNS_ENABLED": "0",
            "OSIRIS_NODE_KEY": "/var/lib/osiris-p2p/identity.key",
            "OSIRIS_TRANSPORT_KEY": "/var/lib/osiris-p2p/transport.key",
            "OSIRIS_PEER_KEY_STORE": "/var/lib/osiris-p2p/peer-keys.sqlite3",
            "OSIRIS_REPLAY_CACHE": "/var/lib/osiris-p2p/replay.cache",
            "OSIRIS_REVOCATION_REGISTRY": "/var/lib/osiris-p2p/revocations.json",
            "OSIRIS_REVOCATION_AUTHORITY_PUBLIC_KEY": "1a" * 32,
            "OSIRIS_TRANSPORT_KEY_EPOCH": "1", "OSIRIS_MAX_OBSERVATION_AGE_SECS": "300",
            "OSIRIS_TRUSTED_PEERS": peer, "OSIRIS_E2EE_RECIPIENTS": peer,
            "OSIRIS_HEALTH_HTTP_ADDR": "127.0.0.1:9091",
            "OSIRIS_BOOTSTRAP_PEERS": f"/ip4/127.0.0.1/tcp/4001/p2p/{peer}",
        }

    def test_valid_profile(self):
        check(self.valid())

    def test_plaintext_or_open_admission_rejected(self):
        for key, value in (("OSIRIS_E2EE_ENABLED", "0"), ("OSIRIS_TRUSTED_PEERS", ""),
                           ("OSIRIS_E2EE_RECIPIENTS", ""), ("OSIRIS_MDNS_ENABLED", "1")):
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    check(dict(self.valid(), **{key: value}))

    def test_public_health_or_state_escape_rejected(self):
        for key, value in (("OSIRIS_HEALTH_HTTP_ADDR", "0.0.0.0:9091"),
                           ("OSIRIS_HEALTH_HTTP_ADDR", "127.0.0.1:0"),
                           ("OSIRIS_NODE_KEY", "/tmp/key"),
                           ("OSIRIS_NODE_KEY", "/var/lib/osiris-p2p/../key")):
            with self.subTest(key=key, value=value):
                with self.assertRaises(ValueError):
                    check(dict(self.valid(), **{key: value}))

    def test_restart_rotation_and_unbounded_age_rejected(self):
        for key, value in (("OSIRIS_ROTATE_NODE_KEY", "1"),
                           ("OSIRIS_MAX_OBSERVATION_AGE_SECS", "0"),
                           ("OSIRIS_MAX_OBSERVATION_AGE_SECS", "86401"),
                           ("OSIRIS_REVOCATION_AUTHORITY_PUBLIC_KEY", "REPLACE_WITH_PUBLIC_KEY")):
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    check(dict(self.valid(), **{key: value}))

    def test_freshness_digit_limit_matches_node_parser(self):
        check(dict(self.valid(), OSIRIS_MAX_OBSERVATION_AGE_SECS="00300"))
        with self.assertRaises(ValueError):
            check(dict(self.valid(), OSIRIS_MAX_OBSERVATION_AGE_SECS="00000300"))

    def test_ipv6_health_requires_socket_address_brackets(self):
        check(dict(self.valid(), OSIRIS_HEALTH_HTTP_ADDR="[::1]:9091"))
        check(dict(self.valid(), OSIRIS_HEALTH_HTTP_ADDR="[0:0:0:0:0:0:0:1]:9091"))
        for address in ("::1:9091", "[[::1]]:9091", "[127.0.0.1]:9091"):
            with self.subTest(address=address):
                with self.assertRaises(ValueError):
                    check(dict(self.valid(), OSIRIS_HEALTH_HTTP_ADDR=address))

    def test_recipient_and_circuit_relay_must_be_trusted(self):
        other = "12D3KooW" + "B" * 44
        profile = self.valid()
        with self.assertRaises(ValueError):
            check(dict(profile, OSIRIS_E2EE_RECIPIENTS=other))
        profile["OSIRIS_BOOTSTRAP_PEERS"] = (
            f"/ip4/127.0.0.1/tcp/4001/p2p/{other}/p2p-circuit/p2p/"
            + profile["OSIRIS_E2EE_RECIPIENTS"]
        )
        with self.assertRaises(ValueError):
            check(profile)
        profile["OSIRIS_TRUSTED_PEERS"] += "," + other
        check(profile)


if __name__ == "__main__":
    unittest.main()
