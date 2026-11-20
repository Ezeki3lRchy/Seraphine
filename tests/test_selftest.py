"""Runs main.py --version / --selftest as subprocesses from the source tree.

Always offscreen: no window is shown. --selftest imports the whole app graph, so
it is the cheap guard against a module that fails to import at startup.
"""
import os
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run(arg, code=None):
    # Fresh config location (platform.config_dir(): %APPDATA% on Windows, ~/ on
    # macOS/Linux) so the run neither reads nor writes the developer's config.
    # log/ is still created in the repo (cwd after main.py's chdir); it is git-ignored.
    with tempfile.TemporaryDirectory() as home:
        env = dict(os.environ, QT_QPA_PLATFORM='offscreen', APPDATA=home,
                   HOME=home, USERPROFILE=home, XDG_CONFIG_HOME=home)
        cmd = [sys.executable]
        if code:
            cmd += ['-c', code]
        else:
            cmd += ['main.py', arg]
        return subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True,
                              text=True, timeout=180)


class SelfTestTests(unittest.TestCase):
    def test_version_prints_version(self):
        r = _run('--version')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(r.stdout.strip().splitlines()[-1], r'^\S+$')

    def test_version_does_not_import_ui(self):
        code = (
            "import sys, runpy\n"
            "sys.argv = ['main.py', '--version']\n"
            "try:\n"
            "    runpy.run_path('main.py', run_name='__main__')\n"
            "except SystemExit:\n"
            "    pass\n"
            "bad = [m for m in ('app.view.main_window', 'qasync')"
            " if m in sys.modules]\n"
            "print('UI-IMPORTED' if bad else 'NO-UI')\n"
        )
        r = _run(None, code)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('NO-UI', r.stdout)

    def test_selftest_ok(self):
        r = _run('--selftest')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertRegex(r.stdout.strip().splitlines()[-1],
                         re.compile(r'^selftest OK \S+$'))


if __name__ == '__main__':
    unittest.main()
