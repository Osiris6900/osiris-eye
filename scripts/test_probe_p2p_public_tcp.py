"""Tests for the external public TCP probe; no network access required."""
import importlib.util
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch
import socket

spec = importlib.util.spec_from_file_location(
    "public_probe", Path(__file__).with_name("probe_p2p_public_tcp.py")
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ProbeTests(TestCase):
    def test_rejects_nonpublic_targets(self):
        for host in ("127.0.0.1", "10.0.0.1", "192.168.1.5", "::1", "203.0.113.10"):
            with self.subTest(host=host), self.assertRaises(ValueError):
                module.validate_target(host, 4001)

    def test_rejects_invalid_ports_and_names(self):
        for host, port in (("", 4001), ("bad host", 4001), ("example.org", 0), ("-bad.org", 4001)):
            with self.subTest(host=host), self.assertRaises(ValueError):
                module.validate_target(host, port)

    def test_failed_resolution_is_not_reported_as_success(self):
        with patch.object(module.socket, "getaddrinfo", side_effect=socket.gaierror):
            result = module.probe("node.example.org", 4001, 1)
        self.assertFalse(result["tcp_reachable"])
        self.assertFalse(result["authenticated_p2p_verified"])
        self.assertFalse(result["e2ee_verified"])
        self.assertFalse(result["relay_fallback_verified"])

    def test_private_dns_resolution_is_not_probed(self):
        with patch.object(module.socket, "getaddrinfo", return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 4001))
        ]), patch.object(module.socket, "socket") as sock:
            result = module.probe("node.example.org", 4001, 1)
        self.assertFalse(result["tcp_reachable"])
        sock.assert_not_called()
