import time

from PySide6.QtCore import QThread

from app.common.logger import logger
from app.common.signals import signalBus
from app.common.util import (getLolClientPids, getLolClientPidsSlowly,
                             isLolGameProcessExist, isLolGameProcessExistMac,
                             getTasklistPath)
from app.common import platform

TAG = "Listener"


class LolProcessExistenceListener(QThread):
    def __init__(self, parent):
        # 当前 Seraphine 连接的客户端 pid
        self.runningPid = 0
        # failed polls (see __iterationFailed): how many since the last log line, and when
        self._failures = 0
        self._lastFailureLog = None

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
            self.__runMac()
            return

        path = getTasklistPath()

        if not path:
            signalBus.tasklistNotFound.emit()
            return

        firstPass = True
        while True:
            try:
                # 取一下当前运行中的所有客户端 pid
                pids = getLolClientPids(path)

                # no client AND no game (the client may be closed during a game)
                if firstPass and len(pids) == 0 and not isLolGameProcessExist(path):
                    signalBus.lolClientAbsent.emit()
                firstPass = False

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
                    if self.runningPid and not isLolGameProcessExist(path):
                        self.runningPid = 0
                        signalBus.lolClientEnded.emit()
            except Exception as e:
                self.__iterationFailed(e)

            self.msleep(1500)

    def __runMac(self):
        # 与 Windows 循环行为等价, 但用 psutil 替代 tasklist:
        #  - getLolClientPidsSlowly() 匹配 LeagueClientUx (无 .exe) 做 started/changed;
        #  - isLolGameProcessExistMac() 匹配 "League of Legends" (无 .exe) 做 ended。
        firstPass = True
        while True:
            try:
                pids = getLolClientPidsSlowly()

                # no client AND no game (the client may be closed during a game)
                if firstPass and len(pids) == 0 and not isLolGameProcessExistMac():
                    signalBus.lolClientAbsent.emit()
                firstPass = False

                if len(pids) != 0:
                    if self.runningPid == 0:
                        self.runningPid = pids[0]
                        signalBus.lolClientStarted.emit(self.runningPid)

                    elif self.runningPid not in pids:
                        self.runningPid = pids[0]
                        signalBus.lolClientChanged.emit(self.runningPid)

                else:
                    if self.runningPid and not isLolGameProcessExistMac():
                        self.runningPid = 0
                        signalBus.lolClientEnded.emit()
            except Exception as e:
                self.__iterationFailed(e)

            self.msleep(1500)


class StoppableThread(QThread):
    def __init__(self, target, parent) -> None:
        self.target = target
        super().__init__(parent)

    def run(self):
        self.target()
