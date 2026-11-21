"""Uncaught exceptions: fatal only before start-up finished, afterwards one notice and
log-only for duplicates / concurrent failures (app/common/error_policy.py), and
MainWindow.exceptHook as its thin caller (run on a fake self, no window is built)."""
import asyncio
import gc
import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from aiohttp.client_exceptions import ClientConnectorError

from app.common import error_policy as ep
from app.lol.exceptions import (SummonerGamesNotFound, RetryMaximumAttempts,
                                SummonerNotFound)
from tests import LOG_RECORDS


def excInfo(raiser):
    try:
        raiser()
    except BaseException as e:
        return type(e), e, e.__traceback__
    raise AssertionError("did not raise")


def keyError(key='a'):
    # one code path (same line) for every key: the same traceback
    return excInfo(lambda: {}[key])


def typeError():
    return excInfo(lambda: None + 1)


def raiseIt(exc):
    def raiser():
        raise exc
    return excInfo(raiser)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class ErrorPolicyTest(unittest.TestCase):

    def setUp(self):
        self.clock = Clock()
        self.policy = ep.ErrorPolicy(clock=self.clock)

    def decide(self, info):
        return self.policy.decide(*info)

    def test_fatal_before_startup_finished(self):
        d = self.decide(keyError())
        self.assertEqual(d.action, ep.FATAL)
        self.assertIn("KeyError", d.text)
        self.assertIn("Traceback", d.text)

    def test_notify_once_after_startup(self):
        self.policy.finishStartup()
        d = self.decide(keyError())
        self.assertEqual(d.action, ep.NOTIFY)
        self.assertIn("Traceback", d.text)

    def test_same_traceback_again_is_one_log_line(self):
        self.policy.finishStartup()
        self.assertEqual(self.decide(keyError('a')).action, ep.NOTIFY)
        self.policy.noticeClosed()
        # another key from the same line is the same bug: not shown again
        d = self.decide(keyError('secret-value'))
        self.assertEqual(d.action, ep.BRIEF)
        self.assertIn("KeyError", d.text)
        self.assertNotIn("secret-value", d.text)  # never the message
        self.assertNotIn("Traceback", d.text)

    def test_same_traceback_after_the_window_notifies_again(self):
        self.policy.finishStartup()
        self.decide(keyError())
        self.policy.noticeClosed()
        self.clock.now += ep.ErrorPolicy.REPEAT_WINDOW + 1
        self.assertEqual(self.decide(keyError()).action, ep.NOTIFY)

    def test_a_storm_keeps_being_brief(self):
        self.policy.finishStartup()
        self.decide(keyError())
        self.policy.noticeClosed()
        for _ in range(20):
            self.clock.now += ep.ErrorPolicy.REPEAT_WINDOW / 2
            self.assertIn(self.decide(keyError()).action, (ep.BRIEF, ep.SILENT))

    def test_one_line_per_window_for_a_fast_storm(self):
        # e.g. a qasync timer that re-raises thousands of times a second
        self.policy.finishStartup()
        self.decide(keyError())
        actions = []
        for _ in range(5000):
            self.clock.now += 0.0001
            actions.append(self.decide(keyError()).action)
        self.assertEqual(actions.count(ep.BRIEF), 1)
        self.assertEqual(actions[0], ep.BRIEF)
        self.assertEqual(set(actions[1:]), {ep.SILENT})
        # the storm goes on: the next line, a window after the last one, carries the count
        self.clock.now += ep.ErrorPolicy.REPEAT_WINDOW - 0.4
        d = self.decide(keyError())
        self.assertEqual(d.action, ep.BRIEF)
        self.assertIn("+4999 more", d.text)

    def test_error_first_seen_while_another_notice_was_open_gets_its_own(self):
        # Y shows a notice; a timer-driven X fires meanwhile and keeps recurring: once
        # Y's notice closed, X must be shown, not stay a one-liner forever
        self.policy.finishStartup()
        self.assertEqual(self.decide(typeError()).action, ep.NOTIFY)  # Y
        self.assertEqual(self.decide(keyError()).reason, 'notice open')  # X, logged
        self.clock.now += 1
        self.assertIn(self.decide(keyError()).action, (ep.BRIEF, ep.SILENT))
        self.policy.noticeClosed()
        self.clock.now += 1  # X recurs within the repeat window
        self.assertEqual(self.decide(keyError()).action, ep.NOTIFY)
        self.clock.now += 1
        self.policy.noticeClosed()
        # now it was shown: reduced to (rate-limited) one-liners
        self.assertIn(self.decide(keyError()).action, (ep.BRIEF, ep.SILENT))

    def test_other_failure_while_the_notice_is_open_is_only_logged(self):
        self.policy.finishStartup()
        self.assertEqual(self.decide(keyError()).action, ep.NOTIFY)
        d = self.decide(typeError())
        self.assertEqual((d.action, d.reason), (ep.LOG, 'notice open'))
        self.assertIn("Traceback", d.text)  # still logged in full
        self.policy.noticeClosed()
        self.clock.now += 1
        self.assertEqual(self.decide(raiseIt(ValueError())).action, ep.NOTIFY)

    def test_a_lost_close_does_not_mute_notices_forever(self):
        self.policy.finishStartup()
        self.decide(keyError())
        self.clock.now += ep.ErrorPolicy.NOTICE_TIMEOUT + 1
        self.assertEqual(self.decide(typeError()).action, ep.NOTIFY)

    def test_interrupt_and_exit_pass_through(self):
        for started in (False, True):
            if started:
                self.policy.finishStartup()
            self.assertEqual(self.decide(raiseIt(KeyboardInterrupt())).action, ep.EXIT)
            self.assertEqual(self.decide(raiseIt(SystemExit(3))).action, ep.EXIT)

    def test_expected_errors_are_only_logged_even_at_startup(self):
        # the app's own LCU errors already showed "LCU request error" (or stay silent),
        # a closed client is not a bug: no second notice, never fatal
        for exc in (SummonerGamesNotFound(), RetryMaximumAttempts("x"), SummonerNotFound(),
                    ConnectionRefusedError(),
                    ClientConnectorError(mock.Mock(), OSError(61, "refused"))):
            policy = ep.ErrorPolicy(clock=self.clock)
            d = policy.decide(*raiseIt(exc))
            self.assertEqual((d.action, d.reason), (ep.LOG, 'expected'), type(exc))
            policy.finishStartup()
            self.assertEqual(policy.decide(*raiseIt(exc)).action, ep.LOG)

    def test_cancellation_is_brief(self):
        self.policy.finishStartup()
        self.assertEqual(self.decide(raiseIt(asyncio.CancelledError())).action, ep.BRIEF)

    def test_already_reported_exception_is_only_logged(self):
        info = keyError()
        policy = ep.ErrorPolicy(alreadyReported=lambda e: e is info[1], clock=self.clock)
        policy.finishStartup()
        self.assertEqual(policy.decide(*info).reason, 'reported')
        self.assertEqual(policy.decide(*typeError()).action, ep.NOTIFY)

    def test_a_failing_reported_check_does_not_hide_the_error(self):
        def broken(e):
            raise RuntimeError("boom")
        policy = ep.ErrorPolicy(alreadyReported=broken, clock=self.clock)
        policy.finishStartup()
        self.assertEqual(policy.decide(*keyError()).action, ep.NOTIFY)


