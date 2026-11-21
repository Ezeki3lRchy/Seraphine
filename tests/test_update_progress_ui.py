"""Update progress in the UI, offscreen: signalBus.updateProgressChanged updates the
"Check for updates now" card (subtitle, a thin bar, the button); a background download
shows one closable info bar whose closing does not cancel the download and whose title
follows the state; a failure replaces it with a timed warning; the update dialog's
progress reaches the card too; the longest progress text fits on the card.
No window is shown on screen, no client or network is needed.
"""
import asyncio
import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication, QPushButton, QWidget

from app.common import update_stage as us
from app.common.signals import signalBus
from app.common.update_progress import (MB, REASONS, Throttle, progressPayload,
                                        progressText)
from app.common.qfluentwidgets import InfoBar
from app.view.setting_interface import SettingInterface


def emit(state, version='1.4.11', cur=0, total=0, kind='full', reason=None, source='background'):
    signalBus.updateProgressChanged.emit(
        progressPayload(state, version, cur, total, kind, reason, source))


# the offscreen platform has no system fonts on Windows: without the real UI font its
# fallback measures text about twice as wide as the app does (as test_setting_buttons
# registers msyh for Chinese); macOS offscreen uses the system fonts
UI_FONT = r"C:\Windows\Fonts\segoeui.ttf"


class UiMixin:

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.fontId = QFontDatabase.addApplicationFont(UI_FONT) if os.path.exists(UI_FONT) else -1

    @classmethod
    def tearDownClass(cls):
        # registration is process-wide: do not leave the font to later test modules
        if cls.fontId >= 0:
            QFontDatabase.removeApplicationFont(cls.fontId)

    def buildPage(self):
        page = SettingInterface()
        page.resize(1000, 800)
        self.assertEqual(self.app.platformName(), "offscreen")
        page.show()
        self.app.processEvents()
        self.addCleanup(page.deleteLater)
        self.addCleanup(page.close)
        for sig, slot in ((signalBus.updateProgressChanged,
                           page._SettingInterface__onUpdateProgressChanged),
                          (signalBus.checkUpdateFinished,
                           page._SettingInterface__onCheckUpdateFinished)):
            self.addCleanup(sig.disconnect, slot)
        return page


