"""tools/update_e2e.py: a new release installed over the previous one by the real
Windows updater scripts (the C9 snapshots of v1.4.6 and v1.4.8 and the new script).

Windows only (runs PowerShell). Synthetic previous / new trees packed as .7z; the
delta is built by tools/make_delta.py. Reverse cases: a new package corrupted after
its manifest was written, and a delta that lacks one changed file, each exit 1.
Unit tests read the committed snapshots only; the release gate itself runs the
tool with real `git show` of every self-updating tag (verify_update_windows.yml).
"""
import hashlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

import py7zr

from tests.test_make_delta import NEW, PREV, make_delta, writeTree

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(ROOT, 'tools', 'update_e2e.py')
LEGACY = os.path.join(ROOT, 'tests', 'fixtures', 'legacy')
SCRIPTS = ['--script', 'v1.4.6=' + os.path.join(LEGACY, 'update_v1_4_6.py.txt'),
           '--script', 'v1.4.8=' + os.path.join(LEGACY, 'update_v1_4_8.py.txt')]
USER = {'_internal\\app\\resource\\game\\cache.png': b'user-cache'}  # in no filelist


@unittest.skipUnless(sys.platform == 'win32', 'runs the PowerShell updater scripts')
class UpdateE2eTest(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="seraphine e2e's é")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.prevTree = writeTree(os.path.join(self.root, 'prev'), PREV, withManifest=False)
        self.newTree = writeTree(os.path.join(self.root, 'new'), NEW)
        self.prev7z = os.path.join(self.root, 'prev.7z')
        self.new7z = os.path.join(self.root, 'Seraphine.7z')
        make_delta.pack(self.prevTree, self.prev7z)
        make_delta.pack(self.newTree, self.new7z)
        self.delta = os.path.join(self.root, 'Seraphine-1.0.0-to-1.0.1.delta')

    def run_tool(self, *args):
        r = subprocess.run([sys.executable, TOOL, '--prev-package', self.prev7z, '--version', '1.0.1']
                           + list(args), capture_output=True, text=True, encoding='utf-8',
                           errors='replace', timeout=900)
        return r.returncode, r.stdout + r.stderr

    def test_full_and_delta_legs_pass(self):
        with redirect_stdout(io.StringIO()) as made:
            self.assertEqual(make_delta.main([self.prevTree, self.newTree, self.delta]), 0)
        self.assertIn('previous + delta == new release', made.getvalue())
        rc, out = self.run_tool('--new-package', self.new7z, '--delta', self.delta, *SCRIPTS)
        self.assertEqual(rc, 0, out)
        for line in ('update_e2e: full  v1.4.6: OK', 'update_e2e: full  v1.4.8: OK',
                     'update_e2e: full  new: OK', 'update_e2e: delta new: OK',
                     'update_e2e: OK: 4 runs'):
            self.assertIn(line, out)
        self.assertIn("v1.4.8 has no '# seraphine-updater: manifest 1' line", out)

    def test_install_with_untouched_user_file_and_new_tree_dir(self):
        # a previous install that has a file in no filelist: every script must leave it
        prev = os.path.join(self.root, 'prev-user')
        shutil.copytree(self.prevTree, prev)
        for rel, data in USER.items():
            path = os.path.join(prev, *rel.split('\\'))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'wb') as f:
                f.write(data)
        want = os.path.join(self.root, 'want')
        shutil.copytree(self.newTree, want)
        shutil.copytree(os.path.join(prev, '_internal', 'app'), os.path.join(want, '_internal', 'app'))
        r = subprocess.run([sys.executable, TOOL, '--prev-package', prev, '--new-package', self.new7z,
                            '--new-tree', want] + SCRIPTS, capture_output=True, text=True, timeout=900)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('update_e2e: OK: 3 runs', r.stdout)

    def test_previous_install_with_a_manifest_runs_every_variant(self):
        # a manual install from a package with filemanifest.txt keeps it at the root;
        # a self-updated one has none; a stale one matches nothing
        prev = writeTree(os.path.join(self.root, 'prev-manifest'), PREV)
        prev7z = os.path.join(self.root, 'prev-manifest.7z')
        make_delta.pack(prev, prev7z)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(make_delta.main([prev, self.newTree, self.delta]), 0)
        r = subprocess.run([sys.executable, TOOL, '--prev-package', prev7z, '--new-package', self.new7z,
                            '--delta', self.delta, '--version', '1.0.1'] + SCRIPTS,
                           capture_output=True, text=True, timeout=900)
        out = r.stdout + r.stderr
        self.assertEqual(r.returncode, 0, out)
        self.assertIn('update_e2e: OK: 12 runs', out)  # (3 full + 1 delta) x 3 installs
        for label in ('full  new [install without filemanifest.txt]: OK',
                      'full  v1.4.6 [install with a mismatching filemanifest.txt]: OK',
                      'delta new [install without filemanifest.txt]: OK',
                      'delta new [install with a mismatching filemanifest.txt]: OK'):
            self.assertIn('update_e2e: ' + label, out)

    def test_reverse_corrupted_new_package(self):
        # one byte flipped after filemanifest.txt was written
        with open(os.path.join(self.newTree, '_internal', 'changed.dll'), 'r+b') as f:
            f.write(b'Z')
        make_delta.pack(self.newTree, self.new7z)
        rc, out = self.run_tool('--new-package', self.new7z, *SCRIPTS)
        self.assertEqual(rc, 1, out)
        self.assertIn('update_e2e: FAILED: 3 of 3 runs', out)
        self.assertIn('the install differs from the package manifest in _internal\\changed.dll', out)
        self.assertIn('result failed:9', out)  # the new script refuses it before changing anything

    def test_reverse_delta_missing_a_changed_file(self):
        bad = os.path.join(self.root, 'bad delta')
        for rel in ('Seraphine.exe', '_internal\\new\\added.dll', 'filelist.txt', 'filemanifest.txt'):
            dst = os.path.join(bad, *rel.split('\\'))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(os.path.join(self.newTree, *rel.split('\\')), dst)
        make_delta.pack(bad, self.delta)
        with py7zr.SevenZipFile(self.delta, 'r') as z:
            self.assertNotIn('_internal/changed.dll', z.getnames())
        rc, out = self.run_tool('--new-package', self.new7z, '--delta', self.delta, '--leg', 'delta')
        self.assertEqual(rc, 1, out)
        self.assertIn('update_e2e: delta new: FAILED', out)
        self.assertIn('result failed:9', out)

    def test_script_that_cannot_be_read_fails_the_run(self):
        rc, out = self.run_tool('--new-package', self.new7z,
                                '--script', 'v9=' + os.path.join(self.root, 'missing.py'))
        self.assertEqual(rc, 1, out)
        self.assertIn('cannot read script', out)

    def test_new_script_must_carry_the_marker(self):
        legacy = os.path.join(LEGACY, 'update_v1_4_8.py.txt')
        rc, out = self.run_tool('--new-package', self.new7z, '--new-script', legacy)
        self.assertEqual(rc, 1, out)
        self.assertIn('the new script lacks the line', out)

    def test_snapshots_are_the_recorded_bytes(self):
        # the files this test runs are the C9 snapshots (LF bytes of git show)
        for name, sha in (('update_v1_4_6.py.txt',
                           'be01edd0264cca0b937b4a6437a8b92fc6cd735280749cae05fe2c404df34464'),
                          ('update_v1_4_8.py.txt',
                           '1b1aed03114ff366f6d9056b5fc1e25ecaa0c6c278c892478ac3ad51cd19dbcb')):
            with open(os.path.join(LEGACY, name), 'rb') as f:
                data = f.read()
            self.assertNotIn(b'\r', data)
            self.assertEqual(hashlib.sha256(data).hexdigest(), sha)


if __name__ == '__main__':
    unittest.main()
