"""Idle resource use: the process listener's cheap paths and the spinners paused while hidden.

ProcessNames (app/lol/listener.py) reads a process name once per pid, again while the process
is young, and all of them once the last full read is REFRESH seconds old; psutil and the clocks
are replaced, so no real process is read.
The listener skips its poll (tasklist / process scan) while the connected client is still the
same running process, and polls as before as soon as it is not. run() is called on this thread
with msleep replaced, as in test_listener_survives. The spinners (app/common/spinners.py) run
their animation only while visible and report the state the caller asked for. Offscreen only.
"""
import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QAbstractAnimation
from PySide6.QtWidgets import QApplication, QWidget

from app.common import qfluentwidgets as fluent
from app.common import spinners
from app.lol import listener as listener_module


class StopLoop(BaseException):
    pass


# ---------------- ProcessNames ----------------

class FakePsutil:
    """pid -> [start time, name]; counts name reads"""

    Error = listener_module.psutil.Error

    def __init__(self, table):
        self.table = table
        self.reads = []
        self.denied = set()

    def pids(self):
        return sorted(self.table)

    def Process(self, pid):
        fake = self

        class Proc:
            def create_time(self):
                if pid in fake.denied:
                    raise listener_module.psutil.AccessDenied(pid)
                return fake.table[pid][0]

            def name(self):
                fake.reads.append(pid)
                return fake.table[pid][1]

        return Proc()


class ProcessNamesTest(unittest.TestCase):

    def setUp(self):
        self.now = [1000.0]
        self.fake = FakePsutil({1: [10.0, 'launchd'], 50: [500.0, 'zsh'],
                                70: [900.0, 'Finder']})
        self.patches = [mock.patch.object(listener_module.psutil, 'pids', self.fake.pids),
                        mock.patch.object(listener_module.psutil, 'Process', self.fake.Process),
                        mock.patch.object(listener_module.time, 'time', lambda: self.now[0]),
                        mock.patch.object(listener_module.time, 'monotonic',
                                          lambda: self.now[0])]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        self.names = listener_module.ProcessNames()

    def scan(self):
        del self.fake.reads[:]
        return self.names.scan()

    def test_a_name_is_read_once_per_process(self):
        self.assertEqual(self.scan(), {1: 'launchd', 50: 'zsh', 70: 'Finder'})
        self.assertEqual(sorted(self.fake.reads), [1, 50, 70])
        self.now[0] += 1.5
        self.assertEqual(self.scan(), {1: 'launchd', 50: 'zsh', 70: 'Finder'})
        self.assertEqual(self.fake.reads, [])  # nothing read again

    def test_new_and_gone_processes(self):
        self.scan()
        self.now[0] += 30  # within REFRESH
        del self.fake.table[50]
        self.fake.table[80] = [self.now[0] - 30, 'LeagueClientUx']
        self.assertEqual(self.scan(), {1: 'launchd', 70: 'Finder', 80: 'LeagueClientUx'})
        self.assertEqual(self.fake.reads, [80])

    def test_a_young_process_is_read_on_every_scan(self):
        # forked as 'Riot Client', then exec'd into the client while still young
        born = self.now[0]
        self.fake.table[90] = [born, 'Riot Client']
        self.assertEqual(self.scan()[90], 'Riot Client')
        self.now[0] += 1.5
        self.fake.table[90][1] = 'LeagueClientUx'
        self.assertEqual(self.scan()[90], 'LeagueClientUx')
        self.assertEqual(self.fake.reads, [90])
        self.now[0] = born + listener_module.ProcessNames.YOUNG - 0.1
        self.scan()
        self.assertEqual(self.fake.reads, [90])
        # no longer young: read no more
        self.now[0] = born + listener_module.ProcessNames.YOUNG
        self.scan()
        self.assertEqual(self.fake.reads, [])

    def test_every_name_is_read_again_after_refresh_seconds(self):
        start = self.now[0]
        self.scan()
        self.fake.table[70][1] = 'renamed'
        while self.now[0] + 1.5 < start + listener_module.ProcessNames.REFRESH:
            self.now[0] += 1.5
            self.assertEqual(self.scan()[70], 'Finder')
            self.assertEqual(self.fake.reads, [])
        self.now[0] = start + listener_module.ProcessNames.REFRESH
        self.assertEqual(self.scan()[70], 'renamed')
        self.assertEqual(sorted(self.fake.reads), [1, 50, 70])

    def test_the_first_scan_after_a_connected_session_reads_every_name(self):
        # no scans while connected; meanwhile pid 50 died and the game got the pid
        self.scan()
        self.now[0] += 3 * 3600
        self.fake.table[50] = [self.now[0] - 600, listener_module.MAC_GAME_NAME]
        self.assertEqual(self.scan()[50], listener_module.MAC_GAME_NAME)

    def test_an_unreadable_process_is_left_out_and_tried_again(self):
        self.fake.denied.add(50)
        self.assertEqual(self.scan(), {1: 'launchd', 70: 'Finder'})
        self.fake.denied.clear()
        self.now[0] += 1.5
        self.assertEqual(self.scan(), {1: 'launchd', 50: 'zsh', 70: 'Finder'})
        self.assertEqual(self.fake.reads, [50])

    def test_same_names_as_psutil_on_this_machine(self):
        for p in self.patches:  # the real psutil and clocks
            p.stop()
        names = listener_module.ProcessNames()
        first, second = names.scan(), names.scan()
        self.assertTrue(first)
        mine = os.getpid()
        self.assertEqual(second[mine], listener_module.psutil.Process(mine).name())


