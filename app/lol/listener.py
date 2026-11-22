import time

import psutil
from PySide6.QtCore import QThread

from app.common.logger import logger
from app.common.signals import signalBus
from app.common.util import getLolClientPids, isLolGameProcessExist, getTasklistPath
from app.common import platform

TAG = "Listener"

POLL_MS = 1500

# the client's process name, with (Windows) and without (macOS) the extension, as
# getLolClientPidsSlowly matches it
CLIENT_NAMES = ('LeagueClientUx.exe', 'LeagueClientUx')
MAC_GAME_NAME = 'League of Legends'  # macOS (no .exe)


class ProcessNames:
    """
    pid -> name of every running process, for the macOS poll. Reading a name is what a scan
    costs (psutil also reads the command line of each process whose name has 15 characters or
    more): about 24 ms of CPU for 1,000 processes, measured on macOS. So a name is read when its
    pid first appears and kept while the pid stays listed. A process younger than YOUNG seconds
    may still exec another program, so its name is read again on every scan. All names are read
    again when the last such full read is REFRESH seconds old, also the first scan after a pause
    (the listener does not scan while the client is connected), so a pid that died and was reused
    meanwhile is not reported under its old name. A process whose start time or name cannot be
    read is left out of that scan and tried again on the next.
    """

    YOUNG = 5.0
    REFRESH = 60.0

    def __init__(self):
        self._known = {}  # pid -> (start time, name)
        self._fullReadAt = None  # time.monotonic() of the last scan that read every name

    def scan(self):
        if self._fullReadAt is None or time.monotonic() - self._fullReadAt >= self.REFRESH:
            self._fullReadAt = time.monotonic()
            self._known = {}

        now = time.time()
        known = {}
        for pid in psutil.pids():
            entry = self._known.get(pid)
            if entry is None or now - entry[0] < self.YOUNG:
                try:
                    proc = psutil.Process(pid)
                    entry = (proc.create_time(), proc.name())
                except psutil.Error:
                    continue
            known[pid] = entry
        self._known = known
        return {pid: name for pid, (_, name) in known.items()}


def clientProcess(pid):
    """psutil handle of the client process `pid`, or None when it is gone, is not a client (the
    pid was reused after the poll that found it) or its start time cannot be read (without it, a
    later reuse of the pid could not be told from the client)"""
    try:
        proc = psutil.Process(pid)
        proc.create_time()
        return proc if proc.name() in CLIENT_NAMES else None
    except psutil.Error:
        return None


class LolProcessExistenceListener(QThread):
    def __init__(self, parent):
        # 当前 Seraphine 连接的客户端 pid
        self.runningPid = 0
        # failed polls (see __iterationFailed): how many since the last log line, and when
        self._failures = 0
        self._lastFailureLog = None
        # psutil handle of the connected client (see __clientStillRunning)
        self._client = None
        self._names = ProcessNames()
        self._lastNames = {}

        super().__init__(parent)

    # a persistent failure is logged at most this often (seconds)
    FAILURE_LOG_INTERVAL = 300

    def __iterationFailed(self, e):
        """One poll failed (tasklist output that does not decode, psutil AccessDenied, ...):
        the thread must survive it, or a client start / end / change is never seen again.
        Logs the type only (never the message), the first time and then at most every
        FAILURE_LOG_INTERVAL seconds with the count"""
        self._failures += 1
        now = time.monotonic()
        if (self._lastFailureLog is not None
                and now - self._lastFailureLog < self.FAILURE_LOG_INTERVAL):
            return

        logger.error(f"process poll failed: {type(e).__name__} "
                     f"({self._failures} failure(s) since the last report)", TAG)
        self._lastFailureLog = now
        self._failures = 0

    def run(self):
        # macOS 没有 tasklist.exe, 且进程名是 "League of Legends" (无 .exe);
        # 走 psutil 的等价循环, 避免依赖 getTasklistPath() (其在 Mac 上恒为 None
        # 会发出 tasklistNotFound 并直接 return, 导致发现逻辑永不启动)。
        if not platform.IS_WINDOWS:
            self.__loop(self.__macClientPids, self.__macGameRunning)
            return

        path = getTasklistPath()

        if not path:
            signalBus.tasklistNotFound.emit()
            return

        self.__loop(lambda: getLolClientPids(path), lambda: isLolGameProcessExist(path))

    def __macClientPids(self):
        self._lastNames = self._names.scan()
        return sorted(pid for pid, name in self._lastNames.items() if name in CLIENT_NAMES)

    def __macGameRunning(self):
        # the names of the scan __macClientPids just made, in the same poll
        return MAC_GAME_NAME in self._lastNames.values()

    def __clientStillRunning(self):
        """True when the connected client (runningPid) is still the same running process.
        The poll then has nothing to do: with that pid among the client pids, it neither
        starts, changes nor ends a client. Costs microseconds, where a poll runs tasklist
        (Windows) or scans every process (macOS)"""
        pid = self.runningPid
        if not pid:
            return False
        if self._client is None or self._client.pid != pid:
            self._client = clientProcess(pid)
        return self._client is not None and self._client.is_running()

    def __loop(self, clientPids, gameRunning):
        """clientPids() -> pids of the running clients; gameRunning() -> whether the game
        process runs (asked after clientPids, in the same poll)"""
        firstPass = True
        while True:
            try:
                if not self.__clientStillRunning():
                    self.__poll(clientPids, gameRunning, firstPass)
                    firstPass = False
            except Exception as e:
                self.__iterationFailed(e)

            self.msleep(POLL_MS)

    def __poll(self, clientPids, gameRunning, firstPass):
        # 取一下当前运行中的所有客户端 pid
        pids = clientPids()

        # no client AND no game (the client may be closed during a game)
        if firstPass and len(pids) == 0 and not gameRunning():
            signalBus.lolClientAbsent.emit()

        # 如果有客户端正在运行
        if len(pids) != 0:

            # 如果当前没有连接客户端，则是第一个客户端启动了
            if self.runningPid == 0:
                self.runningPid = pids[0]
                signalBus.lolClientStarted.emit(self.runningPid)

            # 如果当前有客户端启动中，但是当前连接的客户端不在这些客户端里
            # 则说明是原来多开了客户端，现在原本连接的客户端关了，则切换到新的客户端
            elif self.runningPid not in pids:
                self.runningPid = pids[0]
                signalBus.lolClientChanged.emit(self.runningPid)

        # 如果没有任何客户端在运行，且上一次检查时有客户端在运行
        else:
            if self.runningPid and not gameRunning():
                self.runningPid = 0
                signalBus.lolClientEnded.emit()


class StoppableThread(QThread):
    def __init__(self, target, parent) -> None:
        self.target = target
        super().__init__(parent)

    def run(self):
        self.target()
