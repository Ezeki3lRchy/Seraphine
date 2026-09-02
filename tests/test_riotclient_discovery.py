"""Riot Client local-API discovery (reveal hidden teammates, task 1).

Run: venv/bin/python -m unittest discover -s tests -t . -v
"""
import os
import subprocess
import sys
import tempfile
import time
import unittest

from app.common.util import (parseRiotClientArgs, readRiotClientLockfile,
                             getRiotClientPortToken)


class ParseRiotClientArgsTest(unittest.TestCase):
    """Pure argv parsing — fake cmdline lists, no processes involved."""

    def test_both_args_in_normal_order(self):
        cmdline = [
            "/Applications/League of Legends.app/Contents/LoL/LeagueClientUx",
            "--riotclient-auth-token=AbC123",
            "--riotclient-app-port=54321",
            "--app-port=61234",
            "--remoting-auth-token=xyz",
        ]
        self.assertEqual(parseRiotClientArgs(cmdline), ("54321", "AbC123"))

    def test_both_args_reversed_order(self):
        cmdline = [
            "LeagueClientUx.exe",
            "--app-port=61234",
            "--riotclient-app-port=1234",
            "--riotclient-auth-token=tok",
        ]
        self.assertEqual(parseRiotClientArgs(cmdline), ("1234", "tok"))

    def test_only_lcu_args_present(self):
        # --app-port / --remoting-auth-token must NOT be mistaken for the
        # Riot Client args (different server, different token).
        cmdline = ["LeagueClientUx.exe", "--app-port=61234",
                   "--remoting-auth-token=xyz", "--rso_platform_id=NA1"]
        self.assertEqual(parseRiotClientArgs(cmdline), (None, None))

    def test_missing_token_leaves_token_none(self):
        cmdline = ["LeagueClientUx.exe", "--riotclient-app-port=999"]
        self.assertEqual(parseRiotClientArgs(cmdline), ("999", None))

    def test_quoted_windows_style_args(self):
        cmdline = ['"--riotclient-app-port=4242"',
                   '"--riotclient-auth-token=q-tok"']
        self.assertEqual(parseRiotClientArgs(cmdline), ("4242", "q-tok"))

    def test_garbage_input_never_raises(self):
        self.assertEqual(parseRiotClientArgs(None), (None, None))
        self.assertEqual(parseRiotClientArgs([]), (None, None))
        self.assertEqual(parseRiotClientArgs([None, 42, b"bytes"]), (None, None))
        # non-numeric port is rejected
        self.assertEqual(
            parseRiotClientArgs(["--riotclient-app-port=abc",
                                 "--riotclient-auth-token=t"]),
            (None, "t"))


class RiotClientLockfileTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _write(self, name, body):
        path = os.path.join(self.tmp.name, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)
        return path

    def test_reads_first_readable_candidate(self):
        missing = os.path.join(self.tmp.name, "does-not-exist")
        good = self._write("lockfile", "Riot Client:4321:55555:s3cret:https")
        self.assertEqual(readRiotClientLockfile([missing, good]),
                         ("55555", "s3cret"))

    def test_malformed_then_good(self):
        bad = self._write("bad", "garbage")
        good = self._write("lockfile", "Riot Client:1:777:pw:https\n")
        self.assertEqual(readRiotClientLockfile([bad, good]), ("777", "pw"))

    def test_nothing_readable(self):
        self.assertEqual(readRiotClientLockfile(
            [os.path.join(self.tmp.name, "nope")]), (None, None))


class GetRiotClientPortTokenTest(unittest.TestCase):
    """End-to-end wrapper: real psutil against a real child process, real
    lockfile fallback in a temp dir. Nothing under test is mocked."""

    BOGUS_PID = 2 ** 22 + 12345  # far above macOS/Windows pid ranges

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.missing = [os.path.join(self.tmp.name, "no-lockfile")]

    def _spawn(self, *extraArgs):
        # A real process whose argv carries the Riot Client args, so the
        # psutil cmdline path is exercised for real.
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)", *extraArgs],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(proc.wait)
        self.addCleanup(proc.terminate)
        time.sleep(0.2)
        return proc.pid

    def test_cmdline_path_wins(self):
        pid = self._spawn("--riotclient-app-port=31337",
                          "--riotclient-auth-token=cmd-tok")
        self.assertEqual(getRiotClientPortToken(pid, self.missing),
                         ("31337", "cmd-tok"))

    def test_lockfile_fallback_when_cmdline_lacks_args(self):
        lock = os.path.join(self.tmp.name, "lockfile")
        with open(lock, "w", encoding="utf-8") as f:
            f.write("Riot Client:99:6001:lock-tok:https")
        # own process: real cmdline, no riot args → must fall through
        self.assertEqual(getRiotClientPortToken(os.getpid(), [lock]),
                         ("6001", "lock-tok"))

    def test_bogus_pid_and_no_lockfile_returns_none_pair(self):
        self.assertEqual(getRiotClientPortToken(self.BOGUS_PID, self.missing),
                         (None, None))

    def test_none_pid_never_raises(self):
        self.assertEqual(getRiotClientPortToken(None, self.missing),
                         (None, None))


if __name__ == "__main__":
    unittest.main()
