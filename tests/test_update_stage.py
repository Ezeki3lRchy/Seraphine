"""Background update staging (app/common/update_stage.py), the install-dir helper and
MainWindow's update routing.

No network (the HTTP session is a fake), no real config dir (every base is a temp
dir), no windows. The .7z cases build a tiny real archive with py7zr.
"""
import asyncio
import hashlib
import os
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from app.common import platform
from app.common import update_stage as us


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f:
        f.write(data)


class FakeResp:
    def __init__(self, data=b'', status=200, gate=None, chunk=4):
        self.data, self.status, self.gate, self.chunk = data, status, gate, chunk
        self.headers = {'content-length': str(len(data))}
        self.content = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def iter_chunked(self, n):
        return self._gen()

    async def _gen(self):
        for i in range(0, len(self.data), self.chunk):
            yield self.data[i:i + self.chunk]
            if self.gate is not None:
                await self.gate.wait()  # stalls mid-download
            await asyncio.sleep(0)


class FakeSession:
    def __init__(self, resp):
        self.resp = resp
        self.urls = []
        self.kwargs = []

    def get(self, url, **kwargs):
        self.urls.append(url)
        self.kwargs.append(kwargs)
        return self.resp


PACKAGE_FILES = ('Seraphine.exe', '_internal/a.dll', 'filelist.txt')


def _sevenZip(path, files=PACKAGE_FILES):
    import py7zr

    src = tempfile.mkdtemp(prefix='seraphine_pkg_')
    try:
        _write(os.path.join(src, 'Seraphine.exe'), b'new-exe')
        _write(os.path.join(src, '_internal', 'a.dll'), b'new-a')
        _write(os.path.join(src, 'filelist.txt'), b'Seraphine.exe\r\n_internal\\a.dll\r\n')
        with py7zr.SevenZipFile(path, 'w') as z:
            for rel in files:
                z.write(os.path.join(src, rel), arcname=rel)
    finally:
        shutil.rmtree(src, ignore_errors=True)
    with open(path, 'rb') as f:
        return f.read()


def _info(data=b'', version='9.9.9', digest=None):
    name = 'Seraphine.dmg' if platform.IS_MACOS else 'Seraphine.7z'
    return {
        'tag_name': 'v' + version,
        'forbidden': False,
        'assets': [{
            'name': name,
            'browser_download_url': 'https://example.invalid/' + name,
            'digest': digest if digest is not None
            else 'sha256:' + hashlib.sha256(data).hexdigest(),
        }],
    }


class TempBaseMixin:
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='seraphine_stage_')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.base = os.path.join(self.tmp, 'update')

    def stage(self, version, platformName=sys.platform, digest='sha256:ab'):
        """Write a valid stage for this platform by hand."""
        package = os.path.join(self.base, 'package')
        _write(os.path.join(package, 'Seraphine.exe'), b'x')
        _write(os.path.join(package, 'filelist.txt'), b'Seraphine.exe\r\n')
        dmg = os.path.join(self.base, 'Seraphine-update.dmg')
        _write(dmg, b'dmg')
        marker = {'version': version, 'digest': digest, 'platform': platformName,
                  'path': package if platformName == 'win32' else dmg,
                  'size': 0 if platformName == 'win32' else 3}
        us.writeMarker(self.base, marker)
        return marker

    def writeResult(self, status, version='9.9.9'):
        _write(os.path.join(self.base, 'result.json'),
               ('{"status": "%s", "code": 4, "version": "%s"}' % (status, version))
               .encode('utf-8-sig'))


