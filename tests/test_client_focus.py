"""app/common/client_focus.py: the client is minimized again when a ready check brings
it to the front, and only then.

ReadyCheckFocus runs with a fake clock, a fake foreground reading and recorded
minimize / restore calls: no window is read or changed. The platform helpers run
against a stand-in win32gui. The card row is built offscreen.
"""
import asyncio
import os
import sys
import types
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.common import client_focus as cf
from app.common import platform
from app.common.config import cfg


class Rig:
    def __init__(self, enabled=True, foreground=False, minimizeOk=True):
        self.t = 100.0
        self.enabled = enabled
        self.foreground = foreground
        self.minimizeOk = minimizeOk
        self.minimized = 0
        self.restored = 0
        self.logic = cf.ReadyCheckFocus(
            lambda: self.enabled, lambda: self.foreground,
            self.minimize, self.restore, now=lambda: self.t)

    def minimize(self):
        self.minimized += 1
        return self.minimizeOk

    def restore(self):
        self.restored += 1

    def queue(self, seconds=5.0, step=0.25):
        """In queue for a while, sampled as the timer would"""
        self.logic.onPhase('Matchmaking')
        end = self.t + seconds
        while self.t < end:
            self.logic.sample()
            self.t += step

    def tick(self, seconds):
        self.t += seconds
        self.logic.sample()


