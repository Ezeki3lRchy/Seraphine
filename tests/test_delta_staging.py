"""Windows delta staging with prechecks and a bounded fallback
(app/common/update_stage.py, app/common/util.py).

A delta (``Seraphine-<base>-to-<new>.delta``) is downloaded only when the running
version is exactly its base and the asset has a sha256 digest; it is staged only if
it fits the install (the Python mirror of the updater's step 4b), otherwise the full
package is staged in the same call. ``update-fallback.json`` (outside the stage dir)
records failed deltas and failed:9 runs; two distinct failed:9 runs block a version.
ver.json's ``noDelta`` / ``noResume`` switch the new paths off without a release.

No network (a fake session serves the assets), every stage dir, install and fallback
file is a temp path, no windows. The archives are tiny real .7z files (py7zr). The
Windows platform is set explicitly, so the module also runs on macOS.
"""
import asyncio
import base64
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import aiohttp

from app.common import platform
from app.common import update_stage as us
from app.common import util
from app.common.config import VERSION
from app.common.release_names import FULL_WIN, MANIFEST_ASSET, deltaName
from tests.test_download_resume import RangeResp

_MAJOR, _MINOR, _PATCH = (int(n) for n in VERSION.split('.'))
NEW = f'{_MAJOR}.{_MINOR}.{_PATCH + 1}'           # the release offered
OLDER = f'{_MAJOR}.{_MINOR}.{max(_PATCH - 1, 0)}'  # a base one version behind

INSTALL = {  # the running version's install (the delta's base)
    'Seraphine.exe': b'old-exe',
    '_internal/a.dll': b'same-a',
    '_internal/b.dll': b'old-b',
    '_internal/sub/c.pyd': b'same-c',
}
RELEASE = {  # the release: Seraphine.exe and b.dll changed
    'Seraphine.exe': b'new-exe',
    '_internal/a.dll': b'same-a',
    '_internal/b.dll': b'new-b',
    '_internal/sub/c.pyd': b'same-c',
}
DELTA_FILES = ('Seraphine.exe', '_internal/b.dll')

FULL_URL = 'https://example.invalid/' + FULL_WIN
DELTA_URL = 'https://example.invalid/' + deltaName(VERSION, NEW)
MANIFEST_URL = 'https://example.invalid/' + MANIFEST_ASSET


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f:
        f.write(data)


def _win(rel):
    return rel.replace('/', '\\')


def filelistBytes(files):
    return ''.join(_win(r) + '\r\n' for r in files).encode('utf-8-sig')


def manifestBytes(files, header='#seraphine-manifest 1'):
    """C4: UTF-8 BOM, CRLF, header, then sha256 TAB size TAB backslash path."""
    lines = [header] + [f'{hashlib.sha256(d).hexdigest()}\t{len(d)}\t{_win(r)}'
                        for r, d in files.items()]
    return ('\r\n'.join(lines) + '\r\n').encode('utf-8-sig')


def sevenZip(tmp, files):
    """Bytes of a .7z holding ``files`` (rel -> bytes) at the archive root."""
    import py7zr

    src = tempfile.mkdtemp(prefix='seraphine_delta_src_', dir=tmp)
    path = src + '.7z'
    for rel, data in files.items():
        _write(os.path.join(src, *rel.split('/')), data)
    with py7zr.SevenZipFile(path, 'w') as z:
        for rel in files:
            z.write(os.path.join(src, *rel.split('/')), arcname=rel)
    with open(path, 'rb') as f:
        return f.read()


def packageFiles(release=RELEASE, only=None, manifest=True, header='#seraphine-manifest 1'):
    files = {r: d for r, d in release.items() if only is None or r in only}
    files['filelist.txt'] = filelistBytes(release)
    if manifest:
        files['filemanifest.txt'] = manifestBytes(release, header)
    return files


def digestOf(data):
    return 'sha256:' + hashlib.sha256(data).hexdigest()


def asset(name, url, data, digest=None):
    return {'name': name, 'browser_download_url': url, 'size': len(data),
            'digest': digestOf(data) if digest is None else digest}


class Server:
    """Fake aiohttp session: url -> bytes (served, Range honoured) or an HTTP status."""

    def __init__(self, routes):
        self.routes = routes
        self.requests = []
        self.onGet = None

    def get(self, url, **kwargs):
        rng = (kwargs.get('headers') or {}).get('Range')
        self.requests.append({'url': url, 'range': rng})
        if self.onGet:
            self.onGet(url)
        body = self.routes[url]
        if isinstance(body, BaseException):
            raise body
        if isinstance(body, int):
            return RangeResp(body)
        n = len(body)
        if rng:
            start = int(rng[len('bytes='):-1])
            return RangeResp(206, body[start:], {'content-range': f'bytes {start}-{n - 1}/{n}',
                                                  'content-length': n - start}, chunk=4096)
        return RangeResp(200, body, {'content-length': n}, chunk=4096)

    @property
    def urls(self):
        return [r['url'] for r in self.requests]


class DeltaMixin:
    """Temp stage dir, install and fallback file; the Windows platform."""

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix='seraphine_delta_')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.base = os.path.join(self.tmp, 'update')
        self.install = os.path.join(self.tmp, 'install')
        for rel, data in INSTALL.items():
            _write(os.path.join(self.install, *rel.split('/')), data)
        _write(os.path.join(self.install, 'filelist.txt'), filelistBytes(INSTALL))
        self.fallback = os.path.join(self.tmp, 'cfg', us.FALLBACK_NAME)
        for target, name, value in (
                (us, 'fallbackPath', lambda: self.fallback),
                (us, 'sys', SimpleNamespace(platform='win32', executable=sys.executable)),
                (platform, 'IS_WINDOWS', True),
                (platform, 'IS_MACOS', False),
                (platform, 'install_dir', lambda: None),
                (us, 'downloadProxy', lambda: None)):
            patcher = mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.fullBytes = sevenZip(self.tmp, packageFiles())
        self.deltaBytes = sevenZip(self.tmp, packageFiles(only=DELTA_FILES))
        self.manifestBytes = manifestBytes(RELEASE)

    def info(self, delta=True, deltaBytes=None, deltaDigest=None, deltaBase=None, **extra):
        deltaBytes = self.deltaBytes if deltaBytes is None else deltaBytes
        assets = [asset(FULL_WIN, FULL_URL, self.fullBytes),
                  asset(MANIFEST_ASSET, MANIFEST_URL, self.manifestBytes)]
        if delta:
            assets.append(asset(deltaName(deltaBase or VERSION, NEW), DELTA_URL, deltaBytes,
                                deltaDigest))
        info = {'tag_name': 'v' + NEW, 'forbidden': False, 'assets': assets}
        info.update(extra)
        return info

    def server(self, delta=None):
        return Server({FULL_URL: self.fullBytes,
                       DELTA_URL: self.deltaBytes if delta is None else delta,
                       MANIFEST_URL: self.manifestBytes})

    async def stage(self, info, sess, **kw):
        kw.setdefault('installDir', self.install)
        return await us.stageRelease(info, self.base, session=sess, **kw)

    def packageTree(self):
        root = os.path.join(self.base, 'package')
        out = {}
        for d, _, files in os.walk(root):
            for name in files:
                full = os.path.join(d, name)
                with open(full, 'rb') as f:
                    out[os.path.relpath(full, root).replace(os.sep, '/')] = f.read()
        return out

    def writeResult(self, status, version=NEW, code=9, time='2026-11-22T10:00:00'):
        _write(os.path.join(self.base, 'result.json'), json.dumps(
            {'status': status, 'code': code, 'version': version, 'time': time})
            .encode('utf-8-sig'))


