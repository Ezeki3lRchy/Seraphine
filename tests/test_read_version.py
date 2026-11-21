"""tools/read_version.py: exactly one line on success, a clear stderr message and
a non-zero exit when VERSION cannot be found."""
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(ROOT, 'tools', 'read_version.py')


def _run(*args):
    return subprocess.run([sys.executable, TOOL, *args], capture_output=True,
                          text=True, timeout=60)


class ReadVersionTest(unittest.TestCase):

    def test_reads_the_real_config_as_one_line(self):
        r = _run()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(r.stdout, r'^\d+\.\d+\.\d+\r?\n$')

    def test_missing_version_or_file_fails_with_a_message(self):
        with tempfile.TemporaryDirectory() as d:
            empty = os.path.join(d, 'config.py')
            with open(empty, 'w') as f:
                f.write('BETA = False\n')
            for target in (empty, os.path.join(d, 'nope.py')):
                with self.subTest(target=os.path.basename(target)):
                    r = _run(target)
                    self.assertNotEqual(r.returncode, 0)
                    self.assertEqual(r.stdout, '')
                    self.assertIn('read_version', r.stderr)


if __name__ == '__main__':
    unittest.main()