class ReadyCheckFocusTest(unittest.TestCase):

    def test_client_coming_to_the_front_after_the_event_is_minimized(self):
        rig = Rig(foreground=False)
        rig.queue()
        rig.logic.onPhase('ReadyCheck')
        self.assertTrue(rig.logic.armed)
        self.assertEqual(rig.minimized, 0)

        rig.foreground = True
        rig.tick(0.06)
        self.assertEqual(rig.minimized, 1)
        self.assertTrue(rig.logic.minimized)
        self.assertFalse(rig.logic.armed)

    def test_client_that_came_to_the_front_just_before_the_event_is_minimized(self):
        # the window can be in front before the ReadyCheck event reaches Seraphine
        rig = Rig(foreground=False)
        rig.queue()
        rig.foreground = True
        rig.t += 0.1
        rig.logic.onPhase('ReadyCheck')
        self.assertEqual(rig.minimized, 1)

    def test_client_the_user_was_looking_at_is_left_alone(self):
        rig = Rig(foreground=True)
        rig.queue()
        rig.logic.onPhase('ReadyCheck')
        self.assertEqual(rig.minimized, 0)
        self.assertFalse(rig.logic.armed)
        rig.tick(0.5)
        self.assertEqual(rig.minimized, 0)

    def test_user_who_switched_to_the_client_long_before_is_left_alone(self):
        rig = Rig(foreground=False)
        rig.queue(2.0)
        rig.foreground = True
        rig.queue(3.0)  # in front for 3 s: not the ready check's doing
        rig.logic.onPhase('ReadyCheck')
        self.assertEqual(rig.minimized, 0)

    def test_client_in_front_without_any_earlier_sample_is_left_alone(self):
        # Seraphine started mid-queue: it is not known for how long the client was in front
        rig = Rig(foreground=True)
        rig.logic.onPhase('ReadyCheck')
        self.assertEqual(rig.minimized, 0)
        self.assertFalse(rig.logic.armed)

    def test_no_earlier_sample_and_client_in_the_background_arms(self):
        rig = Rig(foreground=False)
        rig.logic.onPhase('ReadyCheck')
        self.assertTrue(rig.logic.armed)
        rig.foreground = True
        rig.tick(0.03)
        self.assertEqual(rig.minimized, 1)

    def test_client_coming_to_the_front_after_the_window_is_the_users_doing(self):
        rig = Rig(foreground=False)
        rig.queue()
        rig.logic.onPhase('ReadyCheck')
        rig.tick(cf.ReadyCheckFocus.WINDOW + 0.1)
        self.assertFalse(rig.logic.armed)
        rig.foreground = True
        rig.tick(0.03)
        self.assertEqual(rig.minimized, 0)

    def test_off_does_nothing(self):
        rig = Rig(enabled=False, foreground=False)
        rig.queue()
        rig.logic.onPhase('ReadyCheck')
        self.assertFalse(rig.logic.armed)
        rig.foreground = True
        rig.tick(0.03)
        self.assertEqual(rig.minimized, 0)
        self.assertEqual(rig.logic.interval(), 0)

    def test_unknown_foreground_does_nothing(self):
        rig = Rig(foreground=None)
        rig.queue()
        rig.logic.onPhase('ReadyCheck')
        self.assertFalse(rig.logic.armed)
        self.assertEqual(rig.minimized, 0)

    def test_one_ready_check_minimizes_once(self):
        rig = Rig(foreground=False)
        rig.queue()
        rig.logic.onPhase('ReadyCheck')
        rig.foreground = True
        rig.tick(0.05)
        # the user opens the client again, and the phase event is repeated
        rig.logic.onPhase('ReadyCheck')
        rig.tick(0.05)
        self.assertEqual(rig.minimized, 1)

    def test_champ_select_shows_a_minimized_client_again(self):
        rig = Rig(foreground=False)
        rig.queue()
        rig.logic.onPhase('ReadyCheck')
        rig.foreground = True
        rig.tick(0.05)
        rig.logic.onPhase('ChampSelect')
        self.assertEqual(rig.restored, 1)
        self.assertFalse(rig.logic.minimized)
        # only once
        rig.logic.onPhase('ChampSelect')
        self.assertEqual(rig.restored, 1)

    def test_champ_select_does_not_show_a_client_it_did_not_minimize(self):
        rig = Rig(foreground=True)
        rig.queue()
        rig.logic.onPhase('ReadyCheck')
        rig.logic.onPhase('ChampSelect')
        self.assertEqual(rig.restored, 0)

    def test_declined_ready_check_keeps_it_minimized_until_champ_select(self):
        rig = Rig(foreground=False)
        rig.queue()
        rig.logic.onPhase('ReadyCheck')
        rig.foreground = True
        rig.tick(0.05)
        rig.foreground = False  # minimized
        # someone declined: back in queue, then a second ready check
        rig.queue(3.0)
        self.assertEqual(rig.restored, 0)
        rig.logic.onPhase('ReadyCheck')
        rig.foreground = True
        rig.tick(0.05)
        self.assertEqual(rig.minimized, 2)
        rig.logic.onPhase('ChampSelect')
        self.assertEqual(rig.restored, 1)

    def test_leaving_the_queue_does_not_show_the_client(self):
        for phase in ('Lobby', 'None'):
            with self.subTest(phase=phase):
                rig = Rig(foreground=False)
                rig.queue()
                rig.logic.onPhase('ReadyCheck')
                rig.foreground = True
                rig.tick(0.05)
                rig.logic.onPhase(phase)
                self.assertEqual(rig.restored, 0)
                self.assertFalse(rig.logic.minimized)
                rig.logic.onPhase('ChampSelect')
                self.assertEqual(rig.restored, 0)

    def test_failed_accept_shows_the_client_again(self):
        rig = Rig(foreground=False)
        rig.queue()
        rig.logic.onPhase('ReadyCheck')
        rig.foreground = True
        rig.tick(0.05)
        rig.logic.showAgain()
        self.assertEqual(rig.restored, 1)
        rig.logic.showAgain()
        self.assertEqual(rig.restored, 1)

    def test_minimize_that_did_not_happen_is_not_restored(self):
        rig = Rig(foreground=False, minimizeOk=False)
        rig.queue()
        rig.logic.onPhase('ReadyCheck')
        rig.foreground = True
        rig.tick(0.05)
        self.assertEqual(rig.minimized, 1)
        self.assertFalse(rig.logic.minimized)
        rig.logic.onPhase('ChampSelect')
        self.assertEqual(rig.restored, 0)

    def test_samples_from_an_earlier_queue_are_forgotten(self):
        rig = Rig(foreground=False)
        rig.queue()
        rig.logic.onPhase('Lobby')
        # much later, with the client in front and no sample of this queue
        rig.t += 600
        rig.foreground = True
        rig.logic.onPhase('ReadyCheck')
        self.assertEqual(rig.minimized, 0)

    def test_sampling_interval(self):
        rig = Rig(foreground=False)
        self.assertEqual(rig.logic.interval(), 0)
        rig.logic.onPhase('Matchmaking')
        self.assertEqual(rig.logic.interval(), cf.ClientFocusKeeper.SLOW_MS)
        rig.logic.onPhase('ReadyCheck')
        self.assertEqual(rig.logic.interval(), cf.ClientFocusKeeper.FAST_MS)
        rig.foreground = True
        rig.tick(0.03)
        self.assertEqual(rig.logic.interval(), cf.ClientFocusKeeper.SLOW_MS)
        for phase in ('ChampSelect', 'InProgress', 'Lobby', 'None'):
            rig.logic.onPhase(phase)
            self.assertEqual(rig.logic.interval(), 0, phase)