@unittest.skipUnless(sys.platform == 'win32', 'stages the Windows .7z')
class StageReleaseTest(TempBaseMixin, unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        super().setUp()
        self.data = _sevenZip(os.path.join(self.tmp, 'src.7z'))

    async def test_success_extracts_and_writes_marker(self):
        phases = []
        sess = FakeSession(FakeResp(self.data, chunk=64))
        marker = await us.stageRelease(
            _info(self.data), self.base, lambda p, c, t: phases.append(p), sess)

        package = os.path.join(self.base, 'package')
        self.assertEqual(marker['path'], package)
        self.assertEqual(marker['version'], '9.9.9')
        self.assertEqual(marker['platform'], 'win32')
        with open(os.path.join(package, '_internal', 'a.dll'), 'rb') as f:
            self.assertEqual(f.read(), b'new-a')
        self.assertFalse(os.path.exists(os.path.join(self.base, 'Seraphine.7z')))
        self.assertEqual(us.readMarker(self.base), marker)
        self.assertEqual(phases[0], 'download')
        self.assertIn('verify', phases)
        self.assertEqual(phases[-1], 'extract')
        self.assertIs(sess.kwargs[0]['timeout'], us.DOWNLOAD_TIMEOUT)

    async def test_digest_mismatch_stages_nothing_and_removes_file(self):
        sess = FakeSession(FakeResp(self.data))
        with self.assertRaises(us.StageError) as cm:
            await us.stageRelease(_info(self.data, digest='sha256:' + '0' * 64),
                                  self.base, None, sess)
        self.assertIn('verification failed', str(cm.exception))
        self.assertFalse(os.path.exists(self.base))
        self.assertIsNone(us.readMarker(self.base))

    async def test_package_without_filelist_is_refused(self):
        data = _sevenZip(os.path.join(self.tmp, 'nolist.7z'),
                         files=('Seraphine.exe', '_internal/a.dll'))
        with self.assertRaises(us.StageError) as cm:
            await us.stageRelease(_info(data), self.base, None,
                                  FakeSession(FakeResp(data)))
        self.assertIn('filelist.txt', str(cm.exception))
        self.assertFalse(os.path.exists(self.base))

    async def test_package_without_exe_is_refused(self):
        data = _sevenZip(os.path.join(self.tmp, 'noexe.7z'),
                         files=('_internal/a.dll', 'filelist.txt'))
        with self.assertRaises(us.StageError):
            await us.stageRelease(_info(data), self.base, None,
                                  FakeSession(FakeResp(data)))
        self.assertFalse(os.path.exists(self.base))

    async def test_http_error_stages_nothing(self):
        sess = FakeSession(FakeResp(b'', status=404))
        with self.assertRaises(us.StageError):
            await us.stageRelease(_info(self.data), self.base, None, sess)
        self.assertFalse(os.path.exists(self.base))

    async def test_uses_the_configured_proxy(self):
        sess = FakeSession(FakeResp(self.data))
        with mock.patch.object(us, 'downloadProxy', return_value='http://127.0.0.1:7890'):
            await us.stageRelease(_info(self.data), self.base, None, sess)
        self.assertEqual(sess.kwargs[0]['proxy'], 'http://127.0.0.1:7890')

    async def test_already_staged_short_circuits(self):
        info = _info(self.data)
        await us.stageRelease(info, self.base, None, FakeSession(FakeResp(self.data)))
        again = FakeSession(FakeResp(b'', status=500))
        marker = await us.stageRelease(info, self.base, None, again)
        self.assertEqual(again.urls, [])
        self.assertEqual(marker, us.readMarker(self.base))
        self.assertIsNotNone(us.stagedFor(info, self.base))

    async def test_no_digest_is_never_already_staged(self):
        info = _info(self.data, digest='')
        await us.stageRelease(info, self.base, None, FakeSession(FakeResp(self.data)))
        self.assertIsNone(us.stagedFor(info, self.base))

    async def test_different_release_downloads_again(self):
        await us.stageRelease(_info(self.data), self.base, None,
                              FakeSession(FakeResp(self.data)))
        other = _info(self.data, version='9.9.10')
        sess = FakeSession(FakeResp(self.data))
        marker = await us.stageRelease(other, self.base, None, sess)
        self.assertEqual(len(sess.urls), 1)
        self.assertEqual(marker['version'], '9.9.10')

    async def test_cancel_mid_download_cleans_up(self):
        gate = asyncio.Event()
        sess = FakeSession(FakeResp(self.data, gate=gate))
        task = asyncio.ensure_future(
            us.stageRelease(_info(self.data), self.base, None, sess))
        for _ in range(20):
            await asyncio.sleep(0)
        self.assertTrue(os.path.exists(os.path.join(self.base, 'Seraphine.7z.part')))
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(os.path.exists(self.base))


class BackgroundUpdaterTest(unittest.IsolatedAsyncioTestCase):

    def make(self, stage, known=True):
        self.ready = []
        self.starting = []
        bu = us.BackgroundUpdater(self.ready.append, base='unused', stage=stage,
                                  onStarting=self.starting.append)
        if known:
            bu.setGameStatus(None)  # no client: known not in a game
        return bu

    async def settle(self, n=20):
        for _ in range(n):
            await asyncio.sleep(0)

    async def test_unknown_game_status_blocks_download_and_prompt(self):
        stage = mock.AsyncMock(return_value={'version': '9.9.9'})
        bu = self.make(stage, known=False)
        self.assertIsNone(bu.gaming)
        bu.offer({'tag_name': 'v9.9.9'})
        bu.notifyReady({'version': '9.9.8'})
        await self.settle()
        stage.assert_not_called()
        self.assertEqual(self.ready, [])

        bu.setGameStatus('Lobby')
        await self.settle()
        stage.assert_called_once()
        self.assertEqual(self.ready, [{'version': '9.9.8'}, {'version': '9.9.9'}])

        # a client (re)connecting makes it unknown again
        bu.setGameStatusUnknown()
        bu.offer({'tag_name': 'v9.9.10'})
        await self.settle()
        stage.assert_called_once()

    async def test_no_start_during_a_game(self):
        stage = mock.AsyncMock(return_value={'version': '9.9.9'})
        bu = self.make(stage)
        for phase in us.GAME_PHASES:
            bu.setGameStatus(phase)
            bu.offer({'tag_name': 'v9.9.9'})
            await self.settle()
            stage.assert_not_called()
            self.assertIsNone(bu.task)

        bu.setGameStatus('EndOfGame')
        await self.settle()
        stage.assert_called_once()
        self.assertEqual(self.starting, [{'tag_name': 'v9.9.9'}])
        self.assertEqual(self.ready, [{'version': '9.9.9'}])
        self.assertIsNone(bu.pendingInfo)

    async def test_game_start_cancels_and_game_end_retries(self):
        started = []
        gate = asyncio.Event()

        async def stage(info, base):
            started.append(info)
            if len(started) == 1:
                await gate.wait()  # never released: cancelled by the game
            return {'version': '9.9.9'}

        bu = self.make(stage)
        bu.setGameStatus('Lobby')
        bu.offer({'tag_name': 'v9.9.9'})
        await self.settle()
        self.assertTrue(bu.isBusy())

        bu.setGameStatus('ChampSelect')
        await self.settle()
        self.assertFalse(bu.isBusy())
        self.assertEqual(self.ready, [])
        self.assertIsNotNone(bu.pendingInfo)

        bu.setGameStatus('None')
        await self.settle()
        self.assertEqual(len(started), 2)
        self.assertEqual(self.ready, [{'version': '9.9.9'}])

    async def test_ready_prompt_is_held_during_a_game(self):
        bu = self.make(mock.AsyncMock())
        bu.setGameStatus('InProgress')
        bu.notifyReady({'version': '9.9.9'})
        self.assertEqual(self.ready, [])
        bu.setGameStatus('EndOfGame')
        self.assertEqual(self.ready, [{'version': '9.9.9'}])

    async def test_failure_is_silent_and_dropped(self):
        stage = mock.AsyncMock(side_effect=OSError('network down'))
        bu = self.make(stage)
        bu.offer({'tag_name': 'v9.9.9'})
        await self.settle()
        self.assertEqual(self.ready, [])
        self.assertIsNone(bu.pendingInfo)
        self.assertFalse(bu.isBusy())

    async def test_offer_while_busy_does_not_start_twice(self):
        gate = asyncio.Event()
        calls = []

        async def stage(info, base):
            calls.append(info)
            await gate.wait()

        bu = self.make(stage)
        bu.offer({'tag_name': 'v9.9.9'})
        await self.settle()
        bu.offer({'tag_name': 'v9.9.9'})
        await self.settle()
        self.assertEqual(len(calls), 1)
        # a cancel that is not a game (app exit) does not restart the download
        bu.task.cancel()
        await self.settle()
        self.assertEqual(len(calls), 1)
        self.assertFalse(bu.isBusy())
        self.assertIsNone(bu.pendingInfo)


class MarkerTest(TempBaseMixin, unittest.TestCase):

    def test_round_trip(self):
        marker = self.stage('9.9.9')
        self.assertEqual(us.readMarker(self.base), marker)
        self.assertEqual(us.stagedUpdate('1.4.5', self.base), marker)
        self.assertTrue(os.path.exists(self.base))

    def test_not_newer_is_cleaned(self):
        for version in ('1.4.5', '1.4.4'):
            self.stage(version)
            self.assertIsNone(us.stagedUpdate('1.4.5', self.base))
            self.assertFalse(os.path.exists(self.base))

    def test_missing_files_are_cleaned(self):
        marker = self.stage('9.9.9')
        if marker['platform'] == 'win32':
            os.remove(os.path.join(marker['path'], 'filelist.txt'))
        else:
            os.remove(marker['path'])
        self.assertIsNone(us.stagedUpdate('1.4.5', self.base))
        self.assertFalse(os.path.exists(self.base))

    def test_other_platform_is_cleaned(self):
        self.stage('9.9.9', platformName='other')
        self.assertIsNone(us.stagedUpdate('1.4.5', self.base))
        self.assertFalse(os.path.exists(self.base))

    def test_tampered_path_is_cleaned_and_never_applicable(self):
        marker = self.stage('9.9.9')
        elsewhere = os.path.join(self.tmp, 'elsewhere')
        _write(os.path.join(elsewhere, 'Seraphine.exe'), b'x')
        _write(os.path.join(elsewhere, 'filelist.txt'), b'x')
        _write(os.path.join(self.tmp, 'Seraphine-update.dmg'), b'dmg')
        marker['path'] = elsewhere if sys.platform == 'win32' \
            else os.path.join(self.tmp, 'Seraphine-update.dmg')
        us.writeMarker(self.base, marker)
        self.assertFalse(us.markerApplicable(marker, self.base))
        self.assertIsNone(us.stagedUpdate('1.4.5', self.base))
        self.assertTrue(os.path.exists(elsewhere))  # never touched

    def test_garbage_or_missing_marker_is_cleaned(self):
        _write(os.path.join(self.base, 'staged.json'), b'{not json')
        self.assertIsNone(us.stagedUpdate('1.4.5', self.base))
        self.assertFalse(os.path.exists(self.base))
        _write(os.path.join(self.base, 'Seraphine.7z.part'), b'partial')
        self.assertIsNone(us.stagedUpdate('1.4.5', self.base))
        self.assertFalse(os.path.exists(self.base))

    def test_no_stage_dir(self):
        self.assertIsNone(us.stagedUpdate('1.4.5', self.base))

    def test_successful_run_clears_everything(self):
        self.stage('9.9.9')
        self.writeResult('ok')
        self.assertIsNone(us.stagedUpdate('9.9.9', self.base))
        self.assertFalse(os.path.exists(self.base))

    def test_failed_run_keeps_stage_even_when_versions_look_equal(self):
        # a half-applied exe may already report the new version
        for status in ('restored', 'restore-failed', 'failed:4'):
            marker = self.stage('9.9.9')
            self.writeResult(status)
            got = us.stagedUpdate('9.9.9', self.base)
            self.assertEqual(got['lastResult']['status'], status)
            self.assertEqual({k: v for k, v in got.items() if k != 'lastResult'}, marker)
            self.assertTrue(us.markerApplicable(got, self.base))
            us.clearStage(self.base)

    def test_failed_run_older_than_running_is_discarded_with_its_backup(self):
        # the user installed 1.4.8 by hand after a failed 1.4.7 update
        for status in ('restored', 'restore-failed'):
            self.stage('1.4.7')
            self.writeResult(status, version='1.4.7')
            _write(os.path.join(self.base, 'backup', 'Seraphine.exe'), b'v146')
            _write(os.path.join(self.base, 'backup.added.txt'), b'x')
            _write(os.path.join(self.base, 'backup.meta'), b'{}')
            self.assertIsNone(us.stagedUpdate('1.4.8', self.base))
            self.assertFalse(os.path.exists(self.base))

    def test_failed_run_equal_or_newer_is_still_offered(self):
        for version in ('1.4.8', '1.4.9'):
            self.stage(version)
            self.writeResult('restored', version=version)
            got = us.stagedUpdate('1.4.8', self.base)
            self.assertEqual(got['lastResult']['status'], 'restored')
            us.clearStage(self.base)

    def test_nothing_is_cleaned_while_an_updater_holds_the_lock(self):
        self.stage('1.4.7')
        self.writeResult('restored', version='1.4.7')
        with mock.patch.object(us, '_updaterRunning', return_value=True):
            self.assertIsNone(us.stagedUpdate('1.4.8', self.base))
        self.assertIsNotNone(us.readMarker(self.base))
        self.assertIsNotNone(us.readResult(self.base))

    def test_marker_never_applicable_when_it_would_downgrade(self):
        marker = self.stage('1.4.7')
        self.assertFalse(us.markerApplicable(marker, self.base, currentVersion='1.4.8'))
        self.writeResult('restored', version='1.4.7')
        failed = dict(marker, lastResult={'status': 'restored'})
        self.assertFalse(us.markerApplicable(failed, self.base, currentVersion='1.4.8'))
        self.assertTrue(us.markerApplicable(failed, self.base, currentVersion='1.4.6'))

    def test_allow_downgrade_accepts_any_other_version_only(self):
        marker = self.stage('1.4.7')
        self.assertFalse(us.markerApplicable(marker, self.base, currentVersion='1.4.8'))
        self.assertTrue(us.markerApplicable(marker, self.base, currentVersion='1.4.8',
                                            allowDowngrade=True))
        self.assertFalse(us.markerApplicable(marker, self.base, currentVersion='1.4.7',
                                             allowDowngrade=True))
        # everything else still applies: a changed marker is refused
        self.assertFalse(us.markerApplicable(dict(marker, digest='sha256:zz'), self.base,
                                             currentVersion='1.4.8', allowDowngrade=True))

    def test_equal_version_only_for_the_retry_of_a_failed_run(self):
        marker = self.stage('1.4.8')
        failed = dict(marker, lastResult={'status': 'restored'})
        self.assertFalse(us.markerApplicable(marker, self.base, currentVersion='1.4.8'))
        # no failed result on disk -> no retry either
        self.assertFalse(us.markerApplicable(failed, self.base, currentVersion='1.4.8'))
        self.writeResult('restored', version='1.4.8')
        self.assertTrue(us.markerApplicable(failed, self.base, currentVersion='1.4.8'))
        self.assertFalse(us.markerApplicable(marker, self.base, currentVersion='1.4.8'))

    def test_staged_for_matches_version_and_sha256_digest(self):
        self.stage('9.9.9')
        info = _info(digest='sha256:ab')
        self.assertIsNotNone(us.stagedFor(info, self.base))
        self.assertIsNone(us.stagedFor(_info(digest='sha256:ab', version='9.9.8'), self.base))
        self.assertIsNone(us.stagedFor(_info(digest='sha256:cd'), self.base))

    def test_empty_digest_never_matches(self):
        self.stage('9.9.9', digest='')
        self.assertIsNone(us.stagedFor(_info(digest=''), self.base))

    def test_marker_applicable_only_for_the_exact_stage(self):
        marker = self.stage('9.9.9')
        self.assertTrue(us.markerApplicable(marker, self.base))
        self.assertTrue(us.markerApplicable(dict(marker, lastResult={'status': 'x'}),
                                            self.base))
        self.assertFalse(us.markerApplicable(dict(marker, version='9.9.8'), self.base))
        us.clearStage(self.base)
        self.assertFalse(us.markerApplicable(marker, self.base))

    def test_version_order_and_newer(self):
        self.assertGreater(us.parseVersion('1.4.10'), us.parseVersion('1.4.9'))
        self.assertGreater(us.parseVersion('1.5'), us.parseVersion('1.4.9'))
        self.assertEqual(us.parseVersion('v1.4.6'), (1, 4, 6))
        self.assertTrue(us.isNewer({'tag_name': 'v1.4.6'}, '1.4.5'))
        self.assertFalse(us.isNewer({'tag_name': 'v1.4.5'}, '1.4.5'))
        self.assertFalse(us.isNewer({'tag_name': 'v1.4.4'}, '1.4.5'))

    def test_has_digest(self):
        self.assertTrue(us.hasDigest(_info(b'x')))
        self.assertFalse(us.hasDigest(_info(digest='')))
        self.assertFalse(us.hasDigest(_info(digest='sha256:')))
        self.assertFalse(us.hasDigest(_info(digest='md5:abc')))

    def test_download_proxy_follows_the_github_setting(self):
        values = {us.cfg.enableProxy: True, us.cfg.proxyAddr: '127.0.0.1:7890'}
        with mock.patch.object(us.cfg, 'get', side_effect=values.get):
            self.assertEqual(us.downloadProxy(), 'http://127.0.0.1:7890')
        values[us.cfg.proxyAddr] = 'http://proxy:8080'
        with mock.patch.object(us.cfg, 'get', side_effect=values.get):
            self.assertEqual(us.downloadProxy(), 'http://proxy:8080')
        values[us.cfg.enableProxy] = False
        with mock.patch.object(us.cfg, 'get', side_effect=values.get):
            self.assertIsNone(us.downloadProxy())


class ApplyStagedUpdateTest(TempBaseMixin, unittest.IsolatedAsyncioTestCase):

    def launch(self, logText):
        log = os.path.join(self.tmp, 'updater.log')
        if logText is not None:
            _write(log, logText.encode('utf-8'))
        proc = mock.Mock()
        proc.poll.return_value = None
        from app.common.update import UpdaterLaunch
        return UpdaterLaunch(proc, log)

    def patchRunner(self, launch):
        name = 'runUpdaterMac' if sys.platform == 'darwin' else 'runUpdater'
        from app.common import update
        return mock.patch.object(update, name, return_value=launch)

    async def test_downgrade_is_refused_without_launching(self):
        marker = self.stage('1.0.0')
        with self.patchRunner(self.launch('updater started')) as runner:
            self.assertFalse(await us.applyStagedUpdate(marker, self.base))
        runner.assert_not_called()

    async def test_downgrade_with_allow_downgrade_launches(self):
        marker = self.stage('1.0.0')
        with self.patchRunner(self.launch('updater started')) as runner:
            self.assertTrue(await us.applyStagedUpdate(marker, self.base, startTimeout=1,
                                                       allowDowngrade=True))
        runner.assert_called_once()

    async def test_startup_check_waits_for_a_running_stage(self):
        # the start-up clean-up is serialised with staging (same lock)
        lock = us._stageLock()
        await lock.acquire()
        task = asyncio.ensure_future(us.stagedUpdateAsync('1.4.5', self.base))
        await asyncio.sleep(0.05)
        self.assertFalse(task.done())
        lock.release()
        self.assertIsNone(await task)

    async def test_stale_marker_is_refused_without_launching(self):
        marker = self.stage('9.9.9')
        us.clearStage(self.base)
        with self.patchRunner(self.launch('updater started')) as runner:
            self.assertFalse(await us.applyStagedUpdate(marker, self.base))
        runner.assert_not_called()

    async def test_started_updater_is_confirmed(self):
        marker = self.stage('9.9.9')
        with self.patchRunner(self.launch('[x] updater started: ...')) as runner:
            self.assertTrue(await us.applyStagedUpdate(marker, self.base, startTimeout=1))
        runner.assert_called_once()

    async def test_concurrent_apply_is_refused(self):
        marker = self.stage('9.9.9')
        with self.patchRunner(self.launch(None)) as runner:
            first = asyncio.ensure_future(
                us.applyStagedUpdate(marker, self.base, startTimeout=0.5))
            await asyncio.sleep(0.05)
            self.assertFalse(await us.applyStagedUpdate(marker, self.base))
            self.assertFalse(await first)
        runner.assert_called_once()
        self.assertFalse(us._applying)

    def test_default_start_timeout_is_generous(self):
        self.assertGreaterEqual(us.UPDATER_START_TIMEOUT, 20)

    async def test_updater_that_never_starts_is_killed_and_app_stays(self):
        marker = self.stage('9.9.9')
        launch = self.launch(None)
        with self.patchRunner(launch):
            self.assertFalse(await us.applyStagedUpdate(marker, self.base,
                                                        startTimeout=0.3))
        launch.process.kill.assert_called_once()


class MainWindowRoutingTest(unittest.IsolatedAsyncioTestCase):
    """MainWindow update slots on a fake self."""

    def setUp(self):
        from app.view import main_window as mw
        self.mw = mw
        self.fake = SimpleNamespace(
            manualCheckBusy=False,
            tr=lambda text: text,
            backgroundUpdater=mock.Mock(gaming=False),
            _MainWindow__onShowUpdateMessageBox=mock.Mock(),
            _MainWindow__showUpdateError=mock.Mock(),
            _MainWindow__closeUpdateReadyBar=mock.Mock(),
            _MainWindow__setRestartButtonBusy=mock.Mock(),
            _applyingUpdate=False, _updateReadyButton=None,
            _updateReadyBar=None, _updateReadyMarker=None)
        self.fake._MainWindow__backgroundUpdateEligible = \
            lambda info: mw.MainWindow._MainWindow__backgroundUpdateEligible(self.fake, info)
        self.fake.backgroundUpdater.isBusy.return_value = False

    def route(self, info, auto=True, packaged=True, writable=True):
        with mock.patch.object(self.mw.cfg, 'get', return_value=auto), \
                mock.patch.object(self.mw, 'isPackaged', return_value=packaged), \
                mock.patch.object(self.mw, 'installWritable', return_value=writable):
            self.mw.MainWindow._MainWindow__onUpdateAvailable(self.fake, info)

    def test_packaged_with_auto_download_stages_in_background(self):
        info = _info(b'x')
        self.route(info)
        self.fake.backgroundUpdater.offer.assert_called_once_with(info)
        self.fake._MainWindow__onShowUpdateMessageBox.assert_not_called()

    def assertDialog(self, info, **kw):
        self.fake._MainWindow__onShowUpdateMessageBox.reset_mock()
        self.route(info, **kw)
        self.fake._MainWindow__onShowUpdateMessageBox.assert_called_once_with(info)
        self.fake.backgroundUpdater.offer.assert_not_called()

    def test_dialog_when_off_source_run_or_forbidden(self):
        self.assertDialog(_info(b'x'), auto=False)
        self.assertDialog(_info(b'x'), packaged=False)
        self.assertDialog(dict(_info(b'x'), forbidden=True))

    def test_downgrade_or_same_version_is_not_downloaded(self):
        self.assertDialog(_info(b'x', version='1.0.0'))
        self.assertDialog(_info(b'x', version=self.mw.VERSION))

    def test_release_without_digest_takes_the_dialog(self):
        self.assertDialog(_info(digest=''))

    def test_unwritable_install_takes_the_dialog(self):
        self.assertDialog(_info(b'x'), writable=False)

    def test_game_start_hides_ready_prompt_and_holds_it(self):
        bar, marker = mock.Mock(), {'version': '9.9.9'}
        self.fake._updateReadyBar, self.fake._updateReadyMarker = bar, marker
        self.mw.MainWindow._MainWindow__syncUpdaterGameStatus(self.fake, 'GameStart')
        bar.close.assert_called_once()
        self.assertIs(self.fake.backgroundUpdater.heldMarker, marker)
        self.assertIsNone(self.fake._updateReadyBar)
        self.fake.backgroundUpdater.setGameStatus.assert_called_once_with('GameStart')

    def test_lobby_keeps_ready_prompt(self):
        bar = mock.Mock()
        self.fake._updateReadyBar = bar
        self.mw.MainWindow._MainWindow__syncUpdaterGameStatus(self.fake, 'Lobby')
        bar.close.assert_not_called()
        self.fake.backgroundUpdater.setGameStatus.assert_called_once_with('Lobby')

    async def restart(self, marker):
        with mock.patch.object(self.mw.sys, 'exit') as exit_, \
                mock.patch.object(self.mw, 'applyStagedUpdate',
                                  mock.AsyncMock(return_value=True)) as apply:
            await self.mw.MainWindow._MainWindow__restartToUpdate(self.fake, marker)
        return exit_, apply

    async def test_restart_refuses_a_stale_marker(self):
        with mock.patch.object(self.mw, 'markerApplicable', return_value=False):
            exit_, apply = await self.restart({'version': '1.5.0'})
        exit_.assert_not_called()
        apply.assert_not_called()
        self.fake._MainWindow__showUpdateError.assert_called_once()
        self.fake._MainWindow__closeUpdateReadyBar.assert_called_once()

    async def test_restart_refuses_while_a_download_runs(self):
        self.fake.backgroundUpdater.isBusy.return_value = True
        with mock.patch.object(self.mw, 'markerApplicable', return_value=True):
            exit_, apply = await self.restart({'version': '1.5.0'})
        exit_.assert_not_called()
        apply.assert_not_called()

    async def test_restart_exits_only_after_the_updater_started(self):
        with mock.patch.object(self.mw, 'markerApplicable', return_value=True), \
                mock.patch.object(self.mw, 'signalBus') as bus:
            exit_, apply = await self.restart({'version': '1.5.0'})
        apply.assert_awaited_once()
        bus.terminateListeners.emit.assert_called_once()
        exit_.assert_called_once()
        self.fake._MainWindow__setRestartButtonBusy.assert_called_once_with(True)

    async def test_cancelled_apply_resets_the_button(self):
        gate = asyncio.Event()

        async def hang(marker):
            await gate.wait()

        # qasync's done-callback re-raises the CancelledError into the loop's handler
        loop = asyncio.get_running_loop()
        previous = loop.get_exception_handler()
        loop.set_exception_handler(lambda l, ctx: None)
        self.addCleanup(loop.set_exception_handler, previous)
        with mock.patch.object(self.mw, 'markerApplicable', return_value=True), \
                mock.patch.object(self.mw, 'applyStagedUpdate', mock.AsyncMock(side_effect=hang)):
            task = self.mw.MainWindow._MainWindow__restartToUpdate(self.fake, {'v': 1})
            await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(self.fake._MainWindow__setRestartButtonBusy.call_args_list,
                         [mock.call(True), mock.call(False)])
        self.assertFalse(self.fake._applyingUpdate)

    async def test_second_click_while_applying_does_nothing(self):
        gate = asyncio.Event()

        async def slowApply(marker):
            await gate.wait()
            return False

        apply = mock.AsyncMock(side_effect=slowApply)
        with mock.patch.object(self.mw, 'markerApplicable', return_value=True), \
                mock.patch.object(self.mw, 'applyStagedUpdate', apply), \
                mock.patch.object(self.mw.sys, 'exit') as exit_:
            first = self.mw.MainWindow._MainWindow__restartToUpdate(self.fake, {'v': 1})
            await asyncio.sleep(0)
            self.assertTrue(self.fake._applyingUpdate)
            await self.mw.MainWindow._MainWindow__restartToUpdate(self.fake, {'v': 1})
            gate.set()
            await first
        self.assertEqual(apply.await_count, 1)
        exit_.assert_not_called()
        # launch failed: the button is re-enabled and the guard released
        self.assertEqual(self.fake._MainWindow__setRestartButtonBusy.call_args_list,
                         [mock.call(True), mock.call(False)])
        self.assertFalse(self.fake._applyingUpdate)

    async def offerAtStart(self, marker, auto):
        fake = SimpleNamespace(backgroundUpdater=mock.Mock())
        with mock.patch.object(self.mw, 'isPackaged', return_value=True), \
                mock.patch.object(self.mw, 'stagedUpdateAsync',
                                  mock.AsyncMock(return_value=marker)), \
                mock.patch.object(self.mw.cfg, 'get', return_value=auto):
            await self.mw.MainWindow._MainWindow__offerStagedUpdate(fake)
        return fake.backgroundUpdater.notifyReady

    async def test_switch_off_keeps_a_ready_stage_quiet_but_reports_failures(self):
        ready = {'version': '9.9.9'}
        failed = dict(ready, lastResult={'status': 'restored', 'code': 4})
        (await self.offerAtStart(ready, auto=False)).assert_not_called()
        (await self.offerAtStart(failed, auto=False)).assert_called_once_with(failed)
        (await self.offerAtStart(ready, auto=True)).assert_called_once_with(ready)


class CheckUpdateVersionTest(unittest.TestCase):
    """Github.checkUpdate: strictly newer, or older only for a forbidden version."""

    def check(self, tag, running='1.4.8', forbidden=False):
        from app.common import util
        info = _info(b'x', version=tag)
        with mock.patch.object(util, 'VERSION', running), \
                mock.patch.object(util.github, 'getReleasesInfo', return_value=info), \
                mock.patch.object(util.github, '_Github__get_ver_info',
                                  return_value={'forbidden': forbidden}):
            return util.github.checkUpdate()

    def test_newer_is_an_update(self):
        self.assertEqual(self.check('1.4.9')['tag_name'], 'v1.4.9')

    def test_equal_or_older_is_up_to_date(self):
        self.assertIsNone(self.check('1.4.8'))
        self.assertIsNone(self.check('1.4.7'))
        self.assertIsNone(self.check('1.4.7', running='1.4.10'))

    def test_older_is_offered_only_for_a_forbidden_version(self):
        info = self.check('1.4.7', forbidden=True)
        self.assertEqual((info['tag_name'], info['forbidden']), ('v1.4.7', True))
        self.assertIsNone(self.check('1.4.8', forbidden=True))

    def test_manual_check_on_a_newer_dev_build_says_up_to_date(self):
        from app.common import util
        with mock.patch.object(util, 'VERSION', '9.9.9'), \
                mock.patch.object(util.github, 'getReleasesInfo',
                                  return_value=_info(b'x', version='1.4.8')), \
                mock.patch.object(util.github, '_Github__get_ver_info', return_value={}):
            self.assertEqual(util.checkUpdateOutcome(), ('latest', None))


class DialogDowngradeFlagTest(unittest.IsolatedAsyncioTestCase):
    """UpdateMessageBox passes allowDowngrade only for a forbidden running version."""

    async def run_yes(self, forbidden):
        from app.components import message_box as mb
        fake = SimpleNamespace(
            info=dict(_info(b'x', version='1.4.7'), forbidden=forbidden),
            infoLabel=mock.Mock(), bar=mock.Mock(), myYesButton=mock.Mock(),
            myCancelButton=mock.Mock(), manuallyButton=mock.Mock(),
            tr=lambda text: text,
            _UpdateMessageBox__onStageProgress=mock.Mock(),
            _UpdateMessageBox__onUpdateAborted=mock.Mock())
        apply = mock.AsyncMock(return_value=False)
        with mock.patch.object(mb, 'stageRelease', mock.AsyncMock(return_value={'v': 1})), \
                mock.patch.object(mb, 'applyStagedUpdate', apply):
            await mb.UpdateMessageBox._UpdateMessageBox__onYesButtonClicked(fake)
        return apply

    async def test_forbidden_allows_downgrade(self):
        apply = await self.run_yes(forbidden=True)
        self.assertEqual(apply.call_args.kwargs, {'allowDowngrade': True})

    async def test_not_forbidden_never_allows_downgrade(self):
        apply = await self.run_yes(forbidden=False)
        self.assertEqual(apply.call_args.kwargs, {'allowDowngrade': False})


class ClientAbsentSignalTest(unittest.TestCase):
    """lolClientAbsent ("not in a game") only when neither client nor game runs."""

    class Stop(Exception):
        pass

    def runFirstPass(self, mac, gameRunning):
        from PySide6.QtCore import Qt
        from app.common.signals import signalBus
        from app.lol import listener as ls

        seen = []
        slot = lambda: seen.append(1)
        signalBus.lolClientAbsent.connect(slot, Qt.DirectConnection)
        self.addCleanup(signalBus.lolClientAbsent.disconnect, slot)
        lst = ls.LolProcessExistenceListener(None)
        stop = mock.patch.object(lst, 'msleep', side_effect=self.Stop)
        with stop, mock.patch.object(ls, 'getLolClientPids', return_value=[]), \
                mock.patch.object(ls, 'getLolClientPidsSlowly', return_value=[]), \
                mock.patch.object(ls, 'getTasklistPath', return_value='tasklist'), \
                mock.patch.object(ls, 'isLolGameProcessExist', return_value=gameRunning), \
                mock.patch.object(ls, 'isLolGameProcessExistMac', return_value=gameRunning), \
                mock.patch.object(ls.platform, 'IS_WINDOWS', not mac):
            with self.assertRaises(self.Stop):
                lst.run()
        return seen

    def test_windows_no_client_no_game(self):
        self.assertEqual(self.runFirstPass(mac=False, gameRunning=False), [1])

    def test_windows_client_closed_during_a_game(self):
        self.assertEqual(self.runFirstPass(mac=False, gameRunning=True), [])

    def test_mac_no_client_no_game(self):
        self.assertEqual(self.runFirstPass(mac=True, gameRunning=False), [1])

    def test_mac_client_closed_during_a_game(self):
        self.assertEqual(self.runFirstPass(mac=True, gameRunning=True), [])


class InstallDirTest(unittest.TestCase):

    def test_source_run_is_not_packaged(self):
        with mock.patch.object(sys, 'frozen', False, create=True):
            self.assertIsNone(platform.install_dir())
            self.assertFalse(platform.is_packaged_windows_build())
            self.assertFalse(us.installWritable())

    def test_frozen_install_dir_is_the_executable_dir_not_cwd(self):
        root = tempfile.mkdtemp(prefix='seraphine_inst_')
        self.addCleanup(shutil.rmtree, root, True)
        exe = os.path.join(root, 'Seraphine.exe')
        internal = os.path.join(root, '_internal')
        os.makedirs(internal)
        with mock.patch.object(sys, 'frozen', True, create=True), \
                mock.patch.object(sys, 'executable', exe):
            self.assertEqual(platform.install_dir(), root)
            self.assertFalse(platform.is_packaged_windows_build())  # no exe yet
            _write(exe, b'exe')
            cwd = os.getcwd()
            os.chdir(internal)  # the frozen app's real CWD
            try:
                self.assertTrue(platform.is_packaged_windows_build())
                from app.components import message_box
                with mock.patch.object(message_box.platform, 'IS_MACOS', False):
                    self.assertTrue(message_box.isPackaged())
            finally:
                os.chdir(cwd)

    @unittest.skipIf(platform.IS_MACOS, 'Windows install layout')
    def test_install_writable_probes_install_and_internal(self):
        root = tempfile.mkdtemp(prefix='seraphine_inst_')
        self.addCleanup(shutil.rmtree, root, True)
        os.makedirs(os.path.join(root, '_internal'))
        with mock.patch.object(sys, 'frozen', True, create=True), \
                mock.patch.object(sys, 'executable', os.path.join(root, 'Seraphine.exe')):
            self.assertTrue(us.installWritable())
            self.assertEqual(os.listdir(root), ['_internal'])  # probe removed
            probed = []
            with mock.patch.object(us, '_probeWritable',
                                   side_effect=lambda d: probed.append(d) or
                                   not d.endswith('_internal')):
                self.assertFalse(us.installWritable())
            self.assertEqual(probed, [root, os.path.join(root, '_internal')])

    @unittest.skipUnless(sys.platform == 'win32', 'drive letters')
    def test_drive_root_install_is_not_writable_for_the_updater(self):
        with mock.patch.object(us.platform, 'install_dir', return_value='C:\\'), \
                mock.patch.object(us, '_probeWritable', return_value=True):
            self.assertFalse(us.installWritable())

    def test_stage_base_lives_in_config_dir(self):
        with mock.patch.object(us.platform, 'config_dir', return_value='C:/cfg'):
            self.assertEqual(us.stageBase(), os.path.join('C:/cfg', 'update'))


if __name__ == '__main__':
    unittest.main()