# ---------------- the connected client ----------------

class FakeClient:
    def __init__(self, pid):
        self.pid = pid
        self.running = True

    def is_running(self):
        return self.running


class ConnectedClientTest(unittest.TestCase):

    def run_listener(self, pids, polls, onSleep=None, clients=None):
        """pids: list of client pid lists, one per scan; returns (bus, scans, listener)"""
        listener = listener_module.LolProcessExistenceListener(None)
        sleeps = []

        def msleep(ms):
            sleeps.append(ms)
            if onSleep is not None:
                onSleep(len(sleeps), listener)
            if len(sleeps) >= polls:
                raise StopLoop()

        listener.msleep = msleep
        scans = []

        def getPids(path):
            scans.append(len(sleeps))
            return pids[min(len(scans) - 1, len(pids) - 1)]

        handles = clients if clients is not None else {}

        def clientProcess(pid):
            return handles.setdefault(pid, FakeClient(pid))

        patches = [mock.patch.object(listener_module, 'signalBus'),
                   mock.patch.object(listener_module.platform, 'IS_WINDOWS', True),
                   mock.patch.object(listener_module, 'getTasklistPath', return_value='tasklist'),
                   mock.patch.object(listener_module, 'getLolClientPids', getPids),
                   mock.patch.object(listener_module, 'isLolGameProcessExist', return_value=False),
                   mock.patch.object(listener_module, 'clientProcess', clientProcess)]
        bus = [p.start() for p in patches][0]
        self.addCleanup(lambda: [p.stop() for p in patches])
        with self.assertRaises(StopLoop):
            listener.run()
        return bus, scans, sleeps, handles

    def test_no_scan_while_the_client_runs(self):
        bus, scans, sleeps, _ = self.run_listener([[], [7]], polls=20)
        bus.lolClientStarted.emit.assert_called_once_with(7)
        self.assertEqual(scans, [0, 1])  # the two scans before the client was connected
        self.assertEqual(sleeps, [listener_module.POLL_MS] * 20)  # the interval is unchanged

    def test_the_client_ending_is_seen_on_the_next_poll(self):
        clients = {}

        def onSleep(n, listener):
            if n == 5:
                clients[7].running = False

        bus, scans, _, _ = self.run_listener([[7], []], polls=8, onSleep=onSleep,
                                             clients=clients)
        bus.lolClientEnded.emit.assert_called_once()
        self.assertEqual(scans, [0, 5, 6, 7])  # scanning again from the poll after the exit

    def test_a_changed_client_after_the_first_one_ended(self):
        clients = {}

        def onSleep(n, listener):
            if n == 3:
                clients[7].running = False

        bus, _, _, _ = self.run_listener([[7], [8]], polls=6, onSleep=onSleep,
                                         clients=clients)
        bus.lolClientStarted.emit.assert_called_once_with(7)
        bus.lolClientChanged.emit.assert_called_once_with(8)

    def test_a_pid_set_from_outside_gets_its_own_handle(self):
        # MainWindow sets runningPid (client changed by the user, retry after a failed start)
        clients = {}

        def onSleep(n, listener):
            if n == 3:
                listener.runningPid = 9

        _, _, _, handles = self.run_listener([[7]], polls=5, onSleep=onSleep, clients=clients)
        self.assertEqual(sorted(handles), [7, 9])

    def test_no_handle_means_every_poll_scans(self):
        listener = listener_module.LolProcessExistenceListener(None)
        listener.runningPid = 7
        with mock.patch.object(listener_module, 'clientProcess', return_value=None), \
                mock.patch.object(listener_module, 'getLolClientPids', return_value=[7]) as scan, \
                mock.patch.object(listener_module, 'signalBus'), \
                mock.patch.object(listener_module, 'getTasklistPath', return_value='tasklist'), \
                mock.patch.object(listener_module.platform, 'IS_WINDOWS', True), \
                mock.patch.object(listener, 'msleep', side_effect=[None, None, StopLoop()]):
            with self.assertRaises(StopLoop):
                listener.run()
        self.assertEqual(scan.call_count, 3)

    def test_client_process_needs_a_client_with_a_start_time(self):
        self.assertIsNone(listener_module.clientProcess(2 ** 30))  # no such pid
        # this process is no client: its pid may have been reused after the poll found it
        self.assertIsNone(listener_module.clientProcess(os.getpid()))
        Process = listener_module.psutil.Process
        for name in listener_module.CLIENT_NAMES:
            with self.subTest(name=name), mock.patch.object(Process, 'name', return_value=name):
                mine = listener_module.clientProcess(os.getpid())
                self.assertTrue(mine.is_running())
                with mock.patch.object(Process, 'create_time',
                                       side_effect=listener_module.psutil.AccessDenied(1)):
                    self.assertIsNone(listener_module.clientProcess(os.getpid()))


