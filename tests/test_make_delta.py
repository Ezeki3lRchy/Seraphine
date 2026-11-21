"""tools/make_delta.py: the Windows delta of a release (C5) and its equivalence check.

Synthetic previous / new trees in temp dirs (no network, no app import). The pass
case checks the delta's contents and that previous + delta rebuilds the new tree;
the reverse cases inject one fault each into the delta the tool packs (a flipped
byte, a changed file left out, a new file left out, a stale file not deleted) and
require exit 1 with no output file. --verify-base checks a downloaded previous
package against its API digest.
"""
import hashlib
import importlib.util
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

import py7zr

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


make_delta = _load('make_delta_under_test', os.path.join(ROOT, 'tools', 'make_delta.py'))

PREV = {
    'Seraphine.exe': b'exe-1',
    '_internal\\same.dll': b'same-bytes',
    '_internal\\sub\\keep.pyd': b'keep-unchanged',
    '_internal\\changed.dll': b'AAAA',
    '_internal\\gone.dll': b'gone',
}
NEW = {
    'Seraphine.exe': b'exe-2',
    '_internal\\same.dll': b'same-bytes',
    '_internal\\sub\\keep.pyd': b'keep-unchanged',
    '_internal\\changed.dll': b'AAAB',  # same size, one byte changed
    '_internal\\new\\added.dll': b'added',
}
DELTA_NAME = 'Seraphine-1.0.0-to-1.0.1.delta'


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f:
        f.write(data)


def filelist(entries):
    return ''.join(e + '\r\n' for e in entries).encode('utf-8-sig')


def manifest(files, entries):
    lines = ['#seraphine-manifest 1'] + ['%s\t%d\t%s' % (hashlib.sha256(files[e]).hexdigest(),
                                                       len(files[e]), e) for e in entries]
    return ('\r\n'.join(lines) + '\r\n').encode('utf-8-sig')


def writeTree(root, files, withManifest=True):
    entries = sorted(files, key=str.lower)
    for rel, data in files.items():
        _write(os.path.join(root, *rel.split('\\')), data)
    _write(os.path.join(root, 'filelist.txt'), filelist(entries))
    if withManifest:
        _write(os.path.join(root, 'filemanifest.txt'), manifest(files, entries))
    return root


def readArchive(path):
    out = {}
    with py7zr.SevenZipFile(path, 'r') as z:
        for name, bio in z.readall().items():
            out[name.replace('/', '\\')] = bio.read()
    return out


