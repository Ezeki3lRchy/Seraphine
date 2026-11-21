"""tools/check_archive.py: a package passes only when py7zr extracts it to exactly its tree.

The trees here get their filelist.txt / filemanifest.txt from an independent writer
in this test (the C4 format), so the checker is tested on its own; tools/pack_win.ps1's
output is checked in tests/test_pack_manifest.py. Each failure case must exit 1.
"""
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import py7zr

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(ROOT, 'tools', 'check_archive.py')

TREE = {
    'Seraphine.exe': b'MZ exe',
    '_internal\\base.dll': b'dll' * 1000,
    '_internal\\app\\resource\\images\\a b.png': b'png',
    '_internal\\app\\resource\\i18n\\简体.qm': b'qm',
    '_internal\\empty.txt': b'',
}


def writeLists(tree, entries, manifest=True, header='#seraphine-manifest 1'):
    with open(os.path.join(tree, 'filelist.txt'), 'wb') as f:
        f.write(b'\xef\xbb\xbf' + ''.join(e + '\r\n' for e in entries).encode('utf-8'))
    if manifest:
        lines = [header]
        for e in entries:
            with open(os.path.join(tree, *e.split('\\')), 'rb') as f:
                data = f.read()
            lines.append('%s\t%d\t%s' % (hashlib.sha256(data).hexdigest(), len(data), e))
        with open(os.path.join(tree, 'filemanifest.txt'), 'wb') as f:
            f.write(b'\xef\xbb\xbf' + ''.join(x + '\r\n' for x in lines).encode('utf-8'))


def pack(tree, out, skip=()):
    with py7zr.SevenZipFile(out, 'w') as z:
        for d, _, names in os.walk(tree):
            for n in names:
                full = os.path.join(d, n)
                rel = os.path.relpath(full, tree)
                if rel.replace(os.sep, '\\') not in skip:
                    z.write(full, arcname=rel)