class DeltaSelectionTest(DeltaMixin, unittest.IsolatedAsyncioTestCase):
    """Which asset is downloaded (the FakeSession URL list)."""

    async def test_on_the_base_version_only_the_delta_is_downloaded(self):
        sess = self.server()
        with self.assertLogs('Seraphine', level='INFO') as cm:
            marker = await self.stage(self.info(), sess)
        self.assertEqual(sess.urls, [DELTA_URL])
        self.assertEqual(marker['kind'], 'delta')
        self.assertEqual(marker['digest'], digestOf(self.deltaBytes))
        self.assertEqual(marker['version'], NEW)
        self.assertEqual(us.readMarker(self.base), marker)
        tree = self.packageTree()
        self.assertEqual(sorted(tree), sorted(list(DELTA_FILES) + ['filelist.txt', 'filemanifest.txt']))
        self.assertEqual(tree['_internal/b.dll'], b'new-b')
        self.assertFalse(os.path.exists(os.path.join(self.base, us.DELTA_NAME)))
        self.assertIn('delta staged', '\n'.join(cm.output))
        # exactly this release is staged now: no second download
        self.assertEqual(us.stagedFor(self.info(), self.base, self.install), marker)
        self.assertEqual(marker['base'], VERSION)
        self.assertEqual(marker['installDir'], os.path.normcase(os.path.abspath(self.install)))
        again = self.server()
        self.assertEqual(await self.stage(self.info(), again), marker)
        self.assertEqual(again.urls, [])

    def selectionCases(self):
        rollback = os.path.join(self.base, 'backup.meta')
        return [
            ('one version behind', dict(deltaBase=OLDER), None),
            ('no delta asset (kill switch)', dict(delta=False), None),
            ('delta without digest', dict(deltaDigest=''), None),
            ('delta with a non-sha256 digest', dict(deltaDigest='md5:abc'), None),
            ('pending rollback', {}, lambda: _write(rollback, b'{}')),
            ('version in nodelta', {}, lambda: us._addNoDelta(NEW)),
            ('noDelta switch', dict(noDelta=True), None),
        ]

    async def test_otherwise_only_the_full_package_is_downloaded(self):
        for name, kw, prepare in self.selectionCases():
            with self.subTest(case=name):
                shutil.rmtree(self.base, ignore_errors=True)
                _removeIfExists(self.fallback)
                if prepare:
                    prepare()
                sess = self.server()
                marker = await self.stage(self.info(**kw), sess)
                self.assertEqual(sess.urls, [FULL_URL])
                self.assertNotIn('kind', marker)
                self.assertEqual(marker['digest'], digestOf(self.fullBytes))
                self.assertEqual(sorted(self.packageTree()),
                                 sorted(list(RELEASE) + ['filelist.txt', 'filemanifest.txt']))

    async def test_no_install_dir_means_the_full_package(self):
        sess = self.server()
        marker = await us.stageRelease(self.info(), self.base, session=sess)  # install_dir() is None
        self.assertEqual(sess.urls, [FULL_URL])
        self.assertNotIn('kind', marker)

    def test_select_delta_asset_table(self):
        info = self.info()
        assets = info['assets']
        self.assertEqual(util.selectDeltaAsset(assets, VERSION, NEW)['browser_download_url'], DELTA_URL)
        self.assertEqual(util.selectDeltaAsset(assets, 'v' + VERSION, 'v' + NEW)['name'],
                         deltaName(VERSION, NEW))
        self.assertIsNone(util.selectDeltaAsset(assets, OLDER, NEW))
        self.assertIsNone(util.selectDeltaAsset(assets, VERSION, NEW + '1'))
        self.assertIsNone(util.selectDeltaAsset(self.info(deltaDigest='')['assets'], VERSION, NEW))
        self.assertIsNone(util.selectDeltaAsset([], VERSION, NEW))
        with mock.patch.object(platform, 'IS_WINDOWS', False), \
                mock.patch.object(platform, 'IS_MACOS', True):
            self.assertIsNone(util.selectDeltaAsset(assets, VERSION, NEW))

    def test_macos_never_tries_a_delta(self):
        with mock.patch.object(platform, 'IS_WINDOWS', False), \
                mock.patch.object(platform, 'IS_MACOS', True), \
                mock.patch.object(us, 'sys', SimpleNamespace(platform='darwin')):
            self.assertIsNone(us._deltaFor(self.info(), self.base, self.install))

    async def test_install_dir_parameter_is_what_the_precheck_reads(self):
        # platform.install_dir() points at an install the delta does not fit;
        # the given installDir is the one checked
        wrong = os.path.join(self.tmp, 'wrong-install')
        _write(os.path.join(wrong, '_internal', 'a.dll'), b'tampered')
        probe = mock.Mock(return_value=wrong)
        with mock.patch.object(platform, 'install_dir', probe):
            sess = self.server()
            marker = await self.stage(self.info(), sess)
        self.assertEqual(marker['kind'], 'delta')
        self.assertEqual(sess.urls, [DELTA_URL])
        probe.assert_not_called()
        # without the parameter the default install is used (and here it misfits)
        shutil.rmtree(self.base)
        with mock.patch.object(platform, 'install_dir', probe):
            sess = self.server()
            marker = await us.stageRelease(self.info(), self.base, session=sess)
        probe.assert_called()
        self.assertEqual(sess.urls, [DELTA_URL, FULL_URL])
        self.assertNotIn('kind', marker)


def _removeIfExists(path):
    try:
        os.remove(path)
    except OSError:
        pass


