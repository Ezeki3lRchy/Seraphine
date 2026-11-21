"""tools/pack_win.ps1 writes filemanifest.txt (C4) and packs with py7zr or native 7-Zip.

Windows only (runs the real script, once per installed PowerShell). Checks the
manifest bytes against hashlib / os.path.getsize, that -NoManifest writes none and
leaves filelist.txt byte-identical, and that every packer's archive passes
tools/check_archive.py (extraction with this interpreter's pinned py7zr). Native
7-Zip: when it is installed (GitHub's Windows runners), "-Packer 7z" is packed and
checked; with 7-Zip hidden, "-Packer 7z" must exit 1 and "-Packer auto" falls back
to py7zr.
"""
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

CREATE_NO_WINDOW = 0x08000000
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACK = os.path.join(ROOT, 'tools', 'pack_win.ps1')
CHECK = os.path.join(ROOT, 'tools', 'check_archive.py')

TREE = [
    ('Seraphine.exe', b'MZ' + bytes(range(256)) * 40),
    ('_internal\\base.dll', b'dll' * 5000),
    ('_internal\\app\\resource\\images\\a b.png', b'png'),
    ('_internal\\app\\resource\\i18n\\简体.qm', b'qm'),
    ('_internal\\[brackets].txt', b'wildcard-looking name'),
    ('_internal\\empty.bin', b''),
]


def _powershells():
    return [p for p in (shutil.which('powershell.exe'), shutil.which('pwsh')) if p]


def _sevenZip():
    found = shutil.which('7z')
    if found:
        return found
    pf = os.environ.get('ProgramFiles')
    path = os.path.join(pf, '7-Zip', '7z.exe') if pf else ''
    return path if path and os.path.isfile(path) else None


