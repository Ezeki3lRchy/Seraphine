"""Manual "check for updates now" flow: outcome decision (util) and MainWindow's worker."""
import base64
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

from PySide6.QtCore import Qt

from app.common import util
from app.common.signals import signalBus


class CheckUpdateOutcomeTest(unittest.TestCase):

    def test_newer_version(self):
        info = {"tag_name": "v9.9.9"}
        self.assertEqual(util.checkUpdateOutcome(lambda: info), ("update", info))

    def test_up_to_date_and_no_release_are_both_latest(self):
        self.assertEqual(util.checkUpdateOutcome(lambda: None), ("latest", None))

    def test_error(self):
        def boom():
            raise RuntimeError("rate limited")
        self.assertEqual(util.checkUpdateOutcome(boom), ("error", None))

    def test_lock_timeout_is_error_and_check_not_called(self):
        lock = threading.Lock()
        lock.acquire()
        check = mock.Mock()
        self.assertEqual(
            util.checkUpdateOutcome(check, lock=lock, lockTimeout=0.05),
            ("error", None))
        check.assert_not_called()

    def test_lock_released_after_check(self):
        lock = threading.Lock()

        def boom():
            raise RuntimeError("x")
        util.checkUpdateOutcome(boom, lock=lock)
        self.assertFalse(lock.locked())
        util.checkUpdateOutcome(lambda: None, lock=lock)
        self.assertFalse(lock.locked())

    def test_ignores_enable_switch_and_uses_github(self):
        with mock.patch.object(util.cfg, "get", return_value=False), \
                mock.patch.object(util.github, "checkUpdate",
                                  return_value={"tag_name": "v9.9.9"}) as m:
            self.assertEqual(util.checkUpdateOutcome()[0], "update")
            m.assert_called_once()


class GithubRequestTest(unittest.TestCase):
    """Every GitHub request is bounded and uses the configured proxy (no network)."""

    class Sess:
        def __init__(self):
            self.calls = []

        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            content = base64.b64encode(b'{}').decode()
            return SimpleNamespace(json=lambda: {'content': content, 'sha': 'abc',
                                                 'tag_name': 'v1.0.0'})

    def requests(self, proxyOn):
        gh = util.Github()
        gh.sess = self.Sess()
        values = {util.cfg.enableProxy: proxyOn, util.cfg.proxyAddr: '127.0.0.1:7890'}
        with mock.patch.object(util.cfg, 'get', side_effect=values.get):
            gh.getReleasesInfo()
            gh._Github__get_ver_info()
            self.assertEqual(gh.getNotice(), {'sha': 'abc', 'content': '{}'})
        return gh.sess.calls

    def test_every_request_has_a_timeout_and_the_proxy(self):
        for proxyOn, proxies in ((True, {'https': '127.0.0.1:7890'}), (False, None)):
            calls = self.requests(proxyOn)
            self.assertEqual(len(calls), 3)
            for url, kwargs in calls:
                self.assertEqual(kwargs.get('timeout'), 10, url)
                self.assertEqual(kwargs.get('proxies'), proxies, url)


class ManualCheckUpdateWorkerTest(unittest.TestCase):

    def setUp(self):
        from app.view import main_window
        self.mw = main_window
        self.fake = SimpleNamespace(
            checkUpdateLock=threading.Lock(),
            showUpdateMessageBox=mock.Mock(),
            showUpToDateInfo=mock.Mock(),
            checkUpdateFailed=mock.Mock(),
        )
        self.finished = []
        self.slot = lambda: self.finished.append(1)
        signalBus.checkUpdateFinished.connect(self.slot, Qt.DirectConnection)

    def tearDown(self):
        signalBus.checkUpdateFinished.disconnect(self.slot)

    def run_worker(self, outcome):
        with mock.patch.object(self.mw, "checkUpdateOutcome",
                               lambda **kw: outcome()):
            self.mw.MainWindow.manualCheckUpdate(self.fake)

    def test_update(self):
        self.run_worker(lambda: ("update", {"a": 1}))
        self.fake.showUpdateMessageBox.emit.assert_called_once_with({"a": 1})
        self.fake.showUpToDateInfo.emit.assert_not_called()
        self.fake.checkUpdateFailed.emit.assert_not_called()
        self.assertEqual(self.finished, [1])

    def test_latest(self):
        self.run_worker(lambda: ("latest", None))
        self.fake.showUpToDateInfo.emit.assert_called_once()
        self.fake.showUpdateMessageBox.emit.assert_not_called()
        self.assertEqual(self.finished, [1])

    def test_error(self):
        self.run_worker(lambda: ("error", None))
        self.fake.checkUpdateFailed.emit.assert_called_once()
        self.assertEqual(self.finished, [1])

    def test_unexpected_exception_still_finishes(self):
        def boom():
            raise ValueError("x")
        self.run_worker(boom)
        self.fake.checkUpdateFailed.emit.assert_called_once()
        self.assertEqual(self.finished, [1])

    def test_manual_check_ignores_enable_switch(self):
        # the worker never consults cfg.enableCheckUpdate
        with mock.patch.object(self.mw.cfg, "get", return_value=False):
            self.run_worker(lambda: ("latest", None))
        self.fake.showUpToDateInfo.emit.assert_called_once()

    def test_passes_lock_to_outcome(self):
        seen = {}
        with mock.patch.object(
                self.mw, "checkUpdateOutcome",
                lambda **kw: seen.update(kw) or ("latest", None)):
            self.mw.MainWindow.manualCheckUpdate(self.fake)
        self.assertIs(seen["lock"], self.fake.checkUpdateLock)
        self.assertEqual(self.finished, [1])


class UpdateBoxReentryTest(unittest.TestCase):

    def test_second_dialog_is_not_opened_while_first_is_open(self):
        from app.view import main_window as mw
        fake = SimpleNamespace(updateBoxOpen=False, window=lambda: None)
        opened = []

        class Box:
            def __init__(self, info, parent):
                opened.append(info)

            def exec(self):
                # a nested event loop delivers the second queued signal
                mw.MainWindow._MainWindow__onShowUpdateMessageBox(fake, "second")

        with mock.patch.object(mw, "UpdateMessageBox", Box):
            mw.MainWindow._MainWindow__onShowUpdateMessageBox(fake, "first")
        self.assertEqual(opened, ["first"])
        self.assertFalse(fake.updateBoxOpen)


if __name__ == "__main__":
    unittest.main()