class DeltaFallbackTest(DeltaMixin, unittest.IsolatedAsyncioTestCase):
    """Any delta failure stages the full package in the same call."""

    def failureCases(self):
        garbage = b'not a 7z archive at all' * 8
        noManifest = sevenZip(self.tmp, packageFiles(only=DELTA_FILES, manifest=False))
        newHeader = sevenZip(self.tmp, packageFiles(only=DELTA_FILES,
                                                    header='#seraphine-manifest 2'))
        noExe = sevenZip(self.tmp, {k: v for k, v in packageFiles(only=DELTA_FILES).items()
                                    if k != 'Seraphine.exe'})
        trailingDot = dict(packageFiles(only=DELTA_FILES))
        trailingDot['filemanifest.txt'] = trailingDot['filemanifest.txt'].replace(
            b'_internal\\a.dll', b'_internal\\a.dll.')
        trailingDot = sevenZip(self.tmp, trailingDot)
        changed = os.path.join(self.install, '_internal', 'a.dll')
        return [
            # name, served delta, digest override, install change, expected reason,
            # version goes into nodelta (deterministic failure)
            ('HTTP 500', 500, None, None, 'HTTP 500', False),
            ('connection error', aiohttp.ClientConnectionError('cut'), None, None,
             'delta not used (ClientConnectionError)', False),
            ('digest mismatch', self.deltaBytes, digestOf(b'other'), None, 'verification failed',
             True),
            ('corrupt archive', garbage, digestOf(garbage), None, 'delta not used (', True),
            ('no Seraphine.exe', noExe, digestOf(noExe), None, 'package has no Seraphine.exe',
             True),
            ('missing manifest', noManifest, digestOf(noManifest), None,
             'no usable filemanifest.txt', True),
            ('unknown manifest header', newHeader, digestOf(newHeader), None,
             'no usable filemanifest.txt', True),
            ('manifest path ending in a dot', trailingDot, digestOf(trailingDot), None,
             'invalid path _internal\\a.dll.', True),
            ('precheck misfit (one changed install file)', self.deltaBytes, None,
             lambda: _write(changed, b'changed-a'), 'the install does not match the delta', True),
            ('precheck misfit (one missing install file)', self.deltaBytes, None,
             lambda: os.remove(os.path.join(self.install, '_internal', 'sub', 'c.pyd')),
             'the install does not match the delta', True),
        ]

    async def test_every_delta_failure_falls_back_to_the_full_package(self):
        for name, served, digest, change, reason, excluded in self.failureCases():
            with self.subTest(case=name):
                shutil.rmtree(self.base, ignore_errors=True)
                _removeIfExists(self.fallback)
                shutil.rmtree(self.install, ignore_errors=True)
                for rel, data in INSTALL.items():
                    _write(os.path.join(self.install, *rel.split('/')), data)
                if change:
                    change()
                deltaBytes = served if isinstance(served, bytes) else self.deltaBytes
                sess = self.server(delta=served)
                with self.assertLogs('Seraphine', level='INFO') as cm:
                    marker = await self.stage(self.info(deltaBytes=deltaBytes, deltaDigest=digest),
                                              sess)
                self.assertEqual(sess.urls, [DELTA_URL, FULL_URL])
                self.assertNotIn('kind', marker)
                self.assertEqual(marker['digest'], digestOf(self.fullBytes))
                self.assertEqual(sorted(self.packageTree()),
                                 sorted(list(RELEASE) + ['filelist.txt', 'filemanifest.txt']))
                logs = '\n'.join(cm.output)
                self.assertIn('delta not used (', logs)
                self.assertIn('staging the full package', logs)
                self.assertIn(reason, logs)
                self.assertNotIn('example.invalid', logs)
                for name_ in (us.DELTA_NAME, us.DELTA_NAME + '.part', us.DELTA_NAME + '.part.json'):
                    self.assertFalse(os.path.exists(os.path.join(self.base, name_)), name_)
                self.assertEqual(us.deltaExcluded(NEW), excluded)

    async def test_a_cancelled_delta_does_not_fall_back(self):
        sess = self.server()
        task = asyncio.ensure_future(self.stage(self.info(), _StallingServer(sess)))
        for _ in range(50):
            await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(sess.urls, [DELTA_URL])
        self.assertIsNone(us.readMarker(self.base))


    async def pauseDuring(self, info, server, url):
        """Stage until ``url``'s body has started, then cancel (a game started)."""
        stalling = _StallingServer(server, only=url)
        task = asyncio.ensure_future(self.stage(info, stalling, resumable=True))
        for _ in range(500):
            if stalling.stalled:
                break
            await asyncio.sleep(0.01)
        self.assertTrue(stalling.stalled, 'the download did not reach ' + url)
        await asyncio.sleep(0.1)  # its first byte is written, then it stalls
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_misfit_delta_then_paused_full_resumes_without_the_delta(self):
        _write(os.path.join(self.install, '_internal', 'a.dll'), b'changed-a')  # misfit
        first = self.server()
        await self.pauseDuring(self.info(), first, FULL_URL)
        self.assertEqual(first.urls, [DELTA_URL, FULL_URL])
        self.assertTrue(us.deltaExcluded(NEW))
        part = os.path.join(self.base, us.ARCHIVE_NAME + '.part')
        have = os.path.getsize(part)
        self.assertGreater(have, 0)
        self.assertTrue(os.path.isfile(part + '.json'))
        # the next attempt continues the full download and asks for no delta
        second = self.server()
        marker = await self.stage(self.info(), second, resumable=True)
        self.assertEqual(second.requests, [{'url': FULL_URL, 'range': f'bytes={have}-'}])
        self.assertNotIn('kind', marker)
        self.assertEqual(marker['digest'], digestOf(self.fullBytes))

    async def test_both_paused_downloads_are_kept(self):
        # a full download paused earlier (noDelta then), then the delta paused too
        info = self.info()
        fullAsset, deltaAsset = info['assets'][0], info['assets'][2]
        full = os.path.join(self.base, us.ARCHIVE_NAME + '.part')
        _write(full, self.fullBytes[:100])
        _write(full + '.json', json.dumps(us.resumeKey(fullAsset)).encode('utf-8'))
        await self.pauseDuring(info, self.server(), DELTA_URL)
        delta = os.path.join(self.base, us.DELTA_NAME + '.part')
        for path in (full, full + '.json', delta, delta + '.json'):
            self.assertTrue(os.path.isfile(path), path)
        self.assertEqual(us._readJson(delta + '.json'), us.resumeKey(deltaAsset))
        self.assertEqual(os.path.getsize(full), 100)
        # the delta then misfits: the full download continues from byte 100
        _write(os.path.join(self.install, '_internal', 'a.dll'), b'changed-a')
        sess = self.server()
        marker = await self.stage(info, sess, resumable=True)
        self.assertEqual([r['url'] for r in sess.requests], [DELTA_URL, FULL_URL])
        self.assertEqual(sess.requests[1]['range'], 'bytes=100-')
        self.assertNotIn('kind', marker)


