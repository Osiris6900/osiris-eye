import unittest
from check_staging_config import check_staging_config

GOOD = dict(OSIRIS_ENV="staging", OSIRIS_API_TOKEN="token", OSIRIS_OBSERVATION_HMAC_KEY="secret", OSIRIS_TRUSTED_PEERS="peer-a", OSIRIS_LIVE_TRADING_ENABLED="0")

class StagingConfigTests(unittest.TestCase):
    def test_valid_staging(self):
        self.assertTrue(check_staging_config(GOOD)["ready"])

    def test_missing_secret_is_blocked(self):
        self.assertFalse(check_staging_config({**GOOD, "OSIRIS_API_TOKEN": ""})["ready"])

    def test_live_trading_is_blocked(self):
        self.assertFalse(check_staging_config({**GOOD, "OSIRIS_LIVE_TRADING_ENABLED": "1"})["ready"])

if __name__ == "__main__":
    unittest.main()