class EnabledTest(unittest.TestCase):

    def check(self, windows, accept, hide):
        values = {cfg.enableAutoAcceptMatching: accept, cfg.hideClientOnReadyCheck: hide}
        with mock.patch.object(platform, 'IS_WINDOWS', windows), \
                mock.patch.object(cf.cfg, 'get', side_effect=values.get):
            return cf.isEnabled()

    def test_needs_windows_auto_accept_and_the_switch(self):
        self.assertTrue(self.check(True, True, True))
        self.assertFalse(self.check(False, True, True))
        self.assertFalse(self.check(True, False, True))
        self.assertFalse(self.check(True, True, False))

    def test_off_by_default(self):
        self.assertIs(cfg.hideClientOnReadyCheck.defaultValue, False)


class KeeperTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def keeper(self, enabled=True):
        restore = mock.Mock()
        for target, value in ((cf, 'isEnabled'), (platform, 'lol_client_is_foreground'),
                              (platform, 'minimize_lol_client')):
            patcher = mock.patch.object(target, value, return_value={
                'isEnabled': enabled, 'lol_client_is_foreground': False,
                'minimize_lol_client': True}[value])
            patcher.start()
            self.addCleanup(patcher.stop)
        keeper = cf.ClientFocusKeeper(restore)
        self.addCleanup(keeper.deleteLater)
        self.addCleanup(keeper.stop)
        return keeper, restore

    def test_timer_runs_only_in_queue(self):
        keeper, _ = self.keeper()
        self.assertFalse(keeper.timer.isActive())
        keeper.onPhase('Matchmaking')
        self.assertTrue(keeper.timer.isActive())
        self.assertEqual(keeper.timer.interval(), keeper.SLOW_MS)
        keeper.onPhase('ReadyCheck')
        self.assertEqual(keeper.timer.interval(), keeper.FAST_MS)
        keeper.onPhase('ChampSelect')
        self.assertFalse(keeper.timer.isActive())

    def test_timer_never_runs_when_off(self):
        keeper, _ = self.keeper(enabled=False)
        for phase in ('Matchmaking', 'ReadyCheck', 'ChampSelect'):
            keeper.onPhase(phase)
            self.assertFalse(keeper.timer.isActive(), phase)

    def test_minimizes_and_restores_through_the_platform_and_the_callback(self):
        keeper, restore = self.keeper()
        keeper.onPhase('Matchmaking')
        keeper.onPhase('ReadyCheck')
        platform.lol_client_is_foreground.return_value = True
        keeper.logic.sample()
        platform.minimize_lol_client.assert_called_once_with()
        keeper.acceptFailed()
        restore.assert_called_once_with()


class PlatformTest(unittest.TestCase):

    def gui(self, client=7, foreground=7):
        gui = types.SimpleNamespace(
            FindWindow=mock.Mock(return_value=client),
            GetForegroundWindow=mock.Mock(return_value=foreground),
            PostMessage=mock.Mock())
        con = types.SimpleNamespace(WM_SYSCOMMAND=0x0112, SC_MINIMIZE=0xF020)
        for patcher in (mock.patch.dict(sys.modules, {'win32gui': gui, 'win32con': con}),
                        mock.patch.object(platform, 'IS_WINDOWS', True)):
            patcher.start()
            self.addCleanup(patcher.stop)
        return gui

    def test_foreground(self):
        self.gui(client=7, foreground=7)
        self.assertIs(platform.lol_client_is_foreground(), True)

    def test_background(self):
        self.gui(client=7, foreground=9)
        self.assertIs(platform.lol_client_is_foreground(), False)

    def test_no_client_window_is_unknown(self):
        gui = self.gui(client=0)
        self.assertIsNone(platform.lol_client_is_foreground())
        self.assertFalse(platform.minimize_lol_client())
        gui.PostMessage.assert_not_called()

    def test_minimize_posts_the_minimize_command_to_the_client_window(self):
        gui = self.gui(client=7)
        self.assertTrue(platform.minimize_lol_client())
        gui.FindWindow.assert_called_with("RCLIENT", "League of Legends")
        gui.PostMessage.assert_called_once_with(7, 0x0112, 0xF020, 0)

    def test_errors_do_not_escape(self):
        gui = self.gui()
        gui.FindWindow.side_effect = OSError("boom")
        self.assertIsNone(platform.lol_client_is_foreground())
        self.assertFalse(platform.minimize_lol_client())

    def test_other_platforms(self):
        with mock.patch.object(platform, 'IS_WINDOWS', False):
            self.assertIsNone(platform.lol_client_is_foreground())
            self.assertFalse(platform.minimize_lol_client())


class CardRowTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def card(self, hideItem):
        from app.view.auxiliary_interface import AutoAcceptMatchingCard
        card = AutoAcceptMatchingCard(
            "Auto accept", "", cfg.enableAutoAcceptMatching,
            cfg.autoAcceptMatchingDelay, hideConfigItem=hideItem)
        self.addCleanup(card.deleteLater)
        return card

    def test_row_sets_the_config_item(self):
        saved = cfg.hideClientOnReadyCheck.value
        self.addCleanup(setattr, cfg.hideClientOnReadyCheck, 'value', saved)
        cfg.hideClientOnReadyCheck.value = False

        card = self.card(cfg.hideClientOnReadyCheck)
        self.assertFalse(card.hideSwitch.isChecked())
        with mock.patch('app.view.auxiliary_interface.qconfig.set') as setter:
            card.hideSwitch.setChecked(True)
        setter.assert_called_once_with(cfg.hideClientOnReadyCheck, True)

    def test_no_row_without_the_item(self):
        card = self.card(None)
        self.assertIsNone(card.hideSwitch)
        self.assertIsNone(card.hideWidget)

    def test_page_has_the_row_on_windows_only(self):
        import inspect
        from app.view import auxiliary_interface as ai
        source = inspect.getsource(ai.AuxiliaryInterface)
        self.assertIn(
            "hideConfigItem=cfg.hideClientOnReadyCheck if platform.IS_WINDOWS else None",
            source)


class AcceptFailureTest(unittest.IsolatedAsyncioTestCase):
    """MainWindow.__onMatchMade: an accept that fails shows a hidden client again"""

    async def run_accept(self, connector):
        from app.view import main_window as mw
        fake = types.SimpleNamespace(clientFocus=mock.Mock())
        values = {mw.cfg.enableAutoAcceptMatching: True, mw.cfg.autoAcceptMatchingDelay: 0}
        tasks = []
        realCreate = asyncio.create_task

        def create(coro):
            task = realCreate(coro)
            tasks.append(task)
            return task

        with mock.patch.object(mw, 'connector', connector), \
                mock.patch.object(mw.cfg, 'get', side_effect=values.get), \
                mock.patch.object(mw.asyncio, 'create_task', create):
            await mw.MainWindow._MainWindow__onMatchMade(fake)
            results = await asyncio.gather(*tasks, return_exceptions=True)
        return fake, results

    async def test_accepted(self):
        connector = mock.Mock(
            getReadyCheckStatus=mock.AsyncMock(return_value={'playerResponse': 'None'}),
            acceptMatchMaking=mock.AsyncMock())
        fake, _ = await self.run_accept(connector)
        connector.acceptMatchMaking.assert_awaited_once()
        fake.clientFocus.acceptFailed.assert_not_called()

    async def test_accept_request_failed(self):
        connector = mock.Mock(
            getReadyCheckStatus=mock.AsyncMock(return_value={'playerResponse': 'None'}),
            acceptMatchMaking=mock.AsyncMock(side_effect=OSError("down")))
        fake, results = await self.run_accept(connector)
        fake.clientFocus.acceptFailed.assert_called_once_with()
        self.assertIsInstance(results[0], OSError)

    async def test_status_request_failed(self):
        connector = mock.Mock(
            getReadyCheckStatus=mock.AsyncMock(side_effect=OSError("down")),
            acceptMatchMaking=mock.AsyncMock())
        fake, _ = await self.run_accept(connector)
        connector.acceptMatchMaking.assert_not_awaited()
        fake.clientFocus.acceptFailed.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