class _StallingServer:
    """Forwards to a Server, but bodies (of ``only``, else all) stall after the
    first chunk until cancelled."""

    def __init__(self, server, only=None):
        self.server, self.only = server, only
        self.stalled = False

    def get(self, url, **kwargs):
        resp = self.server.get(url, **kwargs)
        if self.only is None or url == self.only:
            resp.stall, resp.chunk = 1, 1
            self.stalled = True
        return resp


class DeltaPrecheckTest(DeltaMixin, unittest.TestCase):
    """deltaFits, the mirror of the updater's step 4b, on extracted trees."""

    def package(self, files):
        root = tempfile.mkdtemp(prefix='pkg_', dir=self.tmp)
        for rel, data in files.items():
            _write(os.path.join(root, *rel.split('/')), data)
        return root

    def test_a_delta_that_fits(self):
        self.assertEqual(us.deltaFits(self.package(packageFiles(only=DELTA_FILES)), self.install),
                         len(RELEASE))

    def test_a_full_package_fits_too(self):
        self.assertEqual(us.deltaFits(self.package(packageFiles()), self.install), len(RELEASE))

    def test_misfits(self):
        good = packageFiles(only=DELTA_FILES)
        header = '#seraphine-manifest 1\r\n'
        line = '{}\t{}\t{}\r\n'

        def manifestWith(*extra):
            return manifestBytes(RELEASE) + ''.join(extra).encode('utf-8')

        a = RELEASE['_internal/a.dll']
        cases = [
            ('manifest line not three fields', dict(good, **{'filemanifest.txt': (
                header + 'abc\r\n').encode('utf-8-sig')}), 'malformed (line 2)'),
            ('bad size', dict(good, **{'filemanifest.txt': (header + line.format(
                hashlib.sha256(a).hexdigest(), '-1', '_internal\\a.dll')).encode('utf-8-sig')}),
             'malformed (line 2)'),
            ('entry listed twice', dict(good, **{'filemanifest.txt': manifestWith(line.format(
                hashlib.sha256(a).hexdigest(), len(a), '_internal\\A.dll'))}), 'listed twice'),
            ('entry not in the new filelist', dict(good, **{'filemanifest.txt': manifestWith(
                line.format('0' * 64, 1, '_internal\\extra.dll'))}), 'not in the new filelist'),
            ('parent reference', dict(good, **{'filemanifest.txt': manifestWith(
                line.format('0' * 64, 1, '..\\evil.dll'))}), 'invalid path'),
            ('segment ending in a dot', dict(good, **{'filemanifest.txt': manifestWith(
                line.format('0' * 64, 1, '_internal.\\x.dll'))}), 'invalid path'),
            ('segment ending in a space', dict(good, **{'filemanifest.txt': manifestWith(
                line.format('0' * 64, 1, '_internal \\x.dll'))}), 'invalid path'),
            ('file name ending in a dot', dict(good, **{'filemanifest.txt': manifestWith(
                line.format('0' * 64, 1, '_internal\\x.dll.'))}), 'invalid path'),
            ('filelist entry ending in a dot', dict(good, **{
                'filelist.txt': filelistBytes(list(RELEASE) + ['_internal/x.dll.'])}),
             'Windows would trim (_internal\\x.dll.)'),
            ('filelist segment ending in a space', dict(good, **{
                'filelist.txt': filelistBytes(list(RELEASE) + ['_internal /x.dll'])}),
             'Windows would trim (_internal \\x.dll)'),
            ('shipped file differs from its entry', dict(good, **{'_internal/b.dll': b'other-b'}),
             "the delta's _internal\\b.dll differs"),
            ('shipped Seraphine.exe differs (never pruned)', dict(good, **{'Seraphine.exe': b'old-exe'}),
             "the delta's Seraphine.exe differs"),
            ('filelist entry without manifest entry or file', dict(good, **{
                'filelist.txt': filelistBytes(list(RELEASE) + ['_internal/new.dll'])}),
             'lacks _internal\\new.dll'),
        ]
        for name, files, reason in cases:
            with self.subTest(case=name):
                with self.assertRaises(us.StageError) as ctx:
                    us.deltaFits(self.package(files), self.install)
                self.assertIn(reason, str(ctx.exception))

    def test_filelist_entries_the_updater_skips_are_skipped(self):
        # absolute paths and parent references: step 4b skips them as well
        files = dict(packageFiles(only=DELTA_FILES), **{'filelist.txt': filelistBytes(
            list(RELEASE) + ['../evil.dll', 'C:/abs.dll', '/rooted.dll', '_internal/../x.dll'])})
        self.assertEqual(us.deltaFits(self.package(files), self.install), len(RELEASE))

    def test_unchanged_file_shipped_but_already_installed_fits(self):
        # 4b prunes a shipped file the install already has, whatever the package holds
        files = dict(packageFiles(only=DELTA_FILES), **{'_internal/a.dll': b'garbage'})
        self.assertEqual(us.deltaFits(self.package(files), self.install), len(RELEASE))

    def test_manifest_reader(self):
        path = os.path.join(self.tmp, 'm.txt')
        _write(path, manifestBytes(RELEASE))
        entries = us.readManifest(path)
        self.assertEqual([e[2] for e in entries], [_win(r) for r in RELEASE])
        self.assertEqual(entries[0][1], len(RELEASE['Seraphine.exe']))
        _write(path, manifestBytes(RELEASE, header='#seraphine-manifest 2'))
        self.assertIsNone(us.readManifest(path))
        self.assertIsNone(us.readManifest(os.path.join(self.tmp, 'missing.txt')))


