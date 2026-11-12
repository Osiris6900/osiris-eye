"""The SDK cannot put credentials in URLs or silently retry mutations."""

import pytest

from osiris_api.control_plane.client import ResearchClient


class Transport:
    def __init__(self):
        self.calls = []

    def request(self, method, url, **options):
        self.calls.append((method, url, options))
        return {"live_execution": False}


@pytest.mark.parametrize(
    "origin",
    [
        "http://example.invalid",
        "https://user:pass@example.invalid",
        "https://example.invalid/x",
        "https://example.invalid?token=x",
        "https://example.invalid#x",
    ],
)
def test_sdk_refuses_unsafe_origin(origin):
    with pytest.raises(ValueError):
        ResearchClient(origin, "Bearer fixture-token")


def test_credentials_remain_in_header_and_queries_are_escaped():
    transport = Transport()
    client = ResearchClient(
        "https://example.invalid", "Session fixture-session", transport=transport
    )
    client.market_views("news", "GLOBAL&limit=1000")
    method, url, options = transport.calls[0]
    assert method == "GET" and "Session" not in url and "fixture-session" not in url
    assert "GLOBAL%26limit%3D1000" in url
    assert options["headers"] == {"Authorization": "Session fixture-session"}
    assert "fixture-session" not in repr(client)
    client.ingest_market_view({"id": "fixture"})
    assert len(transport.calls) == 2 and transport.calls[-1][0] == "POST"


def test_ambiguous_write_is_never_automatically_retried():
    class Failed(Transport):
        def request(self, *args, **kwargs):
            super().request(*args, **kwargs)
            raise ValueError("upstream_request_failed")

    transport = Failed()
    client = ResearchClient(
        "https://example.invalid", "Session fixture-session", transport=transport
    )
    with pytest.raises(ValueError):
        client.ingest_market_view({"id": "fixture"})
    assert len(transport.calls) == 1
