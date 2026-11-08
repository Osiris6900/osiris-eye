"""End-to-end release gate regression checks: no automatic live execution."""
from osiris_api.phase14_operations import evaluate_recovery_gate


def test_recovery_gate_requires_all_independent_approvals():
    services = [{"service": "quant-api", "healthy": True}, {"service": "p2p", "healthy": True}]
    for missing in ("data_quality_pass", "risk_reconciled", "manual_approval"):
        kwargs = {"data_quality_pass": True, "risk_reconciled": True, "manual_approval": True}
        kwargs[missing] = False
        result = evaluate_recovery_gate(services, **kwargs, consecutive_healthy_checks=3)
        assert result["status"] == "recovery_blocked"
        assert result["new_risk_allowed"] is False
        assert result["live_execution_enabled"] is False


def test_recovery_gate_blocks_unhealthy_service_and_short_health_window():
    services = [{"service": "quant-api", "healthy": True}, {"service": "p2p", "healthy": False}]
    result = evaluate_recovery_gate(services, True, True, True, 2)
    assert result["new_risk_allowed"] is False
    assert result["gates"]["services_healthy"] is False
    assert result["gates"]["consecutive_health_window_met"] is False


def test_recovery_eligibility_does_not_enable_live_trading():
    result = evaluate_recovery_gate([{"service": "quant-api", "healthy": True}], True, True, True, 3)
    assert result["status"] == "recovery_eligible"
    assert result["live_execution_enabled"] is False