class FailedResultTest(DeltaMixin, unittest.IsolatedAsyncioTestCase):
    """failed:9 / failed delta results, update-fallback.json and the loop bound."""

    async def stageDelta(self):
        marker = await self.stage(self.info(), self.server())
        self.assertEqual(marker['kind'], 'delta')
        return marker

    async def test_failed9_of_a_delta_means_full_package_after_a_restart(self):
        await self.stageDelta()
        self.writeResult('failed:9')
        self.assertIsNone(us.stagedUpdate(VERSION, self.base, installDir=self.install))
        self.assertTrue(us.deltaExcluded(NEW))
        self.assertEqual(us.failed9Count(NEW), 1)
        self.assertIsNone(us.readMarker(self.base))
        # simulated restart: new process state, the stage dir removed
        shutil.rmtree(self.base, ignore_errors=True)
        sess = self.server()
        marker = await self.stage(self.info(), sess)
        self.assertEqual(sess.urls, [FULL_URL])
        self.assertNotIn('kind', marker)
        with open(self.fallback, encoding='utf-8') as f:
            self.assertEqual(json.load(f), {
                'nodelta': [NEW],
                'failed9': {NEW: {'count': 1, 'lastTime': '2026-11-22T10:00:00'}}})

    async def test_failed9_of_a_full_package_downloads_fresh(self):
        sess = self.server()
        await self.stage(self.info(delta=False), sess)
        self.writeResult('failed:9')
        self.assertIsNone(us.stagedUpdate(VERSION, self.base, installDir=self.install))
        self.assertFalse(us.deltaExcluded(NEW))
        self.assertEqual(us.failed9Count(NEW), 1)
        sess = self.server()
        marker = await self.stage(self.info(delta=False), sess)
        self.assertEqual(sess.urls, [FULL_URL])  # not "already staged"
        self.assertEqual(us.readMarker(self.base), marker)

    async def test_failed9_seen_first_by_staging_is_counted_there_once(self):
        await self.stage(self.info(delta=False), self.server())
        self.writeResult('failed:9')
        sess = self.server()
        await self.stage(self.info(delta=False), sess)
        self.assertEqual(sess.urls, [FULL_URL])
        self.assertEqual(us.failed9Count(NEW), 1)

    def writeOwnRollback(self):
        """A backup the next updater run would apply (its fingerprint matches the
        installed Seraphine.exe), as restore-failed leaves it."""
        with open(os.path.join(self.install, 'Seraphine.exe'), 'rb') as f:
            installed = hashlib.sha256(f.read()).hexdigest().upper()
        _write(os.path.join(self.base, 'backup', 'Seraphine.exe'), b'old-exe')
        _write(os.path.join(self.base, 'backup.meta'), json.dumps(
            {'preExe': installed, 'pkgExe': 'AB' * 32, 'version': NEW}).encode('utf-8-sig'))

    async def test_failed_merge_of_a_delta_excludes_the_delta(self):
        # failed:5 (a merge step failed, nothing left half-done): stage goes, full next
        await self.stageDelta()
        self.writeResult('failed:5', code=5)
        self.assertIsNone(us.stagedUpdate(VERSION, self.base, installDir=self.install))
        self.assertTrue(us.deltaExcluded(NEW))
        self.assertEqual(us.failed9Count(NEW), 0)
        self.assertIsNone(us.readMarker(self.base))
        sess = self.server()
        await self.stage(self.info(), sess)
        self.assertEqual(sess.urls, [FULL_URL])

    async def test_restored_or_restore_failed_delta_keeps_todays_bars(self):
        # (status, code, rollback left, the manual-download bar instead of Retry)
        for status, code, rollback, manual in (('restored', 4, False, False),
                                               ('restore-failed', 4, True, False),
                                               ('restore-failed', 8, True, True)):
            with self.subTest(status=status, code=code):
                shutil.rmtree(self.base, ignore_errors=True)
                _removeIfExists(self.fallback)
                marker = await self.stageDelta()
                if rollback:
                    self.writeOwnRollback()
                self.writeResult(status, code=code)
                offered = us.stagedUpdate(VERSION, self.base, installDir=self.install)
                self.assertIsNotNone(offered)
                self.assertEqual(offered['lastResult']['status'], status)
                self.assertEqual(offered['lastResult']['code'], code)
                self.assertEqual({k: v for k, v in offered.items() if k != 'lastResult'}, marker)
                self.assertEqual(us.readMarker(self.base), marker)  # the stage is kept
                self.assertTrue(us.deltaExcluded(NEW))
                self.assertEqual(us.failed9Count(NEW), 0)
                self.assertEqual(us.repairBlocks(offered['lastResult'], NEW), manual)
                # Retry applies the kept stage; the next update check stages the full one
                with mock.patch.object(platform, 'install_dir', lambda: self.install):
                    self.assertTrue(us.markerApplicable(offered, self.base))
                self.assertIsNone(us.stagedFor(self.info(), self.base, self.install))
                if not manual:
                    sess = self.server()
                    await self.stage(self.info(), sess)
                    self.assertEqual(sess.urls, [FULL_URL])

    async def test_restored_full_stage_keeps_todays_retry(self):
        marker = await self.stage(self.info(delta=False), self.server())
        self.writeResult('restored', code=4)
        offered = us.stagedUpdate(VERSION, self.base, installDir=self.install)
        self.assertEqual(offered['lastResult']['status'], 'restored')
        self.assertEqual({k: v for k, v in offered.items() if k != 'lastResult'}, marker)
        self.assertFalse(us.deltaExcluded(NEW))

    async def test_pending_result_is_counted_before_the_block_check(self):
        us._recordFailed9({'status': 'failed:9', 'version': NEW, 'time': 't1'})
        await self.stage(self.info(delta=False), self.server())
        self.writeResult('failed:9', time='t2')  # not seen by a start-up yet
        sess = self.server()
        with self.assertRaises(us.StageError) as ctx:
            await self.stage(self.info(), sess)
        self.assertIn('update blocked after repeated failures', str(ctx.exception))
        self.assertEqual(sess.urls, [])
        self.assertEqual(us.failed9Count(NEW), 2)

    async def test_fallback_write_retries_the_replace(self):
        realReplace = os.replace
        calls = []

        def flaky(src, dst):
            calls.append(dst)
            if len(calls) <= 2:
                raise PermissionError('held')
            return realReplace(src, dst)

        with mock.patch.object(us.os, 'replace', flaky):
            self.assertTrue(us._writeFallback({'nodelta': [NEW], 'failed9': {}}))
        self.assertEqual(len(calls), 3)
        self.assertTrue(us.deltaExcluded(NEW))

        def broken(src, dst):
            raise PermissionError('held')

        with mock.patch.object(us.os, 'replace', broken), \
                mock.patch.object(us, '_REPLACE_DELAY', 0), \
                self.assertLogs('Seraphine', level='INFO') as cm:
            self.assertFalse(us._writeFallback({'nodelta': [], 'failed9': {}}))
        self.assertIn('could not write update-fallback.json (PermissionError)', '\n'.join(cm.output))
        self.assertFalse(os.path.exists(self.fallback + '.tmp'))
        self.assertTrue(us.deltaExcluded(NEW))  # the old file is unchanged

    async def test_unrecorded_failed9_is_counted_at_the_next_startup(self):
        await self.stage(self.info(delta=False), self.server())
        self.writeResult('failed:9')
        realReplace = os.replace

        def failForFallback(src, dst):
            if os.path.basename(dst) == us.FALLBACK_NAME:
                raise PermissionError('held')
            return realReplace(src, dst)

        with mock.patch.object(us.os, 'replace', failForFallback), \
                mock.patch.object(us, '_REPLACE_DELAY', 0):
            self.assertIsNone(us.stagedUpdate(VERSION, self.base, installDir=self.install))
        self.assertEqual(us.failed9Count(NEW), 0)
        self.assertIsNone(us.readMarker(self.base))  # the stage still goes
        self.assertTrue(os.path.isfile(os.path.join(self.base, 'result.json')))  # result stays
        self.assertIsNone(us.stagedUpdate(VERSION, self.base, installDir=self.install))
        self.assertEqual(us.failed9Count(NEW), 1)
        self.assertFalse(os.path.isfile(os.path.join(self.base, 'result.json')))

    async def test_two_distinct_failed9_runs_block_the_version(self):
        for time in ('2026-11-22T10:00:00', '2026-11-22T11:00:00'):
            self.assertIsNone(us.failedRepair(NEW, self.base))
            await self.stage(self.info(delta=False), self.server())
            self.writeResult('failed:9', time=time)
            self.assertIsNone(us.stagedUpdate(VERSION, self.base, installDir=self.install))
        self.assertEqual(us.failed9Count(NEW), 2)
        self.assertFalse(us._hasRollback(self.base))
        blocked = us.failedRepair(NEW, self.base)
        self.assertEqual(blocked, {'lastResult': {'status': 'failed:9', 'code': 9, 'version': NEW}})
        self.assertTrue(us.repairBlocks(blocked['lastResult'], NEW))
        # what MainWindow passes on: the bar's marker has no version
        self.assertTrue(us.repairBlocks(blocked['lastResult'], blocked.get('version', '')))
        self.assertTrue(us.updateBlocked(NEW))
        # a newer release is not blocked by it
        newer = f'{_MAJOR}.{_MINOR}.{_PATCH + 2}'
        self.assertIsNone(us.failedRepair(newer, self.base))
        self.assertFalse(us.repairBlocks(blocked['lastResult'], newer))
        # staging the blocked version raises before any request (dialog path too)
        sess = self.server()
        with self.assertRaises(us.StageError) as ctx:
            await self.stage(self.info(), sess)
        self.assertIn('update blocked after repeated failures', str(ctx.exception))
        self.assertEqual(sess.urls, [])
        self.assertEqual(us.failureReason(ctx.exception), 'other')

    async def test_one_failed9_does_not_block(self):
        await self.stage(self.info(delta=False), self.server())
        self.writeResult('failed:9')
        us.stagedUpdate(VERSION, self.base, installDir=self.install)
        self.assertIsNone(us.failedRepair(NEW, self.base))
        self.assertFalse(us.repairBlocks({'status': 'failed:9', 'code': 9, 'version': NEW}, NEW))

    async def test_same_result_read_at_two_startups_counts_once(self):
        await self.stage(self.info(delta=False), self.server())
        self.writeResult('failed:9')
        with open(os.path.join(self.base, 'result.json'), 'rb') as f:
            same = f.read()
        for _ in range(2):  # two start-ups; the same result.json is there again
            _write(os.path.join(self.base, 'result.json'), same)
            self.assertIsNone(us.stagedUpdate(VERSION, self.base, installDir=self.install))
            self.assertIsNone(us.failedRepair(NEW, self.base))
        self.assertEqual(us.failed9Count(NEW), 1)

    async def test_same_result_kept_by_a_pending_restore_counts_once(self):
        # a pending restore keeps result.json (rollback rules unchanged)
        marker = await self.stage(self.info(delta=False), self.server())
        exe = os.path.join(self.install, 'Seraphine.exe')
        with open(exe, 'rb') as f:
            installed = hashlib.sha256(f.read()).hexdigest().upper()
        _write(os.path.join(self.base, 'backup', 'Seraphine.exe'), b'old-exe')
        _write(os.path.join(self.base, 'backup.meta'), json.dumps(
            {'preExe': installed, 'pkgExe': 'AB' * 32, 'version': NEW}).encode('utf-8-sig'))
        self.writeResult('failed:9')
        self.assertIsNone(us.stagedUpdate(VERSION, self.base, installDir=self.install))
        self.assertTrue(os.path.isfile(os.path.join(self.base, 'result.json')))
        self.assertTrue(us._hasRollback(self.base))
        self.assertIsNone(us.readMarker(self.base))
        # a fresh stage of the same version, then the next start-up reads the same result
        marker = await self.stage(self.info(delta=False), self.server())
        self.assertEqual(us.stagedUpdate(VERSION, self.base, installDir=self.install), marker)
        self.assertEqual(us.failed9Count(NEW), 1)
        self.assertIsNone(us.failedRepair(NEW, self.base))

    async def test_corrupt_fallback_file_reads_as_empty(self):
        _write(self.fallback, bytes(range(256)) * 4)
        with self.assertLogs('Seraphine', level='INFO') as cm:
            self.assertEqual(us.readFallback(), {'nodelta': [], 'failed9': {}})
        self.assertIn('update-fallback.json unreadable (UnicodeDecodeError)', '\n'.join(cm.output))
        self.assertIsNone(us.failedRepair(NEW, self.base))
        self.assertFalse(us.repairBlocks({'status': 'failed:9', 'code': 9, 'version': NEW}, NEW))
        sess = self.server()
        marker = await self.stage(self.info(), sess)  # not blocked, delta allowed
        self.assertEqual(sess.urls, [DELTA_URL])
        self.assertEqual(marker['kind'], 'delta')
        for junk in (b'[1, 2]', b'{"nodelta": 5, "failed9": {"x": {"count": "2"}}}', b'null'):
            _write(self.fallback, junk)
            self.assertEqual(us.readFallback(), {'nodelta': [], 'failed9': {}})

    async def test_entries_not_newer_than_the_running_version_are_dropped_at_startup(self):
        newer = f'{_MAJOR}.{_MINOR}.{_PATCH + 2}'
        _write(self.fallback, json.dumps({
            'nodelta': [OLDER, VERSION, NEW],
            'failed9': {OLDER: {'count': 2, 'lastTime': 'a'}, VERSION: {'count': 1, 'lastTime': 'b'},
                        newer: {'count': 2, 'lastTime': 'c'}}}).encode('utf-8'))
        self.assertIsNone(us.stagedUpdate(VERSION, self.base))  # no stage dir: still pruned
        with open(self.fallback, encoding='utf-8') as f:
            self.assertEqual(json.load(f), {'nodelta': [NEW],
                                            'failed9': {newer: {'count': 2, 'lastTime': 'c'}}})
        self.assertFalse(os.path.exists(self.fallback + '.tmp'))

    async def test_fallback_file_lives_outside_the_stage_dir(self):
        # the unpatched location: <config dir>/update-fallback.json, beside update/
        real = os.path.join(platform.config_dir(), us.FALLBACK_NAME)
        self.assertEqual(os.path.dirname(real), os.path.dirname(us.stageBase()))
        self.assertNotEqual(os.path.basename(us.stageBase()), us.FALLBACK_NAME)
        await self.stageDelta()
        self.writeResult('failed:9')
        us.stagedUpdate(VERSION, self.base, installDir=self.install)
        us.clearStage(self.base)
        self.assertTrue(us.deltaExcluded(NEW))