class CardTest(UiMixin, unittest.TestCase):

    def setUp(self):
        self.page = self.buildPage()
        self.card = self.page.manualCheckUpdateCard
        self.bar = self.page.updateProgressBar
        self.busy = self.page.updateBusyBar
        self.box = self.page.updateBarBox
        self.button = self.card.button

    def text(self):
        return self.card.contentLabel.text()

    def test_idle_card_is_unchanged(self):
        self.assertTrue(self.text().startswith('Current version: '))
        self.assertFalse(self.box.isVisible())
        self.assertEqual(self.button.text(), 'Check now')
        self.assertTrue(self.button.isEnabled())

    def test_downloading_sets_subtitle_bar_and_disables_the_button(self):
        emit('downloading', cur=int(63.4 * MB), total=150 * MB)
        self.app.processEvents()
        self.assertEqual(self.text(), 'Downloading 1.4.11 - 42% (63 of 150 MB)')
        self.assertTrue(self.bar.isVisible())
        self.assertFalse(self.busy.isVisible())
        self.assertEqual(self.bar.value(), 422)
        self.assertFalse(self.bar.isPaused())
        self.assertFalse(self.button.isEnabled())
        # a manual check that ends meanwhile does not re-enable it
        signalBus.checkUpdateFinished.emit()
        self.assertFalse(self.button.isEnabled())

    def test_bar_spans_the_subtitle_and_takes_no_space_when_hidden(self):
        idleTitle = self.card.titleLabel.geometry()
        emit('downloading', cur=50 * MB, total=150 * MB)
        self.app.processEvents()
        self.assertEqual(self.bar.width(), self.box.width())
        self.assertGreaterEqual(self.box.width(), self.card.contentLabel.width())
        self.assertLessEqual(self.box.width(), max(self.card.contentLabel.width(),
                                                   self.card.titleLabel.width()))
        emit('ready')
        self.app.processEvents()
        self.assertFalse(self.box.isVisible())
        self.assertEqual(self.card.titleLabel.geometry().top(), idleTitle.top())

    def test_verifying_and_unpacking_show_a_moving_bar(self):
        emit('downloading', cur=150 * MB, total=150 * MB)
        emit('verifying')
        self.assertEqual(self.text(), 'Verifying 1.4.11...')
        self.assertTrue(self.busy.isVisible())
        self.assertTrue(self.busy.isStarted())
        self.assertFalse(self.bar.isVisible())
        emit('extracting')
        self.assertEqual(self.text(), 'Unpacking 1.4.11...')
        self.assertTrue(self.busy.isStarted())
        self.assertFalse(self.button.isEnabled())

    def test_ready_turns_the_button_into_restart(self):
        emit('extracting')
        emit('ready')
        self.app.processEvents()
        self.assertEqual(self.text(), 'Update 1.4.11 is ready; restart to install it')
        self.assertFalse(self.box.isVisible())
        self.assertFalse(self.busy.isStarted())
        self.assertEqual(self.button.text(), 'Restart to update')
        self.assertTrue(self.button.isEnabled())
        self.assertGreaterEqual(self.button.width(), self.button.sizeHint().width())

        requests, checks = [], []
        signalBus.restartToUpdateRequested.connect(lambda: requests.append(1))
        signalBus.checkUpdateRequested.connect(lambda: checks.append(1))
        self.addCleanup(signalBus.restartToUpdateRequested.disconnect)
        self.addCleanup(signalBus.checkUpdateRequested.disconnect)
        self.button.click()
        self.assertEqual((requests, checks), ([1], []))

        # "no longer available" (MainWindow) or a check that finds nothing newer
        emit('idle', version='1.4.10')
        self.assertTrue(self.text().startswith('Current version: '))
        self.assertEqual(self.button.text(), 'Check now')
        self.button.click()
        self.assertEqual((requests, checks), ([1], [1]))

    def test_waiting_pauses_the_bar_and_keeps_the_button(self):
        emit('downloading', cur=50 * MB, total=150 * MB)
        emit('waiting', cur=50 * MB, total=150 * MB)
        self.assertEqual(self.text(),
                         "Update 1.4.11 paused while you're in a game (33%); it resumes afterwards")
        self.assertTrue(self.bar.isVisible())
        self.assertTrue(self.bar.isPaused())
        self.assertEqual(self.bar.value(), 333)
        self.assertTrue(self.button.isEnabled())
        emit('idle', version='1.4.10')  # a check finding nothing newer: still paused
        self.assertTrue(self.text().startswith('Update 1.4.11 paused'))
        emit('downloading', cur=60 * MB, total=150 * MB)
        self.assertFalse(self.bar.isPaused())

    def test_waiting_before_any_download_shows_no_bar(self):
        emit('waiting')
        self.assertFalse(self.box.isVisible())

    def test_failed_stays_until_the_next_check_has_run(self):
        emit('downloading', cur=50 * MB, total=150 * MB)
        emit('failed', reason='server')
        self.assertEqual(self.text(), 'Update 1.4.11 failed: GitHub returned an error. '
                                      'Press Check now to try again.')
        self.assertFalse(self.box.isVisible())
        self.assertTrue(self.button.isEnabled())
        self.button.click()
        self.assertFalse(self.button.isEnabled())
        signalBus.checkUpdateFinished.emit()
        self.assertTrue(self.text().startswith('Current version: '))
        self.assertTrue(self.button.isEnabled())

    def test_the_longest_progress_text_fits(self):
        texts = [progressText('downloading', '10.10.10', 1000 * MB, 1000 * MB, 'delta'),
                 progressText('waiting', '10.10.10', 999 * MB, 1000 * MB),
                 progressText('ready', '10.10.10')]
        texts += [progressText('failed', '10.10.10', reason=r) for r in REASONS]
        label = self.card.contentLabel
        for text in texts:
            with self.subTest(text=text):
                self.card.setContent(text)
                self.box.setVisible(True)
                self.bar.setVisible(True)
                self.app.processEvents()
                self.assertEqual(label.text(), text)
                self.assertGreaterEqual(label.width(), label.sizeHint().width())
                self.assertLess(label.geometry().right(), self.button.geometry().left())
                self.assertLessEqual(self.box.geometry().bottom(), self.card.height())
                for b in self.page.findChildren(QPushButton):
                    self.assertGreaterEqual(b.width(), b.sizeHint().width())


