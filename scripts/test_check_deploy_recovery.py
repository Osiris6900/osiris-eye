import unittest
from check_deploy_recovery import validate


class DeployRecoveryTests(unittest.TestCase):
    def plan(self, **overrides):
        base = {
            "schema": "osiris.deploy-recovery-plan.v1",
            "live_trading_authorized": False,
            "executes_commands": False,
            "environments": {
                "staging": {"rollback_command": "systemctl restart osiris-staging", "drill_status": "not_run"},
                "production": {"rollback_command": "systemctl restart osiris-prod", "drill_status": "not_run"},
            },
        }
        base.update(overrides)
        return base

    def test_dry_plan_passes_without_executing(self):
        self.assertEqual(validate(self.plan()), [])

    def test_live_trading_is_rejected(self):
        self.assertTrue(validate(self.plan(live_trading_authorized=True)))

    def test_passed_drill_needs_a_url(self):
        plan = self.plan()
        plan["environments"]["staging"]["drill_status"] = "passed"
        self.assertTrue(any("evidence_url" in error for error in validate(plan)))


if __name__ == "__main__":
    unittest.main()
