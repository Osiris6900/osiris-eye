import unittest
from check_decentralized_bootstrap import validate


def config():
    return {"central_authority_required": False, "bootstrap_peers": [
        {"operator": "operator-a", "multiaddr": "/dns4/a.example.org/tcp/4001/p2p/peerA"},
        {"operator": "operator-b", "multiaddr": "/dns4/b.example.org/tcp/4001/p2p/peerB"},
    ]}


class BootstrapGateTests(unittest.TestCase):
    def test_two_operators_pass_configuration_only(self):
        result = validate(config())
        self.assertEqual(result["status"], "configuration_pass")
        self.assertFalse(result["verified_public_reachability"])

    def test_single_bootstrap_fails(self):
        c = config()
        c["bootstrap_peers"].pop()
        with self.assertRaises(ValueError):
            validate(c)

    def test_shared_operator_fails(self):
        c = config()
        c["bootstrap_peers"][1]["operator"] = "operator-a"
        with self.assertRaises(ValueError):
            validate(c)

    def test_missing_identity_fails(self):
        c = config()
        c["bootstrap_peers"][0]["multiaddr"] = "/dns4/a.example.org/tcp/4001"
        with self.assertRaises(ValueError):
            validate(c)

    def test_central_authority_requirement_fails(self):
        c = config()
        c["central_authority_required"] = True
        with self.assertRaises(ValueError):
            validate(c)


if __name__ == "__main__":
    unittest.main()