# ---------------- spinners ----------------

def state(spinner):
    return spinner.aniGroup.state()


RUNNING, PAUSED, STOPPED = (QAbstractAnimation.Running, QAbstractAnimation.Paused,
                            QAbstractAnimation.Stopped)


class SpinnerTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        if cls.app.platformName() != "offscreen":
            raise RuntimeError("run with QT_QPA_PLATFORM=offscreen")

    def window(self, spinnerClass, **kwargs):
        window = QWidget()
        spinner = spinnerClass(window, **kwargs)
        self.addCleanup(window.deleteLater)
        return window, spinner

    def test_the_app_module_exports_them(self):
        self.assertIs(fluent.IndeterminateProgressRing, spinners.IndeterminateProgressRing)
        self.assertIs(fluent.IndeterminateProgressBar, spinners.IndeterminateProgressBar)

    def test_runs_only_while_visible(self):
        for cls in (spinners.IndeterminateProgressRing, spinners.IndeterminateProgressBar):
            with self.subTest(cls=cls.__name__):
                window, spinner = self.window(cls)
                self.assertEqual(state(spinner), PAUSED)  # started on construction, hidden
                window.show()
                self.assertEqual(state(spinner), RUNNING)
                window.hide()
                self.assertEqual(state(spinner), PAUSED)
                window.show()
                self.assertEqual(state(spinner), RUNNING)
                spinner.setVisible(False)  # the spinner itself hidden
                self.assertEqual(state(spinner), PAUSED)
                spinner.setVisible(True)
                self.assertEqual(state(spinner), RUNNING)

    def test_stop_and_start_while_hidden(self):
        window, spinner = self.window(spinners.IndeterminateProgressRing)
        spinner.stop()
        window.show()
        self.assertEqual(state(spinner), STOPPED)  # a stopped spinner stays stopped
        window.hide()
        spinner.start()
        self.assertEqual(state(spinner), PAUSED)
        window.show()
        self.assertEqual(state(spinner), RUNNING)

    def test_not_started(self):
        window, spinner = self.window(spinners.IndeterminateProgressBar, start=False)
        window.show()
        window.hide()
        window.show()
        self.assertEqual(state(spinner), STOPPED)
        self.assertFalse(spinner.isStarted())

    def test_bar_reports_the_state_asked_for(self):
        window, bar = self.window(spinners.IndeterminateProgressBar)
        self.assertTrue(bar.isStarted())  # paused only because hidden
        self.assertFalse(bar.isPaused())
        window.show()
        bar.setPaused(True)
        self.assertTrue(bar.isPaused())
        window.hide()
        window.show()
        self.assertTrue(bar.isPaused())  # not resumed by the show
        window.hide()
        bar.setPaused(False)
        self.assertTrue(bar.isStarted())
        self.assertFalse(bar.isPaused())
        self.assertEqual(state(bar), PAUSED)  # still hidden
        window.show()
        self.assertEqual(state(bar), RUNNING)
        bar.error()
        window.hide()
        window.show()
        self.assertEqual(state(bar), STOPPED)
        self.assertFalse(bar.isStarted())

    def test_started_while_minimized(self):
        window, spinner = self.window(spinners.IndeterminateProgressRing, start=False)
        window.show()
        with mock.patch.object(QWidget, 'isMinimized', return_value=True):
            spinner.start()
        self.assertEqual(state(spinner), PAUSED)

    def test_nothing_animates_while_every_spinner_is_hidden(self):
        windows = [self.window(cls)[0] for cls in (spinners.IndeterminateProgressRing,
                                                   spinners.IndeterminateProgressBar) * 3]
        for window in windows:
            window.show()
            window.hide()
        running = [a for w in windows for a in w.findChildren(QAbstractAnimation)
                   if a.state() == RUNNING]
        self.assertEqual(running, [])


if __name__ == '__main__':
    unittest.main()
