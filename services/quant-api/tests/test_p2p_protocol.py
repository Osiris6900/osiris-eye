import pytest

from osiris_api.p2p_protocol import (
    ProtocolNegotiationError,
    ProtocolOffer,
    ProtocolVersion,
    negotiate_protocol,
)


def offer(*, current="1.0.0", minimum=1, maximum=1, capabilities=frozenset({"peer_discovery"}), family="osiris-eye-p2p"):
    return ProtocolOffer(
        family=family,
        software_version="test-build",
        current=ProtocolVersion.parse(current),
        minimum_major=minimum,
        maximum_major=maximum,
        capabilities=frozenset(capabilities),
    )


def test_version_parser_is_strict():
    assert str(ProtocolVersion.parse("2.3.4")) == "2.3.4"
    with pytest.raises(ValueError, match="invalid_protocol_version"):
        ProtocolVersion.parse("v2.3")


def test_negotiates_only_allowlisted_capabilities():
    local = offer(capabilities={"peer_discovery", "admin_override"})
    remote = offer(capabilities={"peer_discovery", "admin_override"})
    result = negotiate_protocol(
        local,
        remote,
        allowed_capabilities=frozenset({"peer_discovery"}),
    )
    assert result["selected_major"] == 1
    assert result["capabilities"] == ["peer_discovery"]
    assert result["live_execution"] is False


def test_rejects_protocol_family_mismatch():
    with pytest.raises(ProtocolNegotiationError, match="protocol_family_mismatch"):
        negotiate_protocol(
            offer(),
            offer(family="other-network"),
            allowed_capabilities=frozenset({"peer_discovery"}),
        )


def test_rejects_incompatible_major_ranges():
    with pytest.raises(ProtocolNegotiationError, match="no_compatible_protocol_major"):
        negotiate_protocol(
            offer(current="2.0.0", minimum=2, maximum=2),
            offer(current="1.0.0", minimum=1, maximum=1),
            allowed_capabilities=frozenset(),
        )


def test_rejects_silent_downgrade():
    with pytest.raises(ProtocolNegotiationError, match="protocol_downgrade_rejected"):
        negotiate_protocol(
            offer(current="2.0.0", minimum=1, maximum=2),
            offer(current="1.0.0", minimum=1, maximum=1),
            allowed_capabilities=frozenset({"peer_discovery"}),
        )


def test_required_capability_must_be_mutually_supported_and_allowed():
    with pytest.raises(ProtocolNegotiationError, match="required_capabilities_missing"):
        negotiate_protocol(
            offer(),
            offer(),
            allowed_capabilities=frozenset(),
            required_capabilities=frozenset({"peer_discovery"}),
        )


@pytest.mark.parametrize("value", ["١.٢.٣", "1.0." + "0" * 10000, "4294967296.0.0", None])
def test_network_version_parser_rejects_unicode_and_unbounded_components(value):
    with pytest.raises(ValueError, match="invalid_protocol_version"):
        ProtocolVersion.parse(value)


@pytest.mark.parametrize("major", [float("nan"), 1.5, True])
def test_noninteger_major_cannot_bypass_negotiation_range(major):
    with pytest.raises(ValueError):
        ProtocolVersion(major)
    with pytest.raises(ValueError, match="invalid_supported_major_range"):
        offer(minimum=major)


def test_unbounded_peer_capabilities_rejected():
    with pytest.raises(ValueError, match="invalid_capability_set"):
        offer(capabilities={f"capability_{index}" for index in range(129)})


def test_capability_control_characters_rejected():
    with pytest.raises(ValueError, match="invalid_capability_name"):
        offer(capabilities={"peer_discovery\nadmin_override"})


def test_negotiation_rejects_malformed_offer():
    import pytest
    from osiris_api.p2p_protocol import negotiate_protocol, ProtocolNegotiationError

    with pytest.raises(ProtocolNegotiationError, match="invalid_protocol_offer"):
        negotiate_protocol(None, None, allowed_capabilities=frozenset())
