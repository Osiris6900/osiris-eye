"""Version and capability negotiation primitives for future P2P handshakes.

This module does not open sockets or enable peer discovery. It provides a
deterministic policy function for a transport handshake to call before accepting
a peer.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

MAX_VERSION_COMPONENT = 2**32 - 1
MAX_CAPABILITIES = 128


def _validate_capabilities(capabilities: frozenset[str]) -> None:
    if not isinstance(capabilities, (set, frozenset)) or len(capabilities) > MAX_CAPABILITIES:
        raise ValueError("invalid_capability_set")
    if any(not isinstance(item, str) or re.fullmatch(r"[A-Za-z0-9_.:/-]{1,128}", item) is None
           for item in capabilities):
        raise ValueError("invalid_capability_name")


class ProtocolNegotiationError(ValueError):
    """Raised when two peers cannot safely negotiate a protocol."""


@dataclass(frozen=True, order=True)
class ProtocolVersion:
    major: int
    minor: int = 0
    patch: int = 0

    def __post_init__(self) -> None:
        if any(type(part) is not int or not 0 <= part <= MAX_VERSION_COMPONENT
               for part in (self.major, self.minor, self.patch)):
            raise ValueError("protocol_version_components_must_be_non_negative")
        if self.major == 0:
            raise ValueError("protocol_major_must_be_at_least_one")

    @classmethod
    def parse(cls, value: str) -> "ProtocolVersion":
        if not isinstance(value, str) or re.fullmatch(r"[0-9]{1,10}\.[0-9]{1,10}\.[0-9]{1,10}", value) is None:
            raise ValueError("invalid_protocol_version")
        parts = tuple(int(part) for part in value.split("."))
        if any(part > MAX_VERSION_COMPONENT for part in parts):
            raise ValueError("invalid_protocol_version")
        return cls(*parts)

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


@dataclass(frozen=True)
class ProtocolOffer:
    family: str
    software_version: str
    current: ProtocolVersion
    minimum_major: int
    maximum_major: int
    capabilities: frozenset[str]

    def __post_init__(self) -> None:
        if any(not isinstance(item, str) or not item or len(item) > 128
               or any(ord(character) < 32 or ord(character) == 127 for character in item)
               for item in (self.family, self.software_version)):
            raise ValueError("protocol_family_and_software_version_required")
        if (type(self.minimum_major) is not int or type(self.maximum_major) is not int
                or not 1 <= self.minimum_major <= self.maximum_major <= MAX_VERSION_COMPONENT):
            raise ValueError("invalid_supported_major_range")
        if not isinstance(self.current, ProtocolVersion):
            raise ValueError("invalid_current_protocol_version")
        if not self.minimum_major <= self.current.major <= self.maximum_major:
            raise ValueError("current_major_outside_supported_range")
        _validate_capabilities(self.capabilities)


def negotiate_protocol(
    local: ProtocolOffer,
    remote: ProtocolOffer,
    *,
    allowed_capabilities: frozenset[str],
    required_capabilities: frozenset[str] = frozenset(),
) -> dict[str, object]:
    """Return the highest mutually supported major and safe capability subset.

    Capability names are intersected with an explicit local policy allowlist.
    A peer cannot grant itself permissions by advertising extra capabilities.
    """
    if not isinstance(local, ProtocolOffer) or not isinstance(remote, ProtocolOffer):
        raise ProtocolNegotiationError("invalid_protocol_offer")
    _validate_capabilities(allowed_capabilities)
    _validate_capabilities(required_capabilities)
    if local.family != remote.family:
        raise ProtocolNegotiationError("protocol_family_mismatch")

    lowest = max(local.minimum_major, remote.minimum_major)
    highest = min(local.maximum_major, remote.maximum_major)
    if lowest > highest:
        raise ProtocolNegotiationError("no_compatible_protocol_major")

    # Do not negotiate below the local current major: this prevents silent
    # downgrade to an older major. A deliberate migration must change policy.
    if highest < local.current.major:
        raise ProtocolNegotiationError("protocol_downgrade_rejected")

    selected_major = highest
    capabilities = (
        local.capabilities
        & remote.capabilities
        & allowed_capabilities
    )
    missing = required_capabilities - capabilities
    if missing:
        raise ProtocolNegotiationError(
            "required_capabilities_missing:" + ",".join(sorted(missing))
        )

    return {
        "family": local.family,
        "selected_major": selected_major,
        "capabilities": sorted(capabilities),
        "local_software_version": local.software_version,
        "remote_software_version": remote.software_version,
        "live_execution": False,
    }