class LoopExceptionHandlerTest(unittest.TestCase):

    def test_context_exception_goes_to_excepthook(self):
        ty, value, tb = keyError()
        with mock.patch.object(sys, 'excepthook') as hook:
            ep.handleLoopException(None, {'message': 'm', 'exception': value})
        hook.assert_called_once_with(KeyError, value, tb)

    def test_context_without_exception_logs_only_the_message(self):
        import logging
        seraphine = logging.getLogger("Seraphine")
        level = seraphine.level
        seraphine.setLevel(logging.WARNING)  # the default (40) drops this warning
        self.addCleanup(seraphine.setLevel, level)
        del LOG_RECORDS[:]
        with mock.patch.object(sys, 'excepthook') as hook:
            ep.handleLoopException(None, {'message': 'Unclosed client session',
                                          'client_session': 'token-like-repr'})
        hook.assert_not_called()
        self.assertTrue(any('Unclosed client session' in r for r in LOG_RECORDS))
        self.assertFalse(any('token-like-repr' in r for r in LOG_RECORDS))

    def test_failed_background_task_reaches_the_policy(self):
        """A real loop with the handler main.py installs: a fire-and-forget task that fails
        is decided by the same policy as sys.excepthook (one notice, then brief)"""
        from app.view import main_window as mw
        fake = makeHookOwner(mw)
        fake.errorPolicy.finishStartup()

        async def failing():
            {}['x']

        async def main():
            for _ in range(2):
                asyncio.get_running_loop().create_task(failing())
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                gc.collect()  # "Task exception was never retrieved" is raised on collection

        loop = asyncio.new_event_loop()
        loop.set_exception_handler(ep.handleLoopException)
        try:
            with mock.patch.object(sys, 'excepthook',
                                   lambda *a: mw.MainWindow.exceptHook(fake, *a)):
                loop.run_until_complete(main())
                gc.collect()
        finally:
            loop.close()

        notices = [c.args[0] for c in fake.showExceptionNotice.emit.call_args_list]
        self.assertEqual(notices, [ep.NOTIFY])  # the repeat was brief: logged in the hook
        fake.showExceptionMessageBox.emit.assert_not_called()


def makeHookOwner(mw):
    return SimpleNamespace(
        errorPolicy=ep.ErrorPolicy(),
        oldHook=mock.Mock(),
        showExceptionMessageBox=mock.Mock(),
        showExceptionNotice=mock.Mock())


