import subprocess
import logging
import json

import asyncio
from PySide6.QtCore import QThread, Signal
import aiohttp

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

        super().__init__(parent)

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

        while True:
            # 取一下当前运行中的所有客户端 pid
            pids = getLolClientPids(path)

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

            self.msleep(1500)

    def __runMac(self):
        # 与 Windows 循环行为等价, 但用 psutil 替代 tasklist:
        #  - getLolClientPidsSlowly() 匹配 LeagueClientUx (无 .exe) 做 started/changed;
        #  - isLolGameProcessExistMac() 匹配 "League of Legends" (无 .exe) 做 ended。
        while True:
            pids = getLolClientPidsSlowly()

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

            self.msleep(1500)


class StoppableThread(QThread):
    def __init__(self, target, parent) -> None:
        self.target = target
        super().__init__(parent)

    def run(self):
        self.target()
