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

    def test_passed_drill_rejects_invalid_evidence_urls(self):
        invalid = ("", " ", "http://example.org/run", "file:///tmp/report",
                   "https://", "https://example.org/with space",
                   "https://user:pass@example.org/report", "https://example.org:443/run",
                   "https://example.org/line\nfeed", 123, None)
        for url in invalid:
            with self.subTest(url=url):
                plan = self.plan()
                plan["environments"]["staging"].update(drill_status="passed", evidence_url=url)
                self.assertTrue(any("evidence_url" in e for e in validate(plan)))

    def test_passed_drill_accepts_syntactically_valid_https_reference_only(self):
        plan = self.plan()
        plan["environments"]["staging"].update(
            drill_status="passed", evidence_url="https://github.com/example/repo/actions/runs/123")
        self.assertEqual(validate(plan), [])


if __name__ == "__main__":
    unittest.main()
