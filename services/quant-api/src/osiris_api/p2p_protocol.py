"""Version and capability negotiation primitives for future P2P handshakes.

This module does not open sockets or enable peer discovery. It provides a
deterministic policy function for a transport handshake to call before accepting
a peer.
"""
from __future__ import annotations

from dataclasses import dataclass


class ProtocolNegotiationError(ValueError):
    """Raised when two peers cannot safely negotiate a protocol."""


@dataclass(frozen=True, order=True)
class ProtocolVersion:
    major: int
    minor: int = 0
    patch: int = 0

    def __post_init__(self) -> None:
        if min(self.major, self.minor, self.patch) < 0:
            raise ValueError("protocol_version_components_must_be_non_negative")
        if self.major == 0:
            raise ValueError("protocol_major_must_be_at_least_one")

    @classmethod
    def parse(cls, value: str) -> "ProtocolVersion":
        parts = value.split(".")
        if len(parts) != 3 or any(not p.isdigit() for p in parts):
            raise ValueError("invalid_protocol_version")
        return cls(*(int(p) for p in parts))

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
        if not self.family or not self.software_version:
            raise ValueError("protocol_family_and_software_version_required")
        if self.minimum_major < 1 or self.maximum_major < self.minimum_major:
            raise ValueError("invalid_supported_major_range")
        if not self.minimum_major <= self.current.major <= self.maximum_major:
            raise ValueError("current_major_outside_supported_range")
        if any(not item or item.strip() != item for item in self.capabilities):
            raise ValueError("invalid_capability_name")


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
