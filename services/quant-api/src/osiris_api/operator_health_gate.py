"""Operator-facing health projection without exposing credentials or raw peer payloads."""
from dataclasses import dataclass


@dataclass(frozen=True)
class HealthProjection:
    status: str
    alerts: tuple[str, ...]


def project_health(*, authenticated: bool, authorized_role: bool, feed_age_seconds: float,
                   peer_count: int, kill_switch: bool, max_feed_age_seconds: float = 30) -> HealthProjection:
    import math
    if authenticated is not True or authorized_role is not True:
        return HealthProjection("denied", ("operator_not_authorized",))
    alerts = []
    if not isinstance(peer_count, int) or isinstance(peer_count, bool) or peer_count < 1:
        alerts.append("no_healthy_peers")
    if not isinstance(feed_age_seconds, (int, float)) or isinstance(feed_age_seconds, bool) or not math.isfinite(feed_age_seconds) or feed_age_seconds < 0 or feed_age_seconds > max_feed_age_seconds:
        alerts.append("feed_stale_or_invalid")
    if kill_switch is not False:
        alerts.append("kill_switch_active")
    return HealthProjection("degraded" if alerts else "healthy", tuple(alerts))
