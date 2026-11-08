from osiris_api.operator_health_gate import project_health

BASE = dict(authenticated=True, authorized_role=True, feed_age_seconds=1, peer_count=2, kill_switch=False)

def test_healthy_projection():
    assert project_health(**BASE).status == "healthy"

def test_denies_unauthorized_operator():
    assert project_health(**{**BASE, "authorized_role": False}).status == "denied"

def test_degrades_on_stale_feed_and_kill_switch():
    result = project_health(**{**BASE, "feed_age_seconds": float("nan"), "kill_switch": True})
    assert result.status == "degraded"
    assert "kill_switch_active" in result.alerts