class ExceptHookTest(unittest.TestCase):
    """MainWindow.exceptHook only routes what the policy decided"""

    def setUp(self):
        from app.view import main_window as mw
        self.mw = mw
        self.fake = makeHookOwner(mw)

    def hook(self, info):
        self.mw.MainWindow.exceptHook(self.fake, *info)

    def test_before_startup_keeps_the_fatal_dialog(self):
        self.hook(keyError())
        self.fake.showExceptionMessageBox.emit.assert_called_once()
        self.assertIn("KeyError", self.fake.showExceptionMessageBox.emit.call_args.args[0])
        self.fake.showExceptionNotice.emit.assert_not_called()

    def test_after_startup_a_notice_not_the_dialog(self):
        self.fake.errorPolicy.finishStartup()
        self.hook(keyError())
        self.fake.showExceptionMessageBox.emit.assert_not_called()
        action, reason, text = self.fake.showExceptionNotice.emit.call_args.args
        self.assertEqual(action, ep.NOTIFY)
        self.assertIn("Traceback", text)
        self.fake.oldHook.assert_called_once()

    def test_duplicates_and_concurrent_failures_never_show(self):
        self.fake.errorPolicy.finishStartup()
        del LOG_RECORDS[:]
        self.hook(keyError())
        self.hook(keyError())  # duplicate: one warning line from the hook itself
        self.hook(typeError())  # while the notice is open
        actions = [c.args[0] for c in self.fake.showExceptionNotice.emit.call_args_list]
        self.assertEqual(actions, [ep.NOTIFY, ep.LOG])
        self.assertTrue(any('repeated' in r and 'KeyError' in r for r in LOG_RECORDS))
        self.fake.showExceptionMessageBox.emit.assert_not_called()

    def test_interrupt_ends_the_process_without_the_dialog(self):
        self.fake.errorPolicy.finishStartup()
        self.hook(raiseIt(KeyboardInterrupt()))
        self.assertEqual(self.fake.showExceptionNotice.emit.call_args.args[0], ep.EXIT)
        self.fake.showExceptionMessageBox.emit.assert_not_called()


class NoticeSlotTest(unittest.TestCase):
    """MainWindow.__onShowExceptionNotice on a fake self (InfoBar / button replaced)"""

    def setUp(self):
        from app.view import main_window as mw
        self.mw = mw
        self.fake = SimpleNamespace(
            tr=lambda text: text,
            errorPolicy=ep.ErrorPolicy(),
            searchInterface='search', gameInfoInterface='gameinfo',
            careerInterface='career', auxiliaryFuncInterface='aux',
            settingInterface='settings')
        self.fake._MainWindow__logExceptionDiagnostics = \
            lambda content: mw.MainWindow._MainWindow__logExceptionDiagnostics(self.fake, content)

    def notice(self, action, reason='first', content='Traceback: boom'):
        with mock.patch.object(self.mw, 'InfoBar') as infoBar, \
                mock.patch.object(self.mw, 'PushButton') as button, \
                mock.patch.object(self.mw, 'pyperclip') as clip, \
                mock.patch.object(self.mw, 'signalBus') as bus:
            self.mw.MainWindow._MainWindow__onShowExceptionNotice(
                self.fake, action, reason, content)
        return infoBar, button, clip, bus

    def test_notify_shows_a_closable_bar_with_copy_details(self):
        del LOG_RECORDS[:]
        infoBar, button, _, bus = self.notice(ep.NOTIFY)
        infoBar.error.assert_called_once()
        self.assertEqual(infoBar.error.call_args.args[0], 'Exception occurred 😥')
        self.assertTrue(infoBar.error.call_args.kwargs['isClosable'])
        self.assertGreater(infoBar.error.call_args.kwargs['duration'], 0)
        bar = infoBar.error.return_value
        bar.closedSignal.connect.assert_called_once_with(self.fake.errorPolicy.noticeClosed)
        # the copy button copies the same text the fatal dialog offers
        self.assertEqual(button.call_args.args[0], 'Copy details')
        copy = button.return_value.clicked.connect.call_args.args[0]
        with mock.patch.object(self.mw, 'pyperclip') as clip:
            copy()
        copied = clip.copy.call_args.args[0]
        self.assertTrue(copied.startswith('Seraphine ver.'))
        self.assertIn('Traceback: boom', copied)
        # the existing diagnostic lines
        self.assertTrue(any('Exception occurred:' in r and 'boom' in r for r in LOG_RECORDS))
        self.assertTrue(any('gameinfo' in r for r in LOG_RECORDS))
        bus.terminateListeners.emit.assert_not_called()

    def test_log_only_shows_nothing(self):
        del LOG_RECORDS[:]
        infoBar, *_ = self.notice(ep.LOG, reason='notice open')
        infoBar.error.assert_not_called()
        self.assertTrue(any('notice open' in r and 'boom' in r for r in LOG_RECORDS))

    def test_exit_terminates(self):
        with self.assertRaises(SystemExit):
            with mock.patch.object(self.mw, 'signalBus') as bus:
                self.mw.MainWindow._MainWindow__onShowExceptionNotice(
                    self.fake, ep.EXIT, 'interrupt', 'KeyboardInterrupt')
        bus.terminateListeners.emit.assert_called_once()


if __name__ == '__main__':
    unittest.main()
