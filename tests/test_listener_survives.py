"""LolProcessExistenceListener: one failed poll must not end the thread (client start /
end / change would never be detected again), and a persistent failure must not flood the
log. run() is called directly on this thread (the QThread is never started); msleep is
replaced to count iterations and stop the loop. No process is inspected (the
process scans and the connected-client check are replaced too).
"""
import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.lol import listener as listener_module
from tests import LOG_RECORDS


class StopLoop(BaseException):
    pass


class ListenerSurvivesTest(unittest.TestCase):

    def runPolls(self, pids, polls, mac=False, clock=None):
        """pids: (poll index) -> list, or raises; runs `polls` iterations"""
        listener = listener_module.LolProcessExistenceListener(None)
        sleeps = []

        def msleep(ms):
            sleeps.append(ms)
            if len(sleeps) >= polls:
                raise StopLoop()

        listener.msleep = msleep
        calls = []

        def getPids(*args):
            calls.append(1)
            return pids(len(calls) - 1)

        def macNames():
            return {pid: 'LeagueClientUx' for pid in getPids()}

        patches = [mock.patch.object(listener_module, 'signalBus'),
                   mock.patch.object(listener_module.platform, 'IS_WINDOWS', not mac),
                   mock.patch.object(listener_module, 'getTasklistPath', return_value='tasklist'),
                   mock.patch.object(listener_module, 'getLolClientPids', getPids),
                   mock.patch.object(listener_module.ProcessNames, 'scan',
                                     lambda self: macNames()),
                   mock.patch.object(listener_module, 'isLolGameProcessExist', return_value=False),
                   # no pid here is a real process: every poll scans, as with no client
                   mock.patch.object(listener_module, 'clientProcess', return_value=None)]
        if clock is not None:
            patches.append(mock.patch.object(listener_module.time, 'monotonic', clock))
        started = [p.start() for p in patches]
        self.addCleanup(lambda: [p.stop() for p in patches])
        bus = started[0]
        with self.assertRaises(StopLoop):
            listener.run()
        return bus, sleeps

    def test_a_failed_poll_is_survived(self):
        for mac in (False, True):
            with self.subTest(mac=mac):
                del LOG_RECORDS[:]

                def pids(i):
                    if i == 0:
                        raise UnicodeDecodeError('gbk', b'\xff', 0, 1, 'secret-detail')
                    return [4242]

                bus, sleeps = self.runPolls(pids, polls=3, mac=mac)
                bus.lolClientStarted.emit.assert_called_once_with(4242)
                self.assertEqual(sleeps, [1500, 1500, 1500])  # the normal interval
                failures = [r for r in LOG_RECORDS if 'process poll failed' in r]
                self.assertEqual(len(failures), 1)
                self.assertIn('UnicodeDecodeError', failures[0])
                self.assertNotIn('secret-detail', failures[0])  # the type only

    def test_a_persistent_failure_is_logged_occasionally(self):
        del LOG_RECORDS[:]
        now = [0.0]

        def clock():
            now[0] += 1.5  # one poll interval per call
            return now[0]

        def pids(i):
            raise PermissionError("denied")

        interval = listener_module.LolProcessExistenceListener.FAILURE_LOG_INTERVAL
        polls = int(interval / 1.5) * 2 + 5  # a little over two intervals
        self.runPolls(pids, polls=polls, clock=clock)
        failures = [r for r in LOG_RECORDS if 'process poll failed' in r]
        self.assertEqual(len(failures), 3)  # first, then once per interval
        self.assertIn('PermissionError', failures[1])

    def test_normal_polls_are_unchanged(self):
        del LOG_RECORDS[:]
        seq = [[], [7], [7], [8], []]
        bus, sleeps = self.runPolls(lambda i: seq[i], polls=5)
        bus.lolClientAbsent.emit.assert_called_once()
        bus.lolClientStarted.emit.assert_called_once_with(7)
        bus.lolClientChanged.emit.assert_called_once_with(8)
        bus.lolClientEnded.emit.assert_called_once()
        self.assertEqual([r for r in LOG_RECORDS if 'process poll failed' in r], [])


if __name__ == '__main__':
    unittest.main()