class InfoBarHost(QWidget):
    """The update-progress slots of MainWindow on a plain widget (no client, no threads)."""

    def __init__(self):
        super().__init__()
        from app.view.main_window import MainWindow as M
        for name in ('__onUpdateStaging', '__onBackgroundProgress', '__showUpdateProgressBar',
                     '__onUpdateProgressBarClosed', '__closeUpdateProgressBar',
                     '__onUpdateProgressChanged', '__closeUpdateReadyBar',
                     '__onRestartToUpdateRequested'):
            mangled = '_MainWindow' + name
            setattr(self, mangled, getattr(M, mangled).__get__(self))
        self._MainWindow__showUpdateError = mock.Mock()
        self._MainWindow__restartToUpdate = mock.Mock()
        self._updateProgressThrottle = Throttle()
        self._updateProgressBar = None
        self._updateProgressDismissed = None
        self._updateReadyBar = None
        self._updateReadyMarker = None
        self._updateReadyButton = None
        self._lastReadyMarker = None
        self.resize(1000, 700)

    def warnings(self):
        return [b for b in self.findChildren(InfoBar)
                if b.title == 'Update failed' and b.isVisible()]


class InfoBarTest(UiMixin, unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        from app.view import main_window as mw
        self.mw = mw
        patcher = mock.patch.object(mw, 'stagedFor', return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.host = InfoBarHost()
        self.host.show()
        self.addCleanup(self.host.deleteLater)
        self.addCleanup(self.host.close)
        signalBus.updateProgressChanged.connect(self.host._MainWindow__onUpdateProgressChanged)
        self.addCleanup(signalBus.updateProgressChanged.disconnect,
                        self.host._MainWindow__onUpdateProgressChanged)
        self.page = self.buildPage()

        self.gate = asyncio.Event()
        self.ready = []
        self.fail = None

        async def stage(info, base, onProgress=None, resumable=False):
            onProgress('download', 10 * MB, 150 * MB)
            await self.gate.wait()
            if self.fail is not None:
                raise self.fail
            onProgress('download', 150 * MB, 150 * MB)
            onProgress('verify', 0, 0)
            return {'version': '1.4.11'}

        self.bu = us.BackgroundUpdater(
            self.ready.append, base='unused', stage=stage,
            onStarting=self.host._MainWindow__onUpdateStaging,
            onProgress=self.host._MainWindow__onBackgroundProgress, resumable=True)
        self.host.backgroundUpdater = self.bu
        self.bu.setGameStatus(None)

    async def settle(self):
        for _ in range(20):
            await asyncio.sleep(0)
        self.app.processEvents()

    def cardText(self):
        return self.page.manualCheckUpdateCard.contentLabel.text()

    async def test_background_download_shows_a_bar_that_closes_without_cancelling(self):
        self.bu.offer({'tag_name': 'v1.4.11'})
        await self.settle()
        bar = self.host._updateProgressBar
        self.assertIsNotNone(bar)
        self.assertTrue(bar.isVisible())
        self.assertEqual(bar.title, 'Downloading update')
        self.assertEqual(bar.content, 'Downloading 1.4.11 - 6% (10 of 150 MB)')
        self.assertEqual(self.cardText(), 'Downloading 1.4.11 - 6% (10 of 150 MB)')

        bar.close()  # the user closes it
        self.assertIsNone(self.host._updateProgressBar)
        self.assertTrue(self.bu.isBusy())  # the download goes on

        # resumed after a game: not shown again for this version
        self.host._MainWindow__onUpdateStaging({'tag_name': 'v1.4.11'})
        self.assertIsNone(self.host._updateProgressBar)
        # ... but for another release it is
        self.host._MainWindow__onUpdateStaging({'tag_name': 'v1.4.12'})
        self.assertIsNotNone(self.host._updateProgressBar)

        self.gate.set()
        await self.settle()
        self.assertFalse(self.bu.isBusy())
        self.assertEqual(self.ready, [{'version': '1.4.11'}])
        # ready: the progress bar gives way to the ready bar, the card says so
        self.assertIsNone(self.host._updateProgressBar)
        self.assertEqual(self.cardText(), 'Update 1.4.11 is ready; restart to install it')
        self.assertEqual(self.host.warnings(), [])

    async def test_title_follows_the_state(self):
        self.bu.offer({'tag_name': 'v1.4.11'})
        await self.settle()
        bar = self.host._updateProgressBar
        self.bu.setGameStatus('InProgress')
        await self.settle()
        self.assertIs(self.host._updateProgressBar, bar)
        self.assertEqual(bar.title, 'Update paused')
        self.assertEqual(bar.content, "Update 1.4.11 paused while you're in a game (6%); "
                                      "it resumes afterwards")

    async def test_a_reused_bar_dismisses_the_version_it_shows(self):
        self.host._MainWindow__showUpdateProgressBar('1.4.11', 'a')
        bar = self.host._updateProgressBar
        self.host._MainWindow__showUpdateProgressBar('1.4.12', 'b')
        self.assertIs(self.host._updateProgressBar, bar)
        bar.close()
        self.assertEqual(self.host._updateProgressDismissed, '1.4.12')

    async def test_failure_closes_the_bar_and_warns_with_the_reason(self):
        self.fail = us.StageError('HTTP 500')
        self.bu.offer({'tag_name': 'v1.4.11'})
        await self.settle()
        self.assertIsNotNone(self.host._updateProgressBar)
        self.gate.set()
        await self.settle()
        self.assertIsNone(self.host._updateProgressBar)
        warnings = self.host.warnings()
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0].content, 'Update 1.4.11 failed: GitHub returned an error. '
                                              'Press Check now to try again.')
        self.assertGreater(warnings[0].duration, 0)  # timed
        self.assertTrue(self.page.manualCheckUpdateCard.button.isEnabled())

    async def test_a_staged_release_shows_no_progress_bar(self):
        self.mw.stagedFor.return_value = {'version': '1.4.11'}
        self.host._MainWindow__onUpdateStaging({'tag_name': 'v1.4.11'})
        self.assertIsNone(self.host._updateProgressBar)

    async def test_dialog_progress_reaches_the_card_but_not_the_bar(self):
        from app.components.message_box import UpdateMessageBox
        info = {'tag_name': 'v1.4.11', 'forbidden': False, 'body': '', 'assets': []}
        box = UpdateMessageBox(info, self.host)
        self.addCleanup(box.deleteLater)
        box._UpdateMessageBox__onStageProgress('download', 30 * MB, 150 * MB)
        self.assertEqual(self.cardText(), 'Downloading 1.4.11 - 20% (30 of 150 MB)')
        self.assertEqual(self.page.updateProgressBar.value(), 200)
        self.assertEqual(box.bar.value(), 30 * MB)  # the dialog keeps its own bar
        self.assertIsNone(self.host._updateProgressBar)
        box._UpdateMessageBox__onStageProgress('verify', 0, 0)
        self.assertEqual(self.cardText(), 'Verifying 1.4.11...')
        box._UpdateMessageBox__onUpdateAborted('Update download failed: HTTP 500')
        self.assertEqual(self.cardText(), 'Update 1.4.11 failed. Press Check now to try again.')
        self.assertEqual(self.host.warnings(), [])  # the dialog says it itself

    async def test_manual_check_shows_the_bar_even_after_it_was_closed(self):
        info = {'tag_name': 'v1.4.11', 'forbidden': False, 'assets': []}
        self.host._updateProgressDismissed = '1.4.11'
        self.host.manualCheckBusy = True
        self.bu.setGameStatus('InProgress')
        self.bu.offer = mock.Mock()
        self.host._MainWindow__backgroundUpdateEligible = lambda info: True
        with mock.patch.object(us, 'failedRepair', return_value=None):
            self.mw.MainWindow._MainWindow__onUpdateAvailable(self.host, info)
        bar = self.host._updateProgressBar
        self.assertIsNotNone(bar)
        self.assertEqual(bar.title, 'Update paused')
        self.assertEqual(bar.content,
                         "Update 1.4.11 paused while you're in a game; it resumes afterwards")
        self.bu.offer.assert_called_once_with(info)

    async def test_card_restart_uses_the_ready_marker_even_after_later(self):
        marker = {'version': '1.4.11'}
        self.host._lastReadyMarker = marker  # the ready bar was closed with "Later"
        with mock.patch.object(self.mw, 'markerApplicable', return_value=True):
            self.host._MainWindow__onRestartToUpdateRequested()
        self.host._MainWindow__restartToUpdate.assert_called_once_with(marker)

    def infoTexts(self):
        return [b.content for b in self.host.findChildren(InfoBar) if b.title == 'Update ready']

    async def test_card_restart_waits_for_the_game_to_end(self):
        self.host._lastReadyMarker = {'version': '1.4.11'}
        self.bu.setGameStatus('InProgress')
        with mock.patch.object(self.mw, 'markerApplicable', return_value=True):
            self.host._MainWindow__onRestartToUpdateRequested()
        self.host._MainWindow__restartToUpdate.assert_not_called()
        self.assertEqual(self.infoTexts(), ['Restart after the game to finish updating.'])

    async def test_card_restart_waits_for_the_client_state(self):
        self.host._lastReadyMarker = {'version': '1.4.11'}
        self.bu.setGameStatusUnknown()
        with mock.patch.object(self.mw, 'markerApplicable', return_value=True):
            self.host._MainWindow__onRestartToUpdateRequested()
        self.host._MainWindow__restartToUpdate.assert_not_called()
        self.assertEqual(self.infoTexts(),
                         ['Restart once the client is ready to finish updating.'])

    async def test_card_restart_never_restarts_into_a_failed_repair(self):
        emit('ready')
        blocked = {'version': '1.4.11', 'lastResult': {
            'status': 'restore-failed', 'code': 8, 'version': '1.4.11'}}
        self.host._lastReadyMarker = blocked
        self.host._MainWindow__onUpdateReady = mock.Mock()
        with mock.patch.object(self.mw, 'markerApplicable', return_value=True):
            self.host._MainWindow__onRestartToUpdateRequested()
        self.host._MainWindow__restartToUpdate.assert_not_called()
        self.host._MainWindow__onUpdateReady.assert_called_once_with(blocked)  # manual download
        self.assertIsNone(self.host._lastReadyMarker)
        self.assertTrue(self.cardText().startswith('Current version: '))
        self.assertEqual(self.page.manualCheckUpdateCard.button.text(), 'Check now')

    async def test_card_restart_ignores_a_blocked_ready_bar_marker(self):
        # only the last marker offered as ready counts, never the repair bar's
        self.host._updateReadyMarker = {'version': '1.4.11', 'lastResult': {
            'status': 'restore-failed', 'code': 8, 'version': '1.4.11'}}
        self.host._lastReadyMarker = None
        with mock.patch.object(self.mw, 'markerApplicable', return_value=True):
            self.host._MainWindow__onRestartToUpdateRequested()
        self.host._MainWindow__restartToUpdate.assert_not_called()
        self.host._MainWindow__showUpdateError.assert_called_once()

    async def test_dialog_whose_updater_raises_reports_failed_to_the_card(self):
        from app.components import message_box as mb
        info = {'tag_name': 'v1.4.11', 'forbidden': False, 'body': '',
                'assets': [{'name': 'Seraphine.dmg' if mb.platform.IS_MACOS else 'Seraphine.7z',
                            'browser_download_url': 'https://example.invalid/x',
                            'digest': 'sha256:' + '0' * 64}]}
        box = mb.UpdateMessageBox(info, self.host)
        self.addCleanup(box.deleteLater)
        emit('extracting', source='dialog')
        with mock.patch.object(mb, 'stageRelease', mock.AsyncMock(return_value={'v': 1})), \
                mock.patch.object(mb, 'applyStagedUpdate',
                                  mock.AsyncMock(side_effect=OSError('locked'))), \
                mock.patch.object(mb.sys, 'exit') as exit_, \
                self.assertLogs('Seraphine', level='WARNING') as logs:
            await box._UpdateMessageBox__onYesButtonClicked()
        exit_.assert_not_called()
        self.assertEqual(box.infoLabel.text(), 'Could not start the updater.')
        self.assertTrue(box.myYesButton.isEnabled())
        self.assertEqual(self.cardText(), 'Update 1.4.11 failed. Press Check now to try again.')
        self.assertTrue(self.page.manualCheckUpdateCard.button.isEnabled())
        self.assertIn('starting the updater from the dialog failed: OSError', logs.output[0])
        self.assertNotIn('locked', '\n'.join(logs.output))

    async def test_card_restart_of_a_stale_update_resets_the_card(self):
        emit('ready')
        self.host._lastReadyMarker = {'version': '1.4.11'}
        with mock.patch.object(self.mw, 'markerApplicable', return_value=False):
            self.host._MainWindow__onRestartToUpdateRequested()
        self.host._MainWindow__restartToUpdate.assert_not_called()
        self.host._MainWindow__showUpdateError.assert_called_once()
        self.assertIsNone(self.host._lastReadyMarker)
        self.assertTrue(self.cardText().startswith('Current version: '))
        self.assertEqual(self.page.manualCheckUpdateCard.button.text(), 'Check now')


if __name__ == '__main__':
    unittest.main()
