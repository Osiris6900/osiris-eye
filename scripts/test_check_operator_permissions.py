import unittest
from check_operator_permissions import authorize

class OperatorPermissionTests(unittest.TestCase):
    def test_viewer_read(self): self.assertTrue(authorize("viewer","read_health",True))
    def test_unauthenticated_denied(self): self.assertFalse(authorize("operator","ack_alert",False))
    def test_viewer_cannot_revoke(self): self.assertFalse(authorize("viewer","revoke_peer",True))
    def test_security_admin_can_revoke(self): self.assertTrue(authorize("security_admin","revoke_peer",True))
    def test_live_orders_denied(self): self.assertFalse(authorize("security_admin","place_live_order",True))
    def test_unknown_role_denied(self): self.assertFalse(authorize("root","revoke_peer",True))
    def test_risk_officer_can_activate_kill_switch(self):
        self.assertTrue(authorize("risk_officer", "activate_kill_switch", True))
    def test_other_roles_cannot_activate_kill_switch(self):
        for role in ("viewer", "analyst", "operator", "security_admin"):
            self.assertFalse(authorize(role, "activate_kill_switch", True))
    def test_unauthenticated_risk_officer_denied(self):
        self.assertFalse(authorize("risk_officer", "activate_kill_switch", False))
if __name__=="__main__": unittest.main()