class StaleDeltaMarkerTest(DeltaMixin, unittest.IsolatedAsyncioTestCase):
    """A delta staged by another version or for another install is never offered,
    applied or reused (a config dir shared by two installs, a reinstall)."""

    async def asyncSetUp(self):
        self.marker = await self.stage(self.info(), self.server())
        self.assertEqual(self.marker['kind'], 'delta')
        self.other = os.path.join(self.tmp, 'other-install')
        os.makedirs(self.other)

    def assertDroppedWithRunCounted(self, count):
        # the stale stage and its result go; its failed:9 run is still one run of
        # that version (per-version count), nothing else is recorded
        self.assertIsNone(us.readMarker(self.base))
        self.assertFalse(os.path.exists(os.path.join(self.base, 'result.json')))
        self.assertEqual(us.failed9Count(NEW), count)
        self.assertFalse(us.deltaExcluded(NEW))

    def test_start_up_on_another_version_drops_it(self):
        self.assertIsNone(us.stagedUpdate(OLDER, self.base, installDir=self.install))
        self.assertDroppedWithRunCounted(0)

    def test_start_up_on_another_version_counts_its_failed9_run(self):
        self.writeResult('failed:9')
        self.assertIsNone(us.stagedUpdate(OLDER, self.base, installDir=self.install))
        self.assertDroppedWithRunCounted(1)

    async def test_two_installs_sharing_the_config_dir(self):
        # install A staged the delta and its updater run failed with 9; install B
        # (same config dir) starts first: the stage is not B's, the run still counts
        self.writeResult('failed:9', time='t1')
        self.assertIsNone(us.stagedUpdate(VERSION, self.base, installDir=self.other))
        self.assertDroppedWithRunCounted(1)
        # A starts next: nothing left to count twice
        self.assertIsNone(us.stagedUpdate(VERSION, self.base, installDir=self.install))
        self.assertEqual(us.failed9Count(NEW), 1)
        # A stages again (the delta still fits A), runs, fails with 9 again; B starts
        marker = await self.stage(self.info(), self.server())
        self.assertEqual(marker['kind'], 'delta')
        self.writeResult('failed:9', time='t2')
        self.assertIsNone(us.stagedUpdate(VERSION, self.base, installDir=self.other))
        self.assertDroppedWithRunCounted(2)
        self.assertTrue(us.updateBlocked(NEW))
        self.assertEqual(us.failedRepair(NEW, self.base),
                         {'lastResult': {'status': 'failed:9', 'code': 9, 'version': NEW}})

    def test_unrecordable_failed9_of_a_stale_delta_keeps_its_result(self):
        self.writeResult('failed:9')
        realReplace = os.replace

        def failForFallback(src, dst):
            if os.path.basename(dst) == us.FALLBACK_NAME:
                raise PermissionError('held')
            return realReplace(src, dst)

        with mock.patch.object(us.os, 'replace', failForFallback), \
                mock.patch.object(us, '_REPLACE_DELAY', 0):
            self.assertIsNone(us.stagedUpdate(VERSION, self.base, installDir=self.other))
        self.assertIsNone(us.readMarker(self.base))
        self.assertTrue(os.path.isfile(os.path.join(self.base, 'result.json')))
        self.assertIsNone(us.stagedUpdate(VERSION, self.base, installDir=self.other))
        self.assertDroppedWithRunCounted(1)

    def test_start_up_on_its_own_version_and_install_offers_it(self):
        self.assertEqual(us.stagedUpdate(VERSION, self.base, installDir=self.install), self.marker)

    def test_marker_applicable_only_on_its_base_and_install(self):
        with mock.patch.object(platform, 'install_dir', lambda: self.install):
            self.assertTrue(us.markerApplicable(self.marker, self.base))
            self.assertFalse(us.markerApplicable(self.marker, self.base, currentVersion=OLDER))
        self.assertFalse(us.markerApplicable(self.marker, self.base, installDir=self.other))
        self.assertFalse(us.markerApplicable(self.marker, self.base))  # install unknown

    async def test_staged_for_and_staging_on_another_install_download_again(self):
        self.assertEqual(us.stagedFor(self.info(), self.base, self.install), self.marker)
        self.assertIsNone(us.stagedFor(self.info(), self.base, self.other))
        with mock.patch.object(us, 'VERSION', OLDER):
            self.assertIsNone(us.stagedFor(self.info(), self.base, self.install))
        _write(os.path.join(self.other, '_internal', 'a.dll'), b'other')  # misfit there
        sess = self.server()
        marker = await self.stage(self.info(), sess, installDir=self.other)
        self.assertEqual(sess.urls, [DELTA_URL, FULL_URL])
        self.assertNotIn('kind', marker)


