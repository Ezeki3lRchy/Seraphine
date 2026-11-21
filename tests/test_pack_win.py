"""tools/pack_win.ps1 writes the filelist.txt the in-place updater reads.

Windows only. The shared script (used by release_win.ps1 and the build_windows.yml
workflow) is run for real on a small temp tree, once per PowerShell that is
installed, and the result is checked against the contract the updater relies on:
UTF-8 with BOM, CRLF line endings, one path per line relative to the dist root
with backslash separators, filelist.txt not listing itself. The updater's own
reader (Read-FileList, taken from update.WINDOWS_UPDATER_SCRIPT) must then return
exactly the files of the tree.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from app.common import update

CREATE_NO_WINDOW = 0x08000000
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACK = os.path.join(ROOT, 'tools', 'pack_win.ps1')

# (relative path with backslashes, content); a space and non-ASCII on purpose
TREE = [
    ('Seraphine.exe', b'exe'),
    ('_internal\\base.dll', b'dll'),
    ('_internal\\app\\resource\\images\\a b.png', b'png'),
    ('_internal\\app\\resource\\i18n\\简体.qm', b'qm'),
]


def _powershells():
    found = []
    for exe in ('powershell.exe', 'pwsh'):
        path = shutil.which(exe)
        if path:
            found.append(path)
    return found


def _updaterReadFileList():
    m = re.search(r'^function Read-FileList.*?^}\r?$', update.WINDOWS_UPDATER_SCRIPT,
                  re.S | re.M)
    assert m, 'Read-FileList not found in the updater script'
    return m.group(0)


@unittest.skipUnless(sys.platform == 'win32', 'Windows packing script')
class PackWinFilelistTest(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='seraphine_pack_')
        self.addCleanup(shutil.rmtree, self.root, True)
        self.dist = os.path.join(self.root, 'dist dir', 'Seraphine')
        for rel, data in TREE:
            path = os.path.join(self.dist, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'wb') as f:
                f.write(data)
        # a stale list from an earlier run must be replaced, not listed
        with open(os.path.join(self.dist, 'filelist.txt'), 'wb') as f:
            f.write(b'stale.dll\r\n')

    def _run(self, shell, args):
        return subprocess.run(
            [shell, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass'] + args,
            capture_output=True, text=True, timeout=120,
            creationflags=CREATE_NO_WINDOW)

    def _pack(self, shell):
        r = self._run(shell, ['-File', PACK, '-DistDir', self.dist])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        with open(os.path.join(self.dist, 'filelist.txt'), 'rb') as f:
            return f.read()

    def test_filelist_contract(self):
        shells = _powershells()
        self.assertTrue(shells, 'no PowerShell found')
        expected = sorted(rel for rel, _ in TREE)
        for shell in shells:
            with self.subTest(shell=os.path.basename(shell)):
                raw = self._pack(shell)
                self.assertTrue(raw.startswith(b'\xef\xbb\xbf'), 'UTF-8 BOM')
                text = raw[3:].decode('utf-8')
                self.assertTrue(text.endswith('\r\n'))
                self.assertNotIn('﻿', text)
                # CRLF everywhere: removing the CRLFs leaves no CR and no LF
                self.assertNotRegex(text.replace('\r\n', ''), r'[\r\n]')
                entries = text[:-2].split('\r\n')
                self.assertEqual(sorted(entries), expected)
                self.assertNotIn('filelist.txt', entries)
                self.assertNotIn('stale.dll', entries)
                for e in entries:
                    self.assertNotIn('/', e)
                    self.assertFalse(e.startswith('\\') or ':' in e, e)

    def test_short_name_dist_dir_still_gives_relative_entries(self):
        # A dist dir given by its 8.3 short name, the way GitHub's runner spells its
        # temp dir (a RUNNER~1 component): the enumeration returns long names, so a
        # prefix taken from the path as given would not match and absolute paths
        # would end up in filelist.txt.
        import ctypes
        buf = ctypes.create_unicode_buffer(1024)
        n = ctypes.windll.kernel32.GetShortPathNameW(self.dist, buf, len(buf))
        short = buf.value if 0 < n < len(buf) else ''
        if not short or short.lower() == self.dist.lower():
            self.skipTest('no 8.3 short name for the temp dir on this volume')
        expected = sorted(rel for rel, _ in TREE)
        for shell in _powershells():
            with self.subTest(shell=os.path.basename(shell)):
                r = self._run(shell, ['-File', PACK, '-DistDir', short])
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                with open(os.path.join(self.dist, 'filelist.txt'), 'rb') as f:
                    entries = f.read()[3:].decode('utf-8')[:-2].split('\r\n')
                self.assertEqual(sorted(entries), expected)

    def test_updaters_reader_returns_exactly_the_files(self):
        shell = _powershells()[0]
        self._pack(shell)
        script = _updaterReadFileList() + (
            '\n$e = Read-FileList $env:SERAPHINE_FILELIST\n'
            '[Console]::OutputEncoding = [System.Text.Encoding]::UTF8\n'
            '$e | ForEach-Object { [Console]::Out.WriteLine($_) }\n')
        env = dict(os.environ,
                   SERAPHINE_FILELIST=os.path.join(self.dist, 'filelist.txt'))
        r = subprocess.run(
            [shell, '-NoProfile', '-NonInteractive', '-Command', script],
            capture_output=True, timeout=120, env=env,
            creationflags=CREATE_NO_WINDOW)
        self.assertEqual(r.returncode, 0, r.stderr)
        got = r.stdout.decode('utf-8').splitlines()
        self.assertEqual(sorted(got), sorted(rel for rel, _ in TREE))

    def _runUnderContinue(self, dist, out, python):
        # release_win.ps1 runs with the default 'Continue' preference: a failure
        # must still stop the script with a non-zero exit code
        cmd = ("$ErrorActionPreference = 'Continue'; "
               "& '%s' -DistDir '%s' -Out '%s' -Python '%s'; exit $LASTEXITCODE"
               % (PACK, dist, out, python))
        return self._run(_powershells()[0], ['-Command', cmd])

    def test_missing_dist_dir_fails_and_produces_no_archive(self):
        out = os.path.join(self.root, 'out.7z')
        missing = os.path.join(self.root, 'no such dist')
        r = self._runUnderContinue(missing, out, sys.executable)
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertFalse(os.path.exists(out))

    def test_failing_packer_fails_and_produces_no_archive(self):
        out = os.path.join(self.root, 'out.7z')
        r = self._runUnderContinue(self.dist, out, os.path.join(self.root, 'no-python.exe'))
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertFalse(os.path.exists(out))


if __name__ == '__main__':
    unittest.main()