@unittest.skipUnless(sys.platform == 'win32', 'Windows packing script')
class PackManifestTest(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='seraphine_manifest_')
        self.addCleanup(shutil.rmtree, self.root, True)
        self.dist = os.path.join(self.root, 'dist dir', 'Seraphine')
        for rel, data in TREE:
            path = os.path.join(self.dist, *rel.split('\\'))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'wb') as f:
                f.write(data)
        # stale lists from an earlier run are replaced, never listed
        for name in ('filelist.txt', 'filemanifest.txt'):
            with open(os.path.join(self.dist, name), 'wb') as f:
                f.write(b'stale.dll\r\n')

    def run_pack(self, *args, shell=None, env=None):
        shell = shell or _powershells()[0]
        return subprocess.run(
            [shell, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
             '-File', PACK, '-DistDir', self.dist, '-Python', sys.executable, *args],
            capture_output=True, text=True, timeout=300, env=env,
            creationflags=CREATE_NO_WINDOW)

    def read(self, name):
        with open(os.path.join(self.dist, name), 'rb') as f:
            return f.read()

    def check(self, archive, *extra):
        return subprocess.run([sys.executable, CHECK, archive, self.dist, *extra],
                              capture_output=True, text=True, encoding='utf-8', timeout=300)

    def hidden7zEnv(self):
        # no 7z on PATH and no "%ProgramFiles%\7-Zip\7z.exe"
        empty = os.path.join(self.root, 'no program files')
        os.makedirs(empty, exist_ok=True)
        path = os.pathsep.join(d for d in os.environ.get('PATH', '').split(os.pathsep)
                               if d and not os.path.isfile(os.path.join(d, '7z.exe')))
        # os.environ holds Windows names upper-cased (PROGRAMFILES): drop them before
        # setting, or the child gets both spellings and still finds the real 7-Zip
        env = {k: v for k, v in os.environ.items() if k.upper() not in ('PATH', 'PROGRAMFILES')}
        env['PATH'] = path
        env['ProgramFiles'] = empty
        return env

    def test_manifest_bytes(self):
        shells = _powershells()
        self.assertTrue(shells, 'no PowerShell found')
        for shell in shells:
            with self.subTest(shell=os.path.basename(shell)):
                r = self.run_pack(shell=shell)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                raw = self.read('filemanifest.txt')
                self.assertTrue(raw.startswith(b'\xef\xbb\xbf'), 'UTF-8 BOM')
                text = raw[3:].decode('utf-8')
                self.assertTrue(text.endswith('\r\n'))
                self.assertNotRegex(text.replace('\r\n', ''), r'[\r\n﻿]')
                lines = text[:-2].split('\r\n')
                self.assertEqual(lines[0], '#seraphine-manifest 1')
                listed = self.read('filelist.txt')[3:].decode('utf-8')[:-2].split('\r\n')
                self.assertEqual([ln.split('\t')[2] for ln in lines[1:]], listed)
                self.assertEqual(sorted(listed), sorted(rel for rel, _ in TREE))
                for line in lines[1:]:
                    digest, size, rel = line.split('\t')
                    path = os.path.join(self.dist, *rel.split('\\'))
                    with open(path, 'rb') as f:
                        self.assertEqual(digest, hashlib.sha256(f.read()).hexdigest(), rel)
                    self.assertEqual(int(size), os.path.getsize(path), rel)
                    self.assertEqual(size, str(int(size)))
                self.assertNotIn('filelist.txt', listed)
                self.assertNotIn('filemanifest.txt', listed)
                self.assertNotIn('stale.dll', listed)

    def test_no_manifest_writes_none_and_keeps_filelist_bytes(self):
        for shell in _powershells():
            with self.subTest(shell=os.path.basename(shell)):
                r = self.run_pack(shell=shell)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                withManifest = self.read('filelist.txt')
                self.assertTrue(os.path.exists(os.path.join(self.dist, 'filemanifest.txt')))
                r = self.run_pack('-NoManifest', shell=shell)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertFalse(os.path.exists(os.path.join(self.dist, 'filemanifest.txt')))
                self.assertEqual(self.read('filelist.txt'), withManifest)

    def test_py7zr_and_auto_archives_pass_the_checker(self):
        for packer in ('py7zr', 'auto'):
            with self.subTest(packer=packer):
                out = os.path.join(self.root, packer + '.7z')
                r = self.run_pack('-Out', out, '-Packer', packer)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                c = self.check(out)
                self.assertEqual(c.returncode, 0, c.stderr)
                self.assertIn('manifest checked', c.stdout)

    def test_no_manifest_archive_passes_only_with_no_manifest(self):
        out = os.path.join(self.root, 'nomanifest.7z')
        r = self.run_pack('-Out', out, '-NoManifest')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        c = self.check(out, '--no-manifest')
        self.assertEqual(c.returncode, 0, c.stderr)
        self.assertIn('no manifest', c.stdout)
        self.assertEqual(self.check(out).returncode, 1)

    def test_7z_packer(self):
        out = os.path.join(self.root, 'native.7z')
        r = self.run_pack('-Out', out, '-Packer', '7z')
        if _sevenZip():
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn('packed ' + out + ' with 7-Zip', r.stdout)
            c = self.check(out)
            self.assertEqual(c.returncode, 0, c.stderr)
            self.assertIn('manifest checked', c.stdout)
            r = self.run_pack('-Out', out, '-Packer', '7z', '-NoManifest')
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(self.check(out, '--no-manifest').returncode, 0)
        else:
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn('7-Zip requested', r.stdout + r.stderr)
            self.assertFalse(os.path.exists(out))

    def test_hidden_7z_fails_7z_and_auto_falls_back(self):
        env = self.hidden7zEnv()
        out = os.path.join(self.root, 'hidden.7z')
        r = self.run_pack('-Out', out, '-Packer', '7z', env=env)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertFalse(os.path.exists(out))
        r = self.run_pack('-Out', out, '-Packer', 'auto', env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('with py7zr', r.stdout)
        self.assertEqual(self.check(out).returncode, 0)

    def test_check_methods_allows_only_plain_lzma2(self):
        # A stand-in 7z (a .cmd first on PATH, which Find-SevenZip resolves like the
        # real 7z.exe) prints a canned "7z l -slt" listing, so the allowlist itself
        # is checked without 7-Zip installed.
        fake = os.path.join(self.root, 'fake7z')
        os.makedirs(fake)
        listing = os.path.join(self.root, 'listing.txt')
        with open(os.path.join(fake, '7z.cmd'), 'w') as f:
            f.write('@echo off\r\ntype "%SERAPHINE_FAKE_7Z_LISTING%"\r\nexit /b 0\r\n')
        archive = os.path.join(self.root, 'any.7z')
        with open(archive, 'wb') as f:
            f.write(b'7z')
        env = dict(os.environ, PATH=fake + os.pathsep + os.environ.get('PATH', ''),
                   SERAPHINE_FAKE_7Z_LISTING=listing)
        cases = [
            (['Method = LZMA2:24'], 0),
            (['Method = LZMA2:24', 'Method = LZMA2:16m', 'Method ='], 0),
            (['Method = BCJ2 LZMA2:24 LZMA:20'], 1),
            (['Method = LZMA2:24', 'Method = BCJ LZMA2:24'], 1),
            (['Method = ARM64 LZMA2:24'], 1),
            (['Method = LZMA:24'], 1),
            (['Method = Copy'], 1),
            (['Path = x'], 1),                      # no Method line at all
        ]
        for lines, rc in cases:
            with self.subTest(lines=lines):
                with open(listing, 'w') as f:
                    f.write('--\r\nPath = any.7z\r\nType = 7z\r\n' + ''.join(x + '\r\n' for x in lines))
                r = subprocess.run(
                    [_powershells()[0], '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                     '-File', PACK, '-CheckMethods', archive],
                    capture_output=True, text=True, timeout=120, env=env,
                    creationflags=CREATE_NO_WINDOW)
                self.assertEqual(r.returncode, rc, r.stdout + r.stderr)
                self.assertIn('sevenzip: ' + os.path.join(fake, '7z.cmd'), r.stdout)
        # without any 7-Zip, -CheckMethods fails
        r = subprocess.run(
            [_powershells()[0], '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
             '-File', PACK, '-CheckMethods', archive],
            capture_output=True, text=True, timeout=120, env=self.hidden7zEnv(),
            creationflags=CREATE_NO_WINDOW)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn('-CheckMethods needs 7-Zip', r.stdout + r.stderr)

    def test_existing_archive_is_replaced_not_added_to(self):
        out = os.path.join(self.root, 'again.7z')
        for packer in ('py7zr',) + (('7z',) if _sevenZip() else ()):
            with self.subTest(packer=packer):
                with open(out, 'wb') as f:
                    f.write(b'old archive bytes')
                r = self.run_pack('-Out', out, '-Packer', packer)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertEqual(self.check(out).returncode, 0)


if __name__ == '__main__':
    unittest.main()
