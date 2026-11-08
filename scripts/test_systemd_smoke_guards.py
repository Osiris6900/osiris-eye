"""Prove the deployment smoke cannot overwrite an existing installation."""
import os
from pathlib import Path
import tempfile
import unittest

from test_p2p_systemd_profile import require_disposable


class GuardTests(unittest.TestCase):
    def test_only_root_on_explicit_github_hosted_runner_is_accepted(self):
        hosted = {'GITHUB_ACTIONS': 'true', 'RUNNER_ENVIRONMENT': 'github-hosted'}
        require_disposable(hosted, 0, [], False, False)
        for env, uid in (({}, 0), (hosted, 1000),
                         ({**hosted, 'RUNNER_ENVIRONMENT': 'self-hosted'}, 0)):
            with self.assertRaises(RuntimeError):
                require_disposable(env, uid, [], False, False)

    def test_existing_account_group_file_or_dangling_link_is_rejected(self):
        hosted = {'GITHUB_ACTIONS': 'true', 'RUNNER_ENVIRONMENT': 'github-hosted'}
        for account, group in ((True, False), (False, True)):
            with self.assertRaises(RuntimeError):
                require_disposable(hosted, 0, [], account, group)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'existing'
            path.write_text('operator-owned-data')
            with self.assertRaises(RuntimeError):
                require_disposable(hosted, 0, [path], False, False)
            self.assertEqual(path.read_text(), 'operator-owned-data')
            if os.name == 'posix':
                link = Path(directory) / 'link'
                link.symlink_to(Path(directory) / 'missing')
                with self.assertRaises(RuntimeError):
                    require_disposable(hosted, 0, [link], False, False)


if __name__ == '__main__':
    unittest.main()