class FieldSwitchTest(DeltaMixin, unittest.IsolatedAsyncioTestCase):
    """ver.json noDelta / noResume, copied by Github.checkUpdate like forbidden."""

    def checkUpdate(self, entry):
        info = self.info()
        verJson = json.dumps({VERSION: entry}).encode('utf-8')

        class Resp:
            def __init__(self, data):
                self.data = data

            def json(self):
                return self.data

        def get(url, **kwargs):
            if url.endswith('/releases/latest'):
                return Resp(json.loads(json.dumps(info)))
            if url.endswith('/contents/document/ver.json'):
                return Resp({'content': base64.b64encode(verJson).decode('ascii')})
            raise AssertionError(url)

        gh = util.Github()
        gh.sess = SimpleNamespace(get=get)
        return gh.checkUpdate()

    def test_check_update_copies_the_switches(self):
        info = self.checkUpdate({'noDelta': True, 'noResume': True})
        self.assertIs(info['noDelta'], True)
        self.assertIs(info['noResume'], True)
        info = self.checkUpdate({})
        self.assertIs(info['noDelta'], False)
        self.assertIs(info['noResume'], False)
        self.assertIs(info['forbidden'], False)
        # only JSON true switches: other values change nothing
        for value in ('true', 1, 'yes', [True], {'on': True}):
            with self.subTest(value=value):
                info = self.checkUpdate({'noDelta': value, 'noResume': value})
                self.assertIs(info['noDelta'], False)
                self.assertIs(info['noResume'], False)

    async def test_no_delta_requests_only_the_full_package(self):
        info = self.checkUpdate({'noDelta': True})
        sess = self.server()
        with self.assertLogs('Seraphine', level='INFO') as cm:
            marker = await self.stage(info, sess)
        self.assertEqual(sess.urls, [FULL_URL])
        self.assertNotIn('kind', marker)
        self.assertIn('delta not used (switched off for this version)', '\n'.join(cm.output))

    async def test_no_delta_refuses_an_already_staged_delta(self):
        delta = await self.stage(self.checkUpdate({}), self.server())
        self.assertEqual(delta['kind'], 'delta')
        info = self.checkUpdate({'noDelta': True})
        self.assertIsNone(us.stagedFor(info, self.base))
        sess = self.server()
        await self.stage(info, sess)
        self.assertEqual(sess.urls, [FULL_URL])

    def writePart(self, name, data, assetDict):
        path = os.path.join(self.base, name + '.part')
        _write(path, data)
        with open(path + '.json', 'w', encoding='utf-8') as f:
            json.dump(us.resumeKey(assetDict), f)
        return path

    def watch(self, sess, part):
        seen = []
        sess.onGet = lambda url: seen.append(os.path.exists(part) or os.path.exists(part + '.json'))
        return seen

    async def test_no_resume_discards_the_partial_and_sends_no_range(self):
        for withDelta, name, url, data in ((False, us.ARCHIVE_NAME, FULL_URL, self.fullBytes),
                                           (True, us.DELTA_NAME, DELTA_URL, self.deltaBytes)):
            with self.subTest(delta=withDelta):
                shutil.rmtree(self.base, ignore_errors=True)
                info = self.checkUpdate({'noResume': True})
                if not withDelta:
                    info['assets'] = info['assets'][:2]
                assetDict = next(a for a in info['assets'] if a['browser_download_url'] == url)
                part = self.writePart(name, data[:len(data) // 2], assetDict)
                sess = self.server()
                seen = self.watch(sess, part)
                marker = await self.stage(info, sess, resumable=True)
                self.assertEqual(sess.urls, [url])
                self.assertEqual([r['range'] for r in sess.requests], [None])
                self.assertEqual(seen, [False])  # removed before the download
                self.assertEqual(marker.get('kind', 'full'), 'delta' if withDelta else 'full')

    async def test_switches_missing_means_delta_and_resume(self):
        info = self.checkUpdate({})
        assetDict = next(a for a in info['assets'] if a['browser_download_url'] == DELTA_URL)
        half = len(self.deltaBytes) // 2
        part = self.writePart(us.DELTA_NAME, self.deltaBytes[:half], assetDict)
        sess = self.server()
        seen = self.watch(sess, part)
        marker = await self.stage(info, sess, resumable=True)
        self.assertEqual(sess.urls, [DELTA_URL])
        self.assertEqual([r['range'] for r in sess.requests], [f'bytes={half}-'])
        self.assertEqual(seen, [True])
        self.assertEqual(marker['kind'], 'delta')
        self.assertFalse(os.path.exists(part + '.json'))


class BackgroundKindTest(DeltaMixin, unittest.IsolatedAsyncioTestCase):
    """BackgroundUpdater reports kind 'delta' while a delta is staged (C7)."""

    async def runUpdater(self, info, sess):
        events, ready = [], []
        install = self.install

        async def stage(info, base, onProgress=None, resumable=False):
            return await us.stageRelease(info, base, onProgress, sess, resumable=resumable,
                                         installDir=install)

        bu = us.BackgroundUpdater(ready.append, base=self.base, stage=stage,
                                  onProgress=lambda *a: events.append(a), resumable=True)
        bu.setGameStatus(None)
        bu.offer(info)
        for _ in range(500):
            if bu.task is None:
                break
            await asyncio.sleep(0.01)
        self.assertIsNone(bu.task)
        return events, ready

    async def test_delta_kind_through_to_ready(self):
        events, ready = await self.runUpdater(self.info(), self.server())
        self.assertEqual(len(ready), 1)
        self.assertEqual(ready[0]['kind'], 'delta')
        self.assertEqual({e[4] for e in events}, {'delta'})
        self.assertEqual(events[-1], ('ready', NEW, 0, 0, 'delta'))

    async def test_fallback_switches_the_kind_to_full(self):
        events, ready = await self.runUpdater(self.info(), self.server(delta=500))
        self.assertEqual(len(ready), 1)
        self.assertNotIn('kind', ready[0])
        downloads = [e for e in events if e[0] == 'downloading']
        self.assertEqual(downloads[-1][4], 'full')
        self.assertEqual(events[-1], ('ready', NEW, 0, 0, 'full'))

    def test_a_held_delta_marker_reports_its_kind(self):
        events = []
        bu = us.BackgroundUpdater(lambda m: None, base=self.base,
                                  onProgress=lambda *a: events.append(a))
        bu.setGameStatus('InProgress')
        bu.notifyReady({'version': NEW, 'kind': 'delta'})
        self.assertEqual(events[-1], ('waiting', NEW, 0, 0, 'delta'))
        bu.notifyReady({'version': NEW})  # a full stage replaced it
        self.assertEqual(events[-1], ('waiting', NEW, 0, 0, 'full'))
        bu.notifyReady({'version': NEW, 'kind': 'delta'})
        bu.setGameStatus(None)  # game over: the held marker is delivered
        self.assertEqual(events[-1], ('ready', NEW, 0, 0, 'delta'))

    async def test_three_argument_callbacks_are_called_as_before(self):
        calls = []
        await self.stage(self.info(), self.server(), onProgress=lambda *a: calls.append(a))
        self.assertTrue(calls)
        self.assertTrue(all(len(c) == 3 for c in calls))


if __name__ == '__main__':
    unittest.main()