class CheckArchiveTest(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='seraphine_checkarc_')
        self.addCleanup(shutil.rmtree, self.root, True)
        self.tree = os.path.join(self.root, 'tree')
        for rel, data in TREE.items():
            self.put(rel, data)
        self.entries = list(TREE)
        writeLists(self.tree, self.entries)
        self.archive = os.path.join(self.root, 'Seraphine.7z')

    def put(self, rel, data, tree=None):
        path = os.path.join(tree or self.tree, *rel.split('\\'))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            f.write(data)

    def check(self, *extra, archive=None):
        return subprocess.run([sys.executable, TOOL, archive or self.archive, self.tree, *extra],
                              capture_output=True, text=True, encoding='utf-8', timeout=120)

    def assertFails(self, r, needle):
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn('FAILED', r.stderr)
        self.assertIn(needle, r.stderr)
        self.assertNotIn('OK', r.stdout)

    def test_py7zr_archive_passes(self):
        pack(self.tree, self.archive)
        r = self.check()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('check_archive: OK', r.stdout)
        self.assertIn('7 files, filelist.txt 5 entries, manifest checked (py7zr %s)'
                      % py7zr.__version__, r.stdout)

    def test_tampered_file_fails(self):
        pack(self.tree, self.archive)
        self.put('_internal\\base.dll', b'dlX' + b'dll' * 999)     # same size, one byte
        self.assertFails(self.check(), 'content differs: _internal\\base.dll')

    def test_missing_file_fails(self):
        pack(self.tree, self.archive, skip={'_internal\\base.dll'})
        self.assertFails(self.check(), 'missing from the archive: _internal\\base.dll')

    def test_extra_file_fails(self):
        pack(self.tree, self.archive)
        os.remove(os.path.join(self.tree, 'Seraphine.exe'))
        self.assertFails(self.check(), 'not in the tree: Seraphine.exe')

    def test_truncated_archive_fails(self):
        pack(self.tree, self.archive)
        with open(self.archive, 'rb') as f:
            data = f.read()
        with open(self.archive, 'wb') as f:
            f.write(data[:len(data) // 2])
        self.assertFails(self.check(), 'cannot extract')

    def test_not_an_archive_and_no_archive_fail(self):
        with open(self.archive, 'wb') as f:
            f.write(b'not a 7z at all')
        self.assertFails(self.check(), 'cannot extract')
        self.assertFails(self.check(archive=os.path.join(self.root, 'none.7z')), 'no archive')

    def test_manifest_required_unless_no_manifest(self):
        os.remove(os.path.join(self.tree, 'filemanifest.txt'))
        pack(self.tree, self.archive)
        self.assertFails(self.check(), 'no filemanifest.txt at the archive root')
        r = self.check('--no-manifest')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('no manifest', r.stdout)

    def test_filelist_required(self):
        os.remove(os.path.join(self.tree, 'filelist.txt'))
        pack(self.tree, self.archive)
        self.assertFails(self.check('--no-manifest'), 'no filelist.txt at the archive root')

    def test_unlisted_file_fails(self):
        self.put('_internal\\stray.dll', b'stray')
        pack(self.tree, self.archive)
        self.assertFails(self.check(), 'filelist.txt does not list _internal\\stray.dll')

    def test_listed_but_absent_file_fails_unless_partial(self):
        # a delta: the lists describe the whole release, the archive holds some files
        os.remove(os.path.join(self.tree, '_internal', 'base.dll'))
        writeLists(self.tree, self.entries, manifest=False)
        full = dict(TREE)
        lines = ['#seraphine-manifest 1'] + [
            '%s\t%d\t%s' % (hashlib.sha256(full[e]).hexdigest(), len(full[e]), e) for e in self.entries]
        with open(os.path.join(self.tree, 'filemanifest.txt'), 'wb') as f:
            f.write(b'\xef\xbb\xbf' + ''.join(x + '\r\n' for x in lines).encode('utf-8'))
        pack(self.tree, self.archive)
        self.assertFails(self.check(), 'filelist.txt lists _internal\\base.dll, the archive does not hold it')
        r = self.check('--partial')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('6 files, filelist.txt 5 entries, manifest checked', r.stdout)

    def test_manifest_with_a_wrong_hash_or_size_fails(self):
        path = os.path.join(self.tree, 'filemanifest.txt')
        with open(path, 'rb') as f:
            good = f.read()
        digest = hashlib.sha256(TREE['Seraphine.exe']).hexdigest().encode()
        size = b'\t%d\tSeraphine.exe' % len(TREE['Seraphine.exe'])
        cases = {
            'sha256 of Seraphine.exe differs': good.replace(digest, b'0' * 64),
            'size of Seraphine.exe is 7': good.replace(size, size.replace(b'\t6\t', b'\t7\t')),
        }
        for needle, data in cases.items():
            with self.subTest(needle=needle):
                self.assertNotEqual(data, good)
                with open(path, 'wb') as f:
                    f.write(data)
                pack(self.tree, self.archive)
                self.assertFails(self.check(), needle)

    def test_manifest_format_is_enforced(self):
        path = os.path.join(self.tree, 'filemanifest.txt')
        with open(path, 'rb') as f:
            good = f.read()
        cases = {
            'first line is': good.replace(b'#seraphine-manifest 1', b'#seraphine-manifest 2'),
            'no UTF-8 BOM': good[3:],
            'not CRLF-separated': good.replace(b'\r\n', b'\n', 2),
            'entries, filelist.txt has': good[:good.rindex(b'\r\n', 0, len(good) - 2) + 2],
            'not <sha256>': good.replace(hashlib.sha256(TREE['Seraphine.exe']).hexdigest().encode(),
                                         hashlib.sha256(TREE['Seraphine.exe']).hexdigest().upper().encode()),
        }
        for needle, data in cases.items():
            with self.subTest(needle=needle):
                self.assertNotEqual(data, good)
                with open(path, 'wb') as f:
                    f.write(data)
                pack(self.tree, self.archive)
                self.assertFails(self.check(), needle)

    def test_manifest_out_of_filelist_order_fails(self):
        writeLists(self.tree, self.entries)
        lst = os.path.join(self.tree, 'filelist.txt')
        writeLists(self.tree, list(reversed(self.entries)))     # manifest in the other order
        with open(lst, 'wb') as f:
            f.write(b'\xef\xbb\xbf' + ''.join(e + '\r\n' for e in self.entries).encode('utf-8'))
        pack(self.tree, self.archive)
        self.assertFails(self.check(), 'has Seraphine.exe at that place')


if __name__ == '__main__':
    unittest.main()
