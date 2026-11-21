"""Resuming an update download with an HTTP range (app/common/update_stage.py).

A paused download keeps ``<asset>.part`` + ``.part.json`` (stable download URL, sha256
digest, size of the asset) and continues with ``Range: bytes=N-``; only a 206 whose
Content-Range is exactly ``bytes N-(size-1)/size`` is appended, anything else restarts
from byte 0, and the digest is checked on the whole file. No network: a fake session
serves the asset, and one test runs a real aiohttp server on 127.0.0.1 for the redirect.
Runs on Windows and macOS (the .dmg download takes the same path).
"""
import asyncio
import hashlib
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from unittest import mock

from app.common import platform
from app.common import update_stage as us
from tests.test_update_stage import _sevenZip


class RangeResp:
    def __init__(self, status, body=b'', headers=None, chunk=16, stall=None):
        self.status, self.body, self.chunk, self.stall = status, body, chunk, stall
        self.headers = {k: str(v) for k, v in (headers or {}).items()}
        self.content = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def iter_chunked(self, n):
        return self._gen()

    async def _gen(self):
        for i in range(0, len(self.body), self.chunk):
            if self.stall is not None and i >= self.stall:
                await asyncio.Event().wait()  # stalls until cancelled (a game starts)
            yield self.body[i:i + self.chunk]
            await asyncio.sleep(0)


class RangeSession:
    """Fake aiohttp session serving ``data``. ``mode`` decides how a Range request is
    answered: 'range' (correct 206), 'ignore' (200, full body), '416', 'badrange'
    (206 with a Content-Range for another size), 'liar' (206 with the right
    Content-Range but the body starts at byte 0), 'liarfull' (same, whole body),
    'error' (500 for everything). ``stallAfter``: the first response stalls after
    that many bytes."""

    def __init__(self, data, mode='range', chunk=16, stallAfter=None):
        self.data, self.mode, self.chunk, self.stallAfter = data, mode, chunk, stallAfter
        self.requests = []

    def get(self, url, **kwargs):
        rng = (kwargs.get('headers') or {}).get('Range')
        self.requests.append({'url': url, 'range': rng, 'kwargs': kwargs})
        stall = self.stallAfter if len(self.requests) == 1 else None
        n = len(self.data)
        if self.mode == 'error':
            return RangeResp(500)
        if rng is None or self.mode == 'ignore':
            return RangeResp(200, self.data, {'content-length': n}, self.chunk, stall)
        start = int(rng[len('bytes='):-1])
        good = {'content-range': f'bytes {start}-{n - 1}/{n}', 'content-length': n - start}
        if self.mode == 'range':
            return RangeResp(206, self.data[start:], good, self.chunk, stall)
        if self.mode == '416':
            return RangeResp(416, b'', {'content-range': f'bytes */{n}'})
        if self.mode == 'badrange':
            return RangeResp(206, self.data[start:],
                             {'content-range': f'bytes {start}-{n - 1}/{n + 1}'}, self.chunk)
        if self.mode == 'liar':
            return RangeResp(206, self.data[:n - start], good, self.chunk)
        if self.mode == 'liarfull':
            return RangeResp(206, self.data, good, self.chunk)
        raise AssertionError(self.mode)

    @property
    def ranges(self):
        return [r['range'] for r in self.requests]


ASSET_NAME = 'Seraphine.dmg' if platform.IS_MACOS else 'Seraphine.7z'
STAGED_ASSET = us.DMG_NAME if platform.IS_MACOS else us.ARCHIVE_NAME
URL = 'https://example.invalid/' + ASSET_NAME


def assetInfo(data, version='9.9.9', digest=None, size=None):
    return {
        'tag_name': 'v' + version,
        'forbidden': False,
        'assets': [{
            'name': ASSET_NAME,
            'browser_download_url': URL,
            'size': len(data) if size is None else size,
            'digest': digest if digest is not None
            else 'sha256:' + hashlib.sha256(data).hexdigest(),
        }],
    }