class MakeDeltaTest(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='seraphine_mkdelta_')
        self.addCleanup(shutil.rmtree, self.root, True)
        self.prev = writeTree(os.path.join(self.root, 'prev'), PREV, withManifest=False)
        self.new = writeTree(os.path.join(self.root, 'new'), NEW)
        self.out = os.path.join(self.root, 'out dir', DELTA_NAME)

    def run_tool(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = make_delta.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def assertRefused(self, what):
        rc, out, err = self.run_tool(self.prev, self.new, self.out)
        self.assertEqual(rc, 1, out + err)
        self.assertIn('make_delta: FAILED', err)
        self.assertIn(what, err)
        self.assertFalse(os.path.exists(self.out))

    def test_delta_contents_and_equivalence(self):
        rc, out, err = self.run_tool(self.prev, self.new, self.out)
        self.assertEqual(rc, 0, out + err)
        self.assertIn('previous + delta == new release', out)
        got = readArchive(self.out)
        self.assertEqual(sorted(got), sorted(['Seraphine.exe', '_internal\\changed.dll',
                                              '_internal\\new\\added.dll', 'filelist.txt',
                                              'filemanifest.txt']))
        with open(os.path.join(self.new, 'filelist.txt'), 'rb') as f:
            self.assertEqual(got['filelist.txt'], f.read())
        with open(os.path.join(self.new, 'filemanifest.txt'), 'rb') as f:
            self.assertEqual(got['filemanifest.txt'], f.read())
        self.assertEqual(got['_internal\\changed.dll'], b'AAAB')
        self.assertIn('1 stale removed', out)

    def test_unchanged_exe_is_still_shipped(self):
        same = dict(NEW, **{'Seraphine.exe': PREV['Seraphine.exe']})
        shutil.rmtree(self.new)
        writeTree(self.new, same)
        rc, out, err = self.run_tool(self.prev, self.new, self.out)
        self.assertEqual(rc, 0, out + err)
        self.assertIn('Seraphine.exe', readArchive(self.out))

    def test_packages_work_like_trees(self):
        prev7z = os.path.join(self.root, 'prev.7z')
        new7z = os.path.join(self.root, 'new.7z')
        for tree, archive in ((self.prev, prev7z), (self.new, new7z)):
            make_delta.pack(tree, archive)
        rc, out, err = self.run_tool(prev7z, new7z, self.out)
        self.assertEqual(rc, 0, out + err)

    def test_existing_output_is_removed_on_failure(self):
        _write(self.out, b'old delta')
        os.remove(os.path.join(self.new, 'filemanifest.txt'))
        self.assertRefused('no delta without a manifest')

    def test_reverse_flipped_byte_in_a_delta_file(self):
        real = make_delta._copyIntoDelta

        def flip(src, dst):
            real(src, dst)
            if dst.endswith('changed.dll'):
                with open(dst, 'r+b') as f:
                    f.write(b'Z')
        with mock.patch.object(make_delta, '_copyIntoDelta', flip):
            self.assertRefused('differs from the new release in _internal\\changed.dll')

    def test_reverse_changed_file_left_out(self):
        real = make_delta.selectFiles
        with mock.patch.object(make_delta, 'selectFiles',
                               lambda *a: [r for r in real(*a) if r != '_internal\\changed.dll']):
            self.assertRefused('differs from the new release in _internal\\changed.dll')

    def test_reverse_new_file_left_out(self):
        real = make_delta.selectFiles
        with mock.patch.object(make_delta, 'selectFiles',
                               lambda *a: [r for r in real(*a) if r != '_internal\\new\\added.dll']):
            self.assertRefused('previous + delta lacks _internal\\new\\added.dll')

    def test_reverse_stale_deletion_skipped(self):
        with mock.patch.object(make_delta, 'staleEntries', lambda *a: []):
            self.assertRefused('has _internal\\gone.dll, the new release does not')

    def test_new_tree_must_match_its_manifest(self):
        _write(os.path.join(self.new, '_internal', 'same.dll'), b'same-bytez')
        self.assertRefused('sha256 of _internal\\same.dll differs')

    def test_unlisted_file_in_the_new_tree_is_refused(self):
        _write(os.path.join(self.new, 'log', 'x.log'), b'log')
        self.assertRefused('does not list log\\x.log')

    def test_output_name_must_be_a_delta_name(self):
        rc, out, err = self.run_tool(self.prev, self.new, os.path.join(self.root, 'Seraphine-1.0.0-to-1.0.1.7z'))
        self.assertEqual(rc, 1)
        self.assertIn('is not named', err)

    def test_verify_base(self):
        pkg = os.path.join(self.root, 'Seraphine.7z')
        _write(pkg, b'previous package')
        good = 'sha256:' + hashlib.sha256(b'previous package').hexdigest()
        self.assertEqual(self.run_tool('--verify-base', good, pkg)[0], 0)
        self.assertEqual(self.run_tool('--verify-base', good.upper().replace('SHA256', 'sha256'), pkg)[0], 0)
        bad = 'sha256:' + hashlib.sha256(b'another package').hexdigest()
        for digest in (bad, '', 'md5:abc', 'sha256:', good[:-1]):
            with self.subTest(digest=digest):
                rc, out, err = self.run_tool('--verify-base', digest, pkg)
                self.assertEqual(rc, 1, out)
                self.assertIn('base FAILED', err)
        self.assertEqual(self.run_tool('--verify-base', good, pkg + '.missing')[0], 1)

    def fakeGh(self, served, digest, name='Seraphine.7z'):
        """A stand-in for gh: `api .../releases/tags/v1.0.0` reports ``digest`` for ``name``,
        `release download` writes ``served`` (the bytes GitHub would serve)."""
        calls = []

        def gh(args):
            calls.append(args[:2])
            if args[0] == 'api' and args[1].endswith('/releases/tags/v1.0.0'):
                return json.dumps({'assets': [{'name': 'Seraphine.dmg', 'digest': 'sha256:' + '0' * 64},
                                              {'name': name, 'digest': digest}]})
            if args[:2] == ['release', 'download']:
                dest = args[args.index('--dir') + 1]
                _write(os.path.join(dest, args[args.index('--pattern') + 1]), served)
                return ''
            raise AssertionError(args)
        return gh, calls

    def fetch(self, gh):
        dest = os.path.join(self.root, 'base dir')
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = make_delta.main(['--fetch-base', '1.0.0', dest], gh=gh)
        return rc, out.getvalue() + err.getvalue(), os.path.join(dest, 'Seraphine.7z')

    def test_fetch_base_verifies_the_api_digest(self):
        data = b'the published previous package'
        good = 'sha256:' + hashlib.sha256(data).hexdigest()
        gh, calls = self.fakeGh(data, good)
        rc, out, path = self.fetch(gh)
        self.assertEqual(rc, 0, out)
        self.assertIn('base OK', out)
        with open(path, 'rb') as f:
            self.assertEqual(f.read(), data)

    def test_fetch_base_reverse_digest_mismatch(self):
        # the served package is not the one the API digest describes (E5)
        gh, calls = self.fakeGh(b'some other package',
                                'sha256:' + hashlib.sha256(b'the published previous package').hexdigest())
        rc, out, path = self.fetch(gh)
        self.assertEqual(rc, 1, out)
        self.assertIn('base FAILED', out)
        self.assertIn('differs from the digest', out)
        self.assertFalse(os.path.exists(path))

    def test_fetch_base_without_digest_downloads_nothing(self):
        for digest in ('', None, 'md5:abc'):
            with self.subTest(digest=digest):
                gh, calls = self.fakeGh(b'pkg', digest)
                rc, out, path = self.fetch(gh)
                self.assertEqual(rc, 1, out)
                self.assertIn('has no sha256 digest', out)
                self.assertNotIn(['release', 'download'], calls)
        gh, calls = self.fakeGh(b'pkg', 'sha256:' + '1' * 64, name='Seraphine-win.7z')
        rc, out, path = self.fetch(gh)
        self.assertEqual(rc, 1)
        self.assertIn('has no Seraphine.7z', out)

    def repack(self, drop=(), flip=None):
        """A copy of the built delta without ``drop`` and with one byte of ``flip``
        changed, under the delta name (what a damaged artifact would be)."""
        got = readArchive(self.out)
        tree = os.path.join(self.root, 'repacked')
        shutil.rmtree(tree, ignore_errors=True)
        for rel, data in got.items():
            if rel in drop:
                continue
            if rel == flip:
                data = b'Z' + data[1:]
            _write(os.path.join(tree, *rel.split('\\')), data)
        bad = os.path.join(self.root, 'bad', DELTA_NAME)
        os.makedirs(os.path.dirname(bad), exist_ok=True)
        make_delta.pack(tree, bad)
        return bad

    def test_check_existing_delta(self):
        self.assertEqual(self.run_tool(self.prev, self.new, self.out)[0], 0)
        rc, out, err = self.run_tool('--check', self.prev, self.new, self.out)
        self.assertEqual(rc, 0, out + err)
        self.assertIn('check OK', out)
        self.assertEqual(self.run_tool('--check', self.prev, self.new, self.repack())[0], 0)
        for kw, what in ((dict(flip='_internal\\changed.dll'), 'differs from the new release in _internal\\changed.dll'),
                         (dict(drop=('_internal\\changed.dll',)), 'differs from the new release in _internal\\changed.dll'),
                         (dict(drop=('_internal\\new\\added.dll',)), 'lacks _internal\\new\\added.dll'),
                         (dict(drop=('filemanifest.txt',)), 'filemanifest.txt'),
                         (dict(drop=('Seraphine.exe',)), 'Seraphine.exe')):
            with self.subTest(**kw):
                rc, out, err = self.run_tool('--check', self.prev, self.new, self.repack(**kw))
                self.assertEqual(rc, 1, out + err)
                self.assertIn('check FAILED', err)
                self.assertIn(what, err)
        # a delta for another base: the previous tree lacks the stale-file deletion
        other = writeTree(os.path.join(self.root, 'other prev'), dict(PREV, **{'_internal\\extra.dll': b'x'}),
                          withManifest=False)
        with open(os.path.join(other, 'filelist.txt'), 'wb') as f:
            f.write(filelist(sorted(PREV, key=str.lower)))  # extra.dll listed nowhere
        rc, out, err = self.run_tool('--check', other, self.new, self.out)
        self.assertEqual(rc, 1, out + err)
        self.assertIn('_internal\\extra.dll', err)


if __name__ == '__main__':
    unittest.main()
