"""Update download progress: the text (app/common/update_progress.py), the UI throttle,
and BackgroundUpdater's ``onProgress(state, version, cur, total, kind)`` driven through
the real stageRelease with a fake HTTP session (downloading -> verifying -> extracting ->
ready; waiting while a game runs, then resuming from the partial file; failed).
No network, no real config dir, no windows. Runs on Windows and macOS.
"""
import asyncio
import hashlib
import os
import sys
import unittest
from unittest import mock

from app.common import update_stage as us
from app.common.update_progress import (MB, REASONS, STATES, Throttle, progressPayload,
                                        progressText, reasonText)
from tests.test_download_resume import (RangeSession, STAGED_ASSET, TempDirMixin,
                                        assetInfo, readBytes)


class ProgressTextTest(unittest.TestCase):

    def test_downloading_with_size(self):
        self.assertEqual(progressText('downloading', '1.4.11', int(63.4 * MB), 150 * MB),
                         'Downloading 1.4.11 - 42% (63 of 150 MB)')

    def test_percent_never_reaches_100_before_the_end(self):
        self.assertEqual(progressText('downloading', '1.4.11', 150 * MB - 1, 150 * MB),
                         'Downloading 1.4.11 - 99% (150 of 150 MB)')
        self.assertEqual(progressText('downloading', '1.4.11', 150 * MB, 150 * MB),
                         'Downloading 1.4.11 - 100% (150 of 150 MB)')

    def test_small_download_has_one_decimal(self):
        self.assertEqual(progressText('downloading', '1.4.11', 3 * MB // 2, 6 * MB),
                         'Downloading 1.4.11 - 25% (1.5 of 6.0 MB)')

    def test_delta_says_changes_only(self):
        self.assertEqual(progressText('downloading', '1.4.11', 3 * MB, 6 * MB, 'delta'),
                         'Downloading 1.4.11 - 50% (3.0 of 6.0 MB) (changes only)')

    def test_without_size_only_the_bytes_so_far(self):
        self.assertEqual(progressText('downloading', '1.4.11', 5 * MB, 0),
                         'Downloading 1.4.11 - 5.0 MB')

    def test_paused_says_how_far_it_got_when_known(self):
        self.assertEqual(progressText('waiting', '1.4.11', 75 * MB, 150 * MB),
                         "Update 1.4.11 paused while you're in a game (50%); "
                         "it resumes afterwards")
        self.assertEqual(progressText('waiting', '1.4.11'),
                         "Update 1.4.11 paused while you're in a game; it resumes afterwards")

    def test_end_states(self):
        self.assertEqual(progressText('verifying', '1.4.11'), 'Verifying 1.4.11...')
        self.assertEqual(progressText('extracting', '1.4.11'), 'Unpacking 1.4.11...')
        self.assertEqual(progressText('ready', '1.4.11'),
                         'Update 1.4.11 is ready; restart to install it')
        self.assertEqual(progressText('failed', '1.4.11'),
                         'Update 1.4.11 failed. Press Check now to try again.')

    def test_failures_are_plain_english_with_a_next_step(self):
        retry = 'Press Check now to try again.'
        expected = {
            'network': ("Couldn't reach GitHub", retry),
            'server': ('GitHub returned an error', retry),
            'damaged': ('The download was damaged', retry),
            'disk': ("Couldn't save the update files",
                     'Free up disk space, then press Check now.'),
            'busy': ('Another update is already in progress', 'Try again after it finishes.'),
            'other': ('Something went wrong', retry),
        }
        self.assertEqual(set(expected), set(REASONS))
        for code, (words, nextStep) in expected.items():
            with self.subTest(code=code):
                self.assertEqual(progressText('failed', '1.4.11', reason=code),
                                 f'Update 1.4.11 failed: {words}. {nextStep}')
        self.assertEqual(reasonText('no such code'), 'Something went wrong')

    def test_every_state_has_a_text(self):
        for state in STATES:
            with self.subTest(state=state):
                self.assertTrue(progressText(state, '1.4.11', 1, 2))

    def test_payload(self):
        self.assertEqual(progressPayload('downloading', '1.4.11', 1, 2, 'full', None, 'dialog'),
                         {'state': 'downloading', 'version': '1.4.11', 'cur': 1, 'total': 2,
                          'kind': 'full', 'reason': None, 'source': 'dialog'})


class ThrottleTest(unittest.TestCase):

    def test_a_thousand_callbacks_in_one_second_give_at_most_five_updates(self):
        now = [0.0]
        throttle = Throttle(clock=lambda: now[0])
        total = 1000
        passed = []
        for i in range(1, total + 1):
            now[0] = (i - 1) / total  # 0.000 .. 0.999 s
            if throttle.allow('downloading', '1.4.11', i, total):
                passed.append(i)
        self.assertLessEqual(len(passed), 5)
        self.assertEqual(passed[0], 1)       # the first one
        self.assertEqual(passed[-1], total)  # and the final one
        self.assertLessEqual(len(passed[:-1]), 4)  # <= 4 regular updates per second

    def test_a_new_state_or_version_always_passes(self):
        now = [0.0]
        throttle = Throttle(clock=lambda: now[0])
        self.assertTrue(throttle.allow('downloading', '1.4.11', 1, 10))
        self.assertFalse(throttle.allow('downloading', '1.4.11', 2, 10))
        self.assertTrue(throttle.allow('verifying', '1.4.11'))
        self.assertTrue(throttle.allow('extracting', '1.4.11'))
        self.assertTrue(throttle.allow('extracting', '1.4.12'))
        self.assertFalse(throttle.allow('extracting', '1.4.12'))
        now[0] = 0.25
        self.assertTrue(throttle.allow('extracting', '1.4.12'))


class FailureReasonTest(unittest.TestCase):

    def test_reason_codes(self):
        import aiohttp
        cases = [
            (us.StageError('HTTP 500'), 'server'),
            (us.StageError('verification failed: sha256 mismatch (expected a, got b)'), 'damaged'),
            (us.StageError('incomplete download (1 of 2 bytes)'), 'damaged'),
            (us.StageError('download is larger than the release asset'), 'damaged'),
            (us.StageError('package has no filelist.txt'), 'damaged'),
            (us.StageError('an update is being applied'), 'busy'),
            (us.StageError('could not remove an earlier stage: package'), 'disk'),
            (us.StageError('no install asset for this platform'), 'other'),
            (aiohttp.ClientPayloadError('cut'), 'network'),
            (asyncio.TimeoutError(), 'network'),
            (PermissionError('https://secret.invalid/x?token=1'), 'disk'),
            (ValueError('x'), 'other'),
        ]
        for error, code in cases:
            with self.subTest(error=type(error).__name__, code=code):
                self.assertEqual(us.failureReason(error), code)
                self.assertIn(code, REASONS)


class BackgroundProgressTest(TempDirMixin, unittest.IsolatedAsyncioTestCase):
    """BackgroundUpdater + the real stageRelease on a fake session."""

    def make(self, sess, resumable=True):
        self.events, self.ready = [], []
        self.sess = sess

        async def stage(info, base, onProgress=None, resumable=False):
            return await us.stageRelease(info, base, onProgress, sess, resumable=resumable)

        bu = us.BackgroundUpdater(
            self.ready.append, base=self.base, stage=stage,
            onProgress=lambda *a: self.events.append(a), resumable=resumable)
        bu.setGameStatus(None)  # no client: known not in a game
        return bu

    def states(self):
        out = []
        for event in self.events:
            if not out or out[-1] != event[0]:
                out.append(event[0])
        return out

    async def settle(self, n=200):
        for _ in range(n):
            await asyncio.sleep(0)

    async def waitIdle(self, bu):
        for _ in range(500):
            if not bu.isBusy() and bu.task is None:
                return
            await asyncio.sleep(0.01)
        self.fail('the background updater did not finish')

    async def test_states_through_to_ready_with_phase_timings(self):
        bu = self.make(RangeSession(self.data))
        with self.assertLogs('Seraphine', level='INFO') as logs:
            bu.offer(self.info)
            await self.settle()
            await self.waitIdle(bu)
        expected = ['downloading', 'verifying'] + \
            (['extracting'] if sys.platform == 'win32' else []) + ['ready']
        self.assertEqual(self.states(), expected)
        self.assertEqual(self.events[-1], ('ready', '9.9.9', 0, 0, 'full'))
        downloads = [e for e in self.events if e[0] == 'downloading']
        self.assertEqual(downloads[-1], ('downloading', '9.9.9', self.n, self.n, 'full'))
        self.assertEqual(len(self.ready), 1)
        text = '\n'.join(logs.output)
        self.assertRegex(text, rf'download: {self.n} B in \d+\.\d s \(resumed from 0\)')
        self.assertRegex(text, r'verify: \d+\.\d s')
        if sys.platform == 'win32':
            self.assertRegex(text, r'extract: \d+\.\d s')
        self.assertNotIn('https://', text)  # no URLs in the log

    async def test_game_pause_resumes_from_the_partial_file(self):
        bu = self.make(RangeSession(self.data, stallAfter=48))
        verified = []
        real = us.verifyAssetDigest

        def spy(path, asset):
            data = readBytes(path)
            verified.append((len(data), hashlib.sha256(data).hexdigest()))
            return real(path, asset)

        with mock.patch.object(us, 'verifyAssetDigest', side_effect=spy), \
                self.assertLogs('Seraphine', level='INFO') as logs:
            bu.offer(self.info)
            await self.settle()
            self.assertTrue(bu.isBusy())

            bu.setGameStatus('InProgress')  # the game starts mid-download
            await self.settle()
            self.assertFalse(bu.isBusy())
            part = os.path.join(self.base, STAGED_ASSET + '.part')
            self.assertEqual(readBytes(part), self.data[:48])
            self.assertTrue(os.path.isfile(part + '.json'))
            self.assertEqual(self.events[-1], ('waiting', '9.9.9', 48, self.n, 'full'))
            self.assertEqual(self.ready, [])

            bu.setGameStatus('EndOfGame')  # the game ends: resume
            await self.settle()
            await self.waitIdle(bu)

        self.assertEqual(self.sess.ranges, [None, 'bytes=48-'])
        self.assertEqual(verified, [(self.n, hashlib.sha256(self.data).hexdigest())])
        self.assertEqual(self.states(), ['downloading', 'waiting', 'downloading', 'verifying'] +
                         (['extracting'] if sys.platform == 'win32' else []) + ['ready'])
        self.assertEqual(len(self.ready), 1)
        self.assertRegex('\n'.join(logs.output),
                         rf'download: {self.n - 48} B in \d+\.\d s \(resumed from 48\)')

    async def test_game_pause_without_resumable_restarts(self):
        bu = self.make(RangeSession(self.data, stallAfter=48), resumable=False)
        bu.offer(self.info)
        await self.settle()
        bu.setGameStatus('ChampSelect')
        await self.settle()
        self.assertFalse(os.path.exists(os.path.join(self.base, STAGED_ASSET + '.part')))
        bu.setGameStatus('None')
        await self.settle()
        await self.waitIdle(bu)
        self.assertEqual(self.sess.ranges, [None, None])
        self.assertEqual(self.events[-1][0], 'ready')

    async def test_http_500_fails_with_the_type_name_only(self):
        bu = self.make(RangeSession(self.data, mode='error'))
        with self.assertLogs('Seraphine', level='INFO') as logs:
            bu.offer(self.info)
            await self.settle()
            await self.waitIdle(bu)
        self.assertEqual(self.events[-1], ('failed', '9.9.9', 0, 0, 'full'))
        self.assertEqual(bu.lastError, 'server')
        self.assertIsNone(bu.pendingInfo)
        text = '\n'.join(logs.output)
        self.assertIn('background update failed: StageError (server)', text)
        self.assertEqual(len(logs.output), 1)  # one line for one failure
        self.assertNotIn("StageError('", text)  # never the repr
        self.assertEqual(self.ready, [])

    async def test_offer_during_a_game_is_waiting(self):
        bu = self.make(RangeSession(self.data))
        bu.setGameStatus('GameStart')
        bu.offer(self.info)
        await self.settle()
        self.assertEqual(self.events, [('waiting', '9.9.9', 0, 0, 'full')])
        self.assertEqual(self.sess.requests, [])

    async def test_no_waiting_for_a_release_that_is_already_staged_or_held(self):
        bu = self.make(RangeSession(self.data))
        bu.setGameStatus('InProgress')
        bu.notifyReady({'version': '9.9.9'})  # held until the game ends
        self.events.clear()
        bu.offer(self.info)
        with mock.patch.object(us, 'stagedFor', return_value={'version': '9.9.10'}):
            bu.offer(assetInfo(self.data, version='9.9.10'))
        self.assertEqual(self.events, [])
        bu.offer(assetInfo(self.data, version='9.9.11'))  # neither: it waits
        self.assertEqual(self.events, [('waiting', '9.9.11', 0, 0, 'full')])

    async def test_a_held_stage_is_waiting_then_ready(self):
        # a game, or a client whose game state is not known yet, holds the stage: the
        # card says it waits (instead of staying at "Unpacking...")
        for hold in ('InProgress', 'unknown'):
            with self.subTest(hold=hold):
                bu = self.make(RangeSession(self.data))
                if hold == 'unknown':
                    bu.setGameStatusUnknown()
                else:
                    bu.setGameStatus(hold)
                bu.notifyReady({'version': '9.9.9'})
                self.assertEqual(self.ready, [])
                self.assertEqual(self.events, [('waiting', '9.9.9', 0, 0, 'full')])
                bu.setGameStatus('EndOfGame')
                self.assertEqual(self.events[-1], ('ready', '9.9.9', 0, 0, 'full'))
                self.assertEqual(self.ready, [{'version': '9.9.9'}])

    async def test_a_held_blocked_repair_reports_nothing(self):
        bu = self.make(RangeSession(self.data))
        bu.setGameStatusUnknown()
        bu.notifyReady({'version': '9.9.9', 'lastResult': {
            'status': 'restore-failed', 'code': 8, 'version': '9.9.9'}})
        self.assertEqual(self.events, [])

    async def test_a_blocked_repair_is_not_reported_ready(self):
        bu = self.make(RangeSession(self.data))
        bu.notifyReady({'version': '9.9.9', 'lastResult': {
            'status': 'restore-failed', 'code': 8, 'version': '9.9.9'}})
        bu.notifyReady({'lastResult': {'status': 'restore-failed', 'code': 8,
                                       'version': '9.9.9'}})
        self.assertEqual(len(self.ready), 2)
        self.assertEqual(self.events, [])

    async def test_without_progress_or_resume_the_stage_is_called_as_before(self):
        calls = []

        async def stage(info, base):  # the old two-argument fake
            calls.append((info, base))
            return {'version': '9.9.9'}

        ready = []
        bu = us.BackgroundUpdater(ready.append, base='b', stage=stage)
        bu.setGameStatus(None)
        bu.offer({'tag_name': 'v9.9.9'})
        await self.settle(20)
        self.assertEqual(calls, [({'tag_name': 'v9.9.9'}, 'b')])
        self.assertEqual(ready, [{'version': '9.9.9'}])


if __name__ == '__main__':
    unittest.main()