def payload(tmp):
    """A real release package on Windows (it is extracted), any bytes on macOS."""
    if sys.platform == 'win32':
        return _sevenZip(os.path.join(tmp, 'src.7z'))
    return bytes(range(256)) * 3


def keyOf(info):
    return us.resumeKey(info['assets'][0])


def writePart(path, data, key):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f:
        f.write(data)
    with open(path + '.json', 'w', encoding='utf-8') as f:
        json.dump(key, f)


def readBytes(path):
    with open(path, 'rb') as f:
        return f.read()


class TempDirMixin:
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='seraphine_resume_')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.base = os.path.join(self.tmp, 'update')
        self.data = payload(self.tmp)
        self.info = assetInfo(self.data)
        self.key = keyOf(self.info)
        self.n = len(self.data)
        self.half = self.n // 2


class DownloadAssetTest(TempDirMixin, unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        super().setUp()
        os.makedirs(self.base)
        self.dest = os.path.join(self.base, STAGED_ASSET)
        self.part = self.dest + '.part'

    def keepHalf(self):
        writePart(self.part, self.data[:self.half], self.key)

    async def download(self, sess, resume='key', progress=None):
        return await us.downloadAsset(URL, self.dest, progress, sess, None,
                                      resume=self.key if resume == 'key' else resume)

    async def test_resume_sends_range_and_appends(self):
        self.keepHalf()
        sess, seen = RangeSession(self.data), []
        got = await self.download(sess, progress=lambda p, c, t: seen.append((p, c, t)))
        self.assertEqual(sess.ranges, [f'bytes={self.half}-'])
        self.assertEqual(readBytes(self.dest), self.data)
        self.assertEqual(got, (self.n - self.half, self.half))
        self.assertGreater(seen[0][1], self.half)  # progress counts the kept bytes
        self.assertEqual(seen[-1], ('download', self.n, self.n))
        self.assertFalse(os.path.exists(self.part))

    async def test_server_ignoring_the_range_restarts_without_doubling(self):
        self.keepHalf()
        sess = RangeSession(self.data, mode='ignore')
        got = await self.download(sess)
        self.assertEqual(sess.ranges, [f'bytes={self.half}-'])
        self.assertEqual(readBytes(self.dest), self.data)
        self.assertEqual(got, (self.n, 0))

    async def test_416_restarts_from_zero(self):
        self.keepHalf()
        sess = RangeSession(self.data, mode='416')
        await self.download(sess)
        self.assertEqual(sess.ranges, [f'bytes={self.half}-', None])
        self.assertEqual(readBytes(self.dest), self.data)

    async def test_wrong_content_range_restarts_from_zero(self):
        self.keepHalf()
        sess = RangeSession(self.data, mode='badrange')
        await self.download(sess)
        self.assertEqual(sess.ranges, [f'bytes={self.half}-', None])
        self.assertEqual(readBytes(self.dest), self.data)

    async def test_part_of_another_asset_is_not_resumed(self):
        writePart(self.part, b'x' * self.half, dict(self.key, digest='sha256:' + '1' * 64))
        sess = RangeSession(self.data)
        await self.download(sess)
        self.assertEqual(sess.ranges, [None])
        self.assertEqual(readBytes(self.dest), self.data)

    async def test_part_longer_than_the_asset_is_not_resumed(self):
        writePart(self.part, self.data + b'tail', self.key)
        sess = RangeSession(self.data)
        await self.download(sess)
        self.assertEqual(sess.ranges, [None])
        self.assertEqual(readBytes(self.dest), self.data)

    async def test_without_resume_a_kept_part_is_ignored_and_removed_on_cancel(self):
        self.keepHalf()
        sess = RangeSession(self.data, stallAfter=32)
        task = asyncio.ensure_future(self.download(sess, resume=None))
        for _ in range(50):
            await asyncio.sleep(0)
        self.assertEqual(sess.ranges, [None])
        self.assertEqual(sess.requests[0]['kwargs'].keys(), {'timeout', 'proxy'})
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(os.path.exists(self.part))

    async def test_cancel_keeps_the_part_and_its_key(self):
        sess = RangeSession(self.data, stallAfter=48)
        task = asyncio.ensure_future(self.download(sess))
        for _ in range(50):
            await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(readBytes(self.part), self.data[:48])
        with open(self.part + '.json', encoding='utf-8') as f:
            self.assertEqual(json.load(f), self.key)

    async def test_http_error_removes_the_part_and_its_key(self):
        self.keepHalf()
        sess = RangeSession(self.data, mode='error')
        with self.assertRaises(us.StageError) as cm:
            await self.download(sess)
        self.assertEqual(str(cm.exception), 'HTTP 500')
        self.assertEqual(sess.ranges, [f'bytes={self.half}-', None])
        self.assertFalse(os.path.exists(self.part))
        self.assertFalse(os.path.exists(self.part + '.json'))

    async def test_full_body_behind_a_matching_content_range_is_refused(self):
        self.keepHalf()
        sess = RangeSession(self.data, mode='liarfull')
        with self.assertRaises(us.StageError) as cm:
            await self.download(sess)
        self.assertIn('larger than the release asset', str(cm.exception))
        self.assertFalse(os.path.exists(self.part))
        self.assertFalse(os.path.exists(self.part + '.json'))
        self.assertFalse(os.path.exists(self.dest))

    def test_content_range_must_cover_exactly_the_rest(self):
        ok = us._contentRangeOk
        self.assertTrue(ok('bytes 10-99/100', 10, 100))
        self.assertTrue(ok('Bytes 10-99/100', 10, 100))
        for bad in ('bytes 0-99/100', 'bytes 10-98/100', 'bytes 10-99/101', 'bytes 10-99/*',
                    'bytes */100', '', None, 'items 10-99/100'):
            with self.subTest(bad=bad):
                self.assertFalse(ok(bad, 10, 100))

    def test_resume_key_needs_size_and_digest(self):
        asset = self.info['assets'][0]
        self.assertEqual(us.resumeKey(asset),
                         {'browser_download_url': URL, 'digest': asset['digest'],
                          'size': self.n})
        self.assertIsNone(us.resumeKey(dict(asset, size=0)))
        self.assertIsNone(us.resumeKey({k: v for k, v in asset.items() if k != 'size'}))
        self.assertIsNone(us.resumeKey(dict(asset, digest='')))

    async def test_a_redirect_keeps_the_range_header(self):
        # real aiohttp client + server on 127.0.0.1: the stable URL redirects (302) to
        # a "CDN" URL, as GitHub's browser_download_url does
        from aiohttp import web

        data, seen = self.data, []

        async def asset(request):
            seen.append(('asset', request.headers.get('Range')))
            raise web.HTTPFound('/cdn/' + ASSET_NAME)

        async def cdn(request):
            rng = request.headers.get('Range')
            seen.append(('cdn', rng))
            start = int(rng[len('bytes='):-1])
            return web.Response(status=206, body=data[start:], headers={
                'Content-Range': f'bytes {start}-{len(data) - 1}/{len(data)}'})

        app = web.Application()
        app.router.add_get('/asset', asset)
        app.router.add_get('/cdn/' + ASSET_NAME, cdn)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        self.addAsyncCleanup(runner.cleanup)
        port = site._server.sockets[0].getsockname()[1]

        self.keepHalf()
        got = await us.downloadAsset(f'http://127.0.0.1:{port}/asset', self.dest,
                                     resume=self.key)
        self.assertEqual(seen, [('asset', f'bytes={self.half}-'),
                                ('cdn', f'bytes={self.half}-')])
        self.assertEqual(readBytes(self.dest), data)
        self.assertEqual(got, (self.n - self.half, self.half))


class StageResumeTest(TempDirMixin, unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        super().setUp()
        self.part = os.path.join(self.base, STAGED_ASSET + '.part')

    def stagingLeft(self):
        return [n for n in os.listdir(self.base) if n.startswith(STAGED_ASSET)] \
            if os.path.isdir(self.base) else []

    async def test_resumed_stage_verifies_the_whole_file(self):
        writePart(self.part, self.data[:self.half], self.key)
        sess, verified = RangeSession(self.data), []
        real = us.verifyAssetDigest

        def spy(path, asset):
            verified.append(hashlib.sha256(readBytes(path)).hexdigest())
            return real(path, asset)

        with mock.patch.object(us, 'verifyAssetDigest', side_effect=spy):
            marker = await us.stageRelease(self.info, self.base, None, sess, resumable=True)
        self.assertEqual(sess.ranges, [f'bytes={self.half}-'])
        self.assertEqual(verified, [hashlib.sha256(self.data).hexdigest()])
        self.assertEqual(marker['version'], '9.9.9')
        self.assertEqual(self.stagingLeft(), [] if sys.platform == 'win32' else [STAGED_ASSET])

    async def test_lying_server_fails_the_whole_file_digest(self):
        # Reverse: 206 + a matching Content-Range, but the body restarts at byte 0
        writePart(self.part, self.data[:self.half], self.key)
        sess = RangeSession(self.data, mode='liar')
        with self.assertRaises(us.StageError) as cm:
            await us.stageRelease(self.info, self.base, None, sess, resumable=True)
        self.assertIn('verification failed', str(cm.exception))
        self.assertEqual(self.stagingLeft(), [])
        self.assertIsNone(us.readMarker(self.base))

    async def test_kept_part_of_another_release_is_dropped(self):
        other = assetInfo(self.data[::-1], version='9.9.8')
        writePart(self.part, self.data[::-1][:self.half], keyOf(other))
        sess = RangeSession(self.data)
        await us.stageRelease(self.info, self.base, None, sess, resumable=True)
        self.assertEqual(sess.ranges, [None])

    async def test_without_resumable_a_kept_part_is_dropped(self):
        writePart(self.part, self.data[:self.half], self.key)
        sess = RangeSession(self.data)
        await us.stageRelease(self.info, self.base, None, sess)
        self.assertEqual(sess.ranges, [None])
        self.assertNotIn(STAGED_ASSET + '.part.json', os.listdir(self.base))

    async def test_asset_without_size_is_downloaded_whole(self):
        info = assetInfo(self.data, size=0)
        writePart(self.part, self.data[:self.half], self.key)
        sess = RangeSession(self.data, stallAfter=32)
        task = asyncio.ensure_future(
            us.stageRelease(info, self.base, None, sess, resumable=True))
        for _ in range(50):
            await asyncio.sleep(0)
        self.assertEqual(sess.ranges, [None])
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.stagingLeft(), [])

    async def test_pause_while_verifying_keeps_the_complete_download(self):
        entered, release = threading.Event(), threading.Event()
        real = us.verifyAssetDigest

        def slow(path, asset):
            entered.set()
            release.wait(10)
            return real(path, asset)

        sess = RangeSession(self.data)
        with mock.patch.object(us, 'verifyAssetDigest', side_effect=slow):
            task = asyncio.ensure_future(
                us.stageRelease(self.info, self.base, None, sess, resumable=True))
            while not entered.is_set():
                await asyncio.sleep(0.01)
            task.cancel()
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(readBytes(self.part), self.data)
        self.assertEqual(sorted(self.stagingLeft()),
                         [STAGED_ASSET + '.part', STAGED_ASSET + '.part.json'])

        marker = await us.stageRelease(self.info, self.base, None, sess, resumable=True)
        self.assertEqual(len(sess.requests), 1)  # verified again, not downloaded again
        self.assertEqual(marker['version'], '9.9.9')

    async def test_digest_mismatch_after_resume_leaves_nothing(self):
        info = assetInfo(self.data, digest='sha256:' + '0' * 64)
        writePart(self.part, self.data[:self.half], keyOf(info))
        sess = RangeSession(self.data)
        with self.assertRaises(us.StageError):
            await us.stageRelease(info, self.base, None, sess, resumable=True)
        self.assertEqual(sess.ranges, [f'bytes={self.half}-'])
        self.assertEqual(self.stagingLeft(), [])


if __name__ == '__main__':
    unittest.main()
