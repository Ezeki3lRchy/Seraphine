import os
import sys
import subprocess
import time
import copy
import webbrowser
from pathlib import Path

import pyperclip

import asyncio
from qasync import asyncClose, asyncSlot
from PySide6.QtCore import Qt, Signal, QSize, QEvent, QTimer, QRect
from PySide6.QtGui import QIcon, QImage, QGuiApplication
from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QWidget, QHBoxLayout

from app.common.qfluentwidgets import (NavigationItemPosition, InfoBar, InfoBarPosition, Action,
                                       FluentWindow, SplashScreen, MessageBox, SmoothScrollArea,
                                       ToolTipFilter, FluentIcon, ToolTipPosition, SystemThemeListener, qconfig, PushButton,
                                       PrimaryPushButton)
from qfluentwidgets.common.style_sheet import updateStyleSheet

from app.view.start_interface import StartInterface
from app.view.setting_interface import SettingInterface
from app.view.career_interface import CareerInterface
from app.view.search_interface import SearchInterface
from app.view.saved_players_interface import SavedPlayersInterface
from app.view.game_info_interface import GameInfoInterface
from app.view.auxiliary_interface import AuxiliaryInterface
from app.view.opgg_window import OpggWindow
from app.common.util import (github, getLolClientPid, getTasklistPath,
                             getLolClientPidSlowly, getLoLPathByRegistry,
                             getLoLPathByScan, checkUpdateOutcome)
from app.common import platform
from app.components.avatar_widget import NavigationAvatarWidget
from app.components.temp_system_tray_menu import TmpSystemTrayMenu
from app.common.icons import Icon
from app.common.config import cfg, VERSION, BETA, GITHUB_URL
from app.common.logger import logger
from app.common.signals import signalBus
from app.common import error_policy
from app.common.error_policy import ErrorPolicy
from app.components.message_box import (UpdateMessageBox, NoticeMessageBox,
                                        WaitingForLolMessageBox, ExceptionMessageBox,
                                        ChangeDpiMessageBox, isPackaged)
from app.common.update_stage import (BackgroundUpdater, GAME_PHASES, stagedUpdateAsync,
                                     stagedFor, applyStagedUpdate, markerApplicable,
                                     isNewer, hasDigest, installWritable, releaseVersion)
from app.common.update_progress import Throttle, progressPayload, progressText
from app.lol.exceptions import (SummonerGamesNotFound, RetryMaximumAttempts,
                                SummonerNotFound, SummonerNotInGame, SummonerRankInfoNotFound)
from app.lol.listener import (LolProcessExistenceListener, StoppableThread)
from app.lol.connector import connector, errorSummary, isReported
from app.lol.tools import (parseAllyGameInfo, parseLobbyGameInfo, parseGameInfoByGameflowSession,
                           getAllyOrderByGameRole, getTeamColor, autoBan, autoPick,
                           autoComplete, autoSwap, autoTrade, ChampionSelection,
                           SERVERS_NAME, SERVERS_SUBSET, showOpggBuild, autoShow,
                           parseEnemyChampSelectInfo, visibleEnemies,
                           lobbyHistoryQueueFilter, splitCustomLobbyTeams)
from app.lol.aram import AramBuff
from app.lol.champions import ChampionAlias
from app.lol.opgg import opgg
from app.lol.live_client import liveClientPoller
from app.components.gold_overlay import GoldOverlay
from app.components.ledger_panel import LedgerPanel
from app.lol.ledger_data import LedgerDatabase, LedgerDataError

import threading

TAG = "MainWindow"

# Gameflow phases in which the lobby page may be (re)painted: the party is still the
# lobby while queueing; from champ select on its own flows own the game info page
LOBBY_REFRESH_STATUSES = ('Lobby', 'Matchmaking', 'ReadyCheck')


def itemFullPrice(itemId):
    """Full gold price of an item from the client's items.json (priceTotal), or None before
    the connector has loaded it or for an unknown id; the live item value falls back to the
    game's own (combine-cost) price then"""
    manager = getattr(connector, 'manager', None)
    info = manager.getItemInfo(itemId) if manager is not None else None
    return info.get('priceTotal') if info else None


liveClientPoller.fullPrice = itemFullPrice


class MainWindow(FluentWindow):
    mainWindowHide = Signal(bool)
    showUpdateMessageBox = Signal(dict)
    showNoticeMessageBox = Signal(str)
    checkUpdateFailed = Signal()
    showUpToDateInfo = Signal()
    fetchNoticeFailed = Signal()
    # Routes exceptHook's dialog to the GUI thread (see exceptHook / __onShowExceptionMessageBox).
    showExceptionMessageBox = Signal(str)
    # Same routing for the non-fatal outcomes: (error_policy action, reason, text)
    showExceptionNotice = Signal(str, str, str)

    def __init__(self):
        super().__init__()

        logger.critical(f"Seraphine started, version: {BETA or VERSION}", TAG)

        self.windowSize = cfg.get(cfg.windowSize)

        self.__initConfig()
        self.__initWindow()
        self.__initSystemTray()

        # "跟随系统" 时, Windows 切换深浅色后即时跟随 (不必重启)
        self.themeListener = SystemThemeListener(self)
        self.themeListener.systemThemeChanged.connect(self.__onSystemThemeChanged)
        self.themeListener.start()

        # create sub interface
        self.startInterface = StartInterface(self)
        self.careerInterface = CareerInterface(self)
        self.searchInterface = SearchInterface(self)
        self.savedPlayersInterface = SavedPlayersInterface(self)
        self.gameInfoInterface = GameInfoInterface(self)
        self.auxiliaryFuncInterface = AuxiliaryInterface(self)
        self.settingInterface = SettingInterface(self)

        logger.critical("Seraphine interfaces initialized", TAG)

        # create listener
        self.isClientProcessRunning = False
        # pid -> consecutive failed start-ups of that client (see __onClientStartFailed)
        self._clientStartFailures = {}
        self.processListener = LolProcessExistenceListener(self)
        self.checkUpdateThread = StoppableThread(
            target=self.checkUpdate, parent=self)
        self.manualCheckThread = None
        self.manualCheckBusy = False
        self.updateBoxOpen = False
        self.checkUpdateLock = threading.Lock()
        # Background download + "ready, restart now" prompt (UI thread only)
        self.backgroundUpdater = BackgroundUpdater(
            self.__onUpdateReady, onStarting=self.__onUpdateStaging,
            onProgress=self.__onBackgroundProgress, resumable=True)
        self._updateProgressThrottle = Throttle()
        # closable bar showing a background download; closing it does not cancel
        self._updateProgressBar = None
        self._updateProgressDismissed = None  # version whose bar the user closed
        self._lastReadyMarker = None  # last update offered as ready (card's restart)
        self._updateReadyBar = None
        self._updateReadyMarker = None
        self._updateReadyButton = None  # (button, its normal text)
        self._applyingUpdate = False
        self.checkNoticeThread = StoppableThread(
            target=lambda: self.checkNotice(False), parent=self)

        logger.critical("Seraphine listerners started", TAG)

        self.currentSummoner = None

        self.isGaming = False
        self.isTrayExit = False
        self.tasklistEnabled = True
        self.championSelection = ChampionSelection()

        self.lastTipsTime = time.time()
        self.lastTipsType = None

        self.__initInterface()
        self.__initNavigation()
        self.__initListener()
        self.__conncetSignalToSlot()
        self.__autoStartLolClient()

        self.splashScreen.finish()

        self.opggWindow = OpggWindow()
        self.goldOverlay = GoldOverlay()
        self._ledgerTasks = set()
        self.ledgerDb, self.ledgerPanel = self.__createLedger()

        logger.critical("Seraphine initialized", TAG)
        # From here an uncaught exception no longer ends the session (see exceptHook)
        self.errorPolicy.finishStartup()

        QTimer.singleShot(0, self.__offerStagedUpdate)

        self.__silentStart()

    def __createLedger(self):
        """The cooldown ledger's database (bundled baseline + cache + the user's priorities,
        files only: no request at start-up) and its panel, which shows itself only while
        cfg.enableCooldownLedger is on. (None, None) when the baseline cannot be read"""
        try:
            db = LedgerDatabase().load()
        except LedgerDataError as e:
            logger.error(f"ledger db unavailable ({type(e).__name__}); no cooldown ledger", TAG)
            return None, None
        except Exception as e:  # start-up: the error policy is not armed yet
            logger.error(f"ledger db failed ({type(e).__name__}); no cooldown ledger", TAG)
            return None, None
        try:
            logger.info(f"ledger db: baseline {db.baselinePatch}, cache {db.cachePatch or 'none'}, "
                        f"{len(db.data['champions'])} champions", TAG)
            panel = LedgerPanel(db, spellIcon=connector.getSummonerSpellIcon,
                                itemIcon=connector.getItemIcon,
                                spellKnown=lambda i: i in connector.manager.spells)
            panel.enemiesSeen.connect(self.__onLedgerEnemiesSeen)
        except Exception as e:
            logger.error(f"ledger panel failed ({type(e).__name__}); no cooldown ledger", TAG)
            return None, None
        return db, panel

    def __onLedgerEnemiesSeen(self, enemies):
        """A game's enemies passed the panel's gate: fetch the skill priorities the cache
        lacks from OP.GG (GET only, the app's OP.GG session) in a background task; the panel
        keeps using the baseline / cache meanwhile and picks the result up on its next use"""
        if self.ledgerDb is None or not cfg.get(cfg.enableCooldownLedger):
            return
        session = opgg.session
        if session is None or session.closed:  # no client session: try in a later game
            return
        task = asyncio.ensure_future(self.ledgerDb.refresh(opgg.getChampionBuild, enemies))
        self._ledgerTasks.add(task)
        task.add_done_callback(self._ledgerTasks.discard)

    async def __cancelLedgerRefresh(self):
        """Cancel the running ledger refresh tasks and wait until they have ended (the client
        ended, or shutdown). An OP.GG GET already in flight is not cancelled with them: the
        build fetch is shielded by its alru_cache"""
        tasks = list(self._ledgerTasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def __closeLedger(self):
        """Shutdown: a running refresh is cancelled, the panel stops following the live data
        and the settings"""
        await self.__cancelLedgerRefresh()
        if self.ledgerPanel is not None:
            self.ledgerPanel.disconnectSources()
            self.ledgerPanel.close()

    def __initConfig(self):
        folder = cfg.get(cfg.lolFolder)

        isEmptyList = folder == []
        isEmptyStr = folder == str(Path("").absolute()).replace(
            "\\", "/") or folder == ""

        if isEmptyList or isEmptyStr:
            # Windows: Tencent registry (CN). macOS: scan /Applications for the
            # League of Legends.app bundle. Both return "" when not found.
            path = getLoLPathByScan() if platform.IS_MACOS else getLoLPathByRegistry()

            if not path:
                return

            cfg.set(cfg.lolFolder, [path])
            return

        if type(folder) is str:
            cfg.set(cfg.lolFolder, [folder])

    def __initInterface(self):
        self.__lockInterface()

        self.startInterface.setObjectName("startInterface")
        self.careerInterface.setObjectName("careerInterface")
        self.searchInterface.setObjectName("searchInterface")
        self.savedPlayersInterface.setObjectName("savedPlayersInterface")
        self.gameInfoInterface.setObjectName("gameInfoInterface")
        self.auxiliaryFuncInterface.setObjectName("auxiliaryFuncInterface")
        self.settingInterface.setObjectName("settingInterface")

    def __initNavigation(self):
        pos = NavigationItemPosition.SCROLL

        self.navigationInterface.addSeparator(NavigationItemPosition.TOP)

        self.addSubInterface(
            self.startInterface, Icon.HOME, self.tr("Start"), pos)
        self.addSubInterface(
            self.careerInterface, Icon.PERSON, self.tr("Career"), pos)
        self.addSubInterface(
            self.searchInterface, Icon.SEARCH, self.tr("Search 👀"), pos)
        self.addSubInterface(
            self.savedPlayersInterface, Icon.STAR, self.tr("Saved players"), pos)
        self.addSubInterface(
            self.gameInfoInterface, Icon.GAME, self.tr("Game Information"), pos)
        self.addSubInterface(
            self.auxiliaryFuncInterface, Icon.WRENCH,
            self.tr("Auxiliary Functions"), pos)

        pos = NavigationItemPosition.BOTTOM

        self.navigationInterface.addItem(
            routeKey='Opgg',
            icon=QIcon("app/resource/images/opgg.svg"),
            text="OP.GG",
            onClick=self.showOpggWindow,
            selectable=False,
            position=pos,
            tooltip="OP.GG"
        )

        self.navigationInterface.addItem(
            routeKey='Fix',
            icon=Icon.ARROWCIRCLE,
            text=self.tr("Back to Lobby"),
            onClick=self.__onFixLCUButtonClicked,
            selectable=False,
            position=pos,
            tooltip=self.tr("Back to Lobby"),
        )

        self.navigationInterface.addItem(
            routeKey='Notice',
            icon=Icon.ALERT,
            text=self.tr("Notice"),
            onClick=lambda: threading.Thread(
                target=lambda: self.checkNotice(True)).start(),
            selectable=False,
            position=pos,
            tooltip=self.tr("Notice"),
        )

        self.navigationInterface.insertSeparator(
            3, NavigationItemPosition.BOTTOM)

        self.avatarWidget = NavigationAvatarWidget(
            avatar="app/resource/images/game.png", name=self.tr("Start LOL"))
        self.navigationInterface.addWidget(
            routeKey="avatar",
            widget=self.avatarWidget,
            onClick=self.__onAvatarWidgetClicked,
            position=pos,
        )

        self.addSubInterface(
            self.settingInterface, FluentIcon.SETTING,
            self.tr("Settings"), pos,
        )

        # set the maximum width
        self.navigationInterface.setExpandWidth(250)
        self.navigationInterface.setMinimumExpandWidth(1321)

    def __conncetSignalToSlot(self):
        # From listener:
        signalBus.tasklistNotFound.connect(self.__showWaitingMessageBox)
        signalBus.lolClientStarted.connect(self.__onLolClientStarted)
        signalBus.lolClientEnded.connect(self.__onLolClientEnded)
        signalBus.lolClientChanged.connect(self.__onLolClientChanged)
        signalBus.lolClientAbsent.connect(self.__onLolClientAbsent)
        signalBus.terminateListeners.connect(self.__terminateListeners)

        # From connector
        signalBus.currentSummonerProfileChanged.connect(
            self.__onCurrentSummonerProfileChanged)
        signalBus.gameStatusChanged.connect(
            self.__onGameStatusChanged)
        signalBus.champSelectChanged.connect(
            self.__onChampSelectChanged)

        # 组队大厅成员战绩: puuid 缓存 (只查新成员) + 防抖 (合并连续成员变动)
        self._lobbyGameInfoCache = {}
        # 缓存里的战绩是按哪个队列过滤的 (见 historyQueueFilter); 大厅换模式后要重查
        self._lobbyQueueFilter = None
        # The enemy side shows the other team of a custom lobby (cleared when it empties)
        self._lobbyEnemyShown = False
        # Bumped by every lobby refresh; only the newest refresh may paint
        self._lobbyRefreshSeq = 0
        # Last gameflow status seen by __onGameStatusChanged (None until the first one)
        self._gameStatus = None
        # Bumped by every __onGameStatusChanged: events run as concurrent tasks, and after
        # an await only the newest may commit isGaming / the title / the champ select start
        self._gameStatusSeq = 0
        # 这次选人的队列, 己方 / 敌方战绩都按它过滤
        self._champSelectQueueId = None

        # 选人阶段揭示隐藏队友: 队友是陆续进入选人聊天房的, 开局那一次只能拿到一部分,
        # 之后在选人事件里节流地重读名单, 名单变长就用缓存重新解析 (只为新队友发请求)
        self._allyGameInfoCache = {}
        self._champSelectEpoch = 0
        self._champSelectReady = False
        self._revealRefreshing = False
        self._lastRevealRefresh = 0.0
        self._revealParticipantCount = 0

        # 选人阶段的敌方战绩: 只有能看到敌方身份的模式 (自定义 / 锦标赛征召等) 才有;
        # 排位里敌方被隐藏, 仍在进游戏 (GameStart) 后查
        self._enemyGameInfoCache = {}
        self._enemyKey = None
        self._enemyLoading = False
        self._lobbyRefreshTimer = QTimer(self)
        self._lobbyRefreshTimer.setSingleShot(True)
        self._lobbyRefreshTimer.setInterval(800)
        self._lobbyRefreshTimer.timeout.connect(self.__doLobbyRefresh)
        signalBus.lobbyChanged.connect(self.__onLobbyChanged)

        signalBus.lcuApiExceptionRaised.connect(
            self.__onShowLcuConnectError)
        signalBus.getCmdlineError.connect(
            self.__showNeedAdminMessageBox)

        # From career_interface
        signalBus.careerGameBarClicked.connect(self.__onCareerGameClicked)

        # From game_info_interface
        signalBus.gameInfoBarClicked.connect(self.__onGameInfoBarClicked)

        # From search_interface and gameinfo_interface
        signalBus.toSearchInterface.connect(self.__switchToSearchInterface)
        signalBus.toCareerInterface.connect(self.__switchToCareerInterface)

        # From setting_interface
        self.settingInterface.careerGamesCount.pushButton.clicked.connect(
            self.__refreshCareerInterface)
        self.settingInterface.micaCard.checkedChanged.connect(
            self.__cascadeSetMicaEffect)

        # From main_window
        self.showUpdateMessageBox.connect(self.__onUpdateAvailable)
        self.showNoticeMessageBox.connect(self.__onShowNoticeMessageBox)
        self.checkUpdateFailed.connect(self.__onCheckUpdateFailed)
        self.showUpToDateInfo.connect(self.__onShowUpToDateInfo)
        signalBus.checkUpdateRequested.connect(self.__onCheckUpdateRequested)
        signalBus.checkUpdateFinished.connect(self.__onManualCheckFinished)
        signalBus.updateProgressChanged.connect(self.__onUpdateProgressChanged)
        signalBus.restartToUpdateRequested.connect(self.__onRestartToUpdateRequested)
        self.fetchNoticeFailed.connect(self.__onFetchNoticeFailed)
        self.showExceptionMessageBox.connect(self.__onShowExceptionMessageBox)
        self.showExceptionNotice.connect(self.__onShowExceptionNotice)
        self.stackedWidget.currentChanged.connect(
            self.__onCurrentStackedChanged)
        self.mainWindowHide.connect(self.__onWindowHide)

    def __initWindow(self):
        self.setMinimumSize(1134, 826)
        self.setWindowIcon(QIcon("app/resource/images/logo.png"))
        self.setWindowTitle("Seraphine")

        self.titleBar.titleLabel.setStyleSheet(
            "QLabel {font: 13px 'Segoe UI', 'Microsoft YaHei';}")

        if platform.IS_MACOS:
            # macOS: use native top-left traffic lights instead of the Fluent
            # top-right min/max/close buttons. The frameless base
            # (MacFramelessWindowBase) draws the real system buttons; we hide the
            # Fluent custom button group and let the native ones show. Their
            # position is set top-left via the systemTitleBarRect override below.
            self.titleBar.minBtn.hide()
            self.titleBar.maxBtn.hide()
            self.titleBar.closeBtn.hide()
            self.setSystemTitleBarButtonVisible(True)

            # Inset the icon/title so they clear the native traffic lights, which
            # occupy x in [8, 83] (systemTitleBarRect origin 8 + width 75). Without
            # this the logo and title would render on top of the buttons.
            self.titleBar.hBoxLayout.insertSpacing(0, 92)

            # The navigation rail's return/menu buttons sit at the very top of the
            # panel (y~5), so the back button collides with the traffic lights too.
            # Push the nav top-section below the title bar (48px) so it clears them.
            self.navigationInterface.panel.vBoxLayout.setContentsMargins(
                0, self.titleBar.height(), 0, 5)
        else:
            self.titleBar.hBoxLayout.insertSpacing(0, 10)

        self.setMicaEffectEnabled(cfg.get(cfg.micaEnabled))

        self.splashScreen = SplashScreen(self.windowIcon(), self)
        self.splashScreen.setIconSize(QSize(106, 106))
        self.splashScreen.raise_()

        desktop = QGuiApplication.primaryScreen().availableGeometry()
        w, h = desktop.width(), desktop.height()
        self.move(w // 2 - self.width() // 2, h // 2 - self.height() // 2)

        self.show()

        QApplication.processEvents()

        self.oldHook = sys.excepthook
        # connector's @retry marks the exception it already showed as "LCU request error"
        self.errorPolicy = ErrorPolicy(alreadyReported=isReported)
        sys.excepthook = self.exceptHook

    def systemTitleBarRect(self, size: QSize) -> QRect:
        """ macOS-only: place the native traffic lights top-left.

        The Fluent base returns ``QRect(size.width() - 75, ...)`` (top-right) for
        the system button area. On macOS we override it to the top-left corner so
        the native min/max/close buttons render where macOS users expect them.
        Only invoked by MacFramelessWindowBase when the system buttons are visible
        (macOS); the Windows path never calls this, so it is left unchanged there.
        """
        if platform.IS_MACOS:
            return QRect(8, 0 if self.isFullScreen() else 8, 75, size.height())

        return super().systemTitleBarRect(size)

    @asyncSlot(str, BaseException)
    async def __onShowLcuConnectError(self, api, obj):
        # 同类错误限制弹出频率(1.5秒每次)
        if time.time() - self.lastTipsTime < 1.5 and self.lastTipsType is type(obj):
            return
        else:
            self.lastTipsTime = time.time()
            self.lastTipsType = type(obj)

        if type(obj) in [SummonerGamesNotFound, SummonerRankInfoNotFound]:
            msg = self.tr(
                "The server returned abnormal content, which may be under maintenance.")
        elif type(obj) is RetryMaximumAttempts:
            msg = self.tr("Exceeded maximum retry attempts.")
        elif type(obj) in [SummonerNotFound, SummonerNotInGame]:
            return
        else:
            # not repr(obj): an aiohttp error's repr carries the request headers (token)
            msg = errorSummary(obj)

        InfoBar.error(
            self.tr("LCU request error"),
            self.tr("Connect API") + f" {api}: {msg}",
            duration=5000,
            orient=Qt.Vertical,
            parent=self,
            position=InfoBarPosition.BOTTOM_RIGHT
        )

    def __onWindowHide(self, hide):
        """

        @param hide: True -> 隐藏, False -> 显示
        @return:
        """
        if hide:
            self.hide()
        else:
            self.showNormal()
            self.activateWindow()

    def checkUpdate(self):
        if not cfg.get(cfg.enableCheckUpdate):
            return

        try:
            with self.checkUpdateLock:
                releasesInfo = github.checkUpdate()
        except:
            self.checkUpdateFailed.emit()
            return

        if releasesInfo:
            self.showUpdateMessageBox.emit(releasesInfo)

    def __onCheckUpdateRequested(self):
        # Manual check ignores enableCheckUpdate. The busy flag lives on the UI
        # thread; an ignored request keeps the button disabled until the running
        # check emits checkUpdateFinished, which re-enables it.
        if self.manualCheckBusy:
            return

        self.manualCheckBusy = True
        thread = StoppableThread(target=self.manualCheckUpdate, parent=self)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(
            lambda t=thread: self.__onManualThreadFinished(t))
        self.manualCheckThread = thread
        thread.start()

    def __onManualThreadFinished(self, thread):
        if self.manualCheckThread is thread:
            self.manualCheckThread = None

    def __onManualCheckFinished(self):
        self.manualCheckBusy = False

    def manualCheckUpdate(self):
        try:
            # Serialise with the start-up check so GitHub is not hit concurrently
            status, info = checkUpdateOutcome(
                lock=self.checkUpdateLock, lockTimeout=15)

            if status == "update":
                self.showUpdateMessageBox.emit(info)
            elif status == "latest":
                self.showUpToDateInfo.emit()
            else:
                self.checkUpdateFailed.emit()
        except Exception as e:
            logger.exception("manual update check crashed", e, TAG)
            self.checkUpdateFailed.emit()
        finally:
            signalBus.checkUpdateFinished.emit()

    def checkNotice(self, triggerByUser):
        try:
            noticeInfo = github.getNotice()
            sha = noticeInfo['sha']
            content = noticeInfo['content']
        except:
            self.fetchNoticeFailed.emit()
            return

        # 如果是开启软件时，并且该公告曾经已经展示过，就直接 return 了
        if not triggerByUser and sha == cfg.get(cfg.lastNoticeSha):
            return

        cfg.set(cfg.lastNoticeSha, sha)
        self.showNoticeMessageBox.emit(content)

    def __onCheckUpdateFailed(self):
        InfoBar.warning(
            self.tr("Check Update Failed"),
            self.tr(
                "Failed to check for updates, possibly unable to connect to Github."),
            duration=5000,
            orient=Qt.Vertical,
            parent=self,
            position=InfoBarPosition.BOTTOM_RIGHT
        )

    def __onShowUpToDateInfo(self):
        # nothing newer: the settings card shows its normal text again
        signalBus.updateProgressChanged.emit(progressPayload('idle', BETA or VERSION))
        InfoBar.success(
            self.tr("Up to Date"),
            self.tr("You are using the latest version ({}).").format(
                BETA or VERSION),
            duration=5000,
            orient=Qt.Vertical,
            parent=self,
            position=InfoBarPosition.BOTTOM_RIGHT
        )

    def __onFetchNoticeFailed(self):
        InfoBar.warning(
            self.tr("Fetch notice Failed"),
            self.tr(
                "Failed to fetch notice, possibly unable to connect to Github."),
            duration=5000,
            orient=Qt.Vertical,
            parent=self,
            position=InfoBarPosition.BOTTOM_RIGHT
        )

    def __onShowUpdateMessageBox(self, info):
        # exec() runs a nested event loop; don't open a second dialog from it
        if self.updateBoxOpen:
            return

        self.updateBoxOpen = True
        try:
            msgBox = UpdateMessageBox(info, self.window())
            msgBox.exec()
        finally:
            self.updateBoxOpen = False

    def __backgroundUpdateEligible(self, info):
        # Silent path only for a strictly newer release with a sha256 digest, in a
        # packaged build that can write its install dir; everything else -> dialog
        return bool(cfg.get(cfg.autoDownloadUpdate) and isPackaged()
                    and not info.get("forbidden") and isNewer(info, VERSION)
                    and hasDigest(info) and installWritable())

    def __onUpdateAvailable(self, info):
        if not self.__backgroundUpdateEligible(info):
            self.__onShowUpdateMessageBox(info)
            return

        # An earlier restore could not be completed (restore-failed/8): every run
        # would stop the same way, so keep pointing at the manual download (the
        # same bar as at start-up) instead of offering this release as ready
        # (only for a release not newer than the failed one; a newer one gets a run)
        from app.common.update_stage import failedRepair
        blocked = failedRepair(releaseVersion(info))
        if blocked is not None:
            self.backgroundUpdater.notifyReady(blocked)
            return

        # A manual "Check now" says something while the download runs / waits: the
        # progress bar, shown again even if it was closed before
        if self.manualCheckBusy and stagedFor(info) is None:
            version = releaseVersion(info)
            self._updateProgressDismissed = None
            state = 'downloading' if self.backgroundUpdater.gaming is False else 'waiting'
            self.__showUpdateProgressBar(version, progressText(state, version), state)

        self.backgroundUpdater.offer(info)

    @asyncSlot()
    async def __offerStagedUpdate(self):
        # Start-up: an update staged by an earlier run (or one whose updater run
        # failed) is offered at once. Clean-up of stale data runs off the UI thread.
        if not isPackaged():
            return
        try:
            marker = await stagedUpdateAsync(VERSION)
        except Exception as e:
            logger.exception("reading staged update failed", e, TAG)
            return
        # switch off: keep a ready stage for the dialog to reuse and offer nothing,
        # but always say when the last update run did not complete
        if marker and (cfg.get(cfg.autoDownloadUpdate) or marker.get("lastResult")):
            self.backgroundUpdater.notifyReady(marker)

    def __onLolClientAbsent(self):
        # no LoL client at start-up: known not to be in a game
        if not self.isClientProcessRunning:
            self.__syncUpdaterGameStatus(None)

    def __onUpdateStaging(self, info):
        if stagedFor(info) is not None:
            return  # already staged: no download, the ready bar follows
        # A different release is about to replace the stage: the old bar is stale
        if self._updateReadyBar is not None:
            self.__closeUpdateReadyBar()
        version = releaseVersion(info)
        self.__showUpdateProgressBar(version, progressText('downloading', version))

    def __onBackgroundProgress(self, state, version, cur, total, kind):
        # BackgroundUpdater.onProgress: at most a few UI updates per second
        if not self._updateProgressThrottle.allow(state, version, cur, total):
            return
        reason = self.backgroundUpdater.lastError if state == 'failed' else None
        signalBus.updateProgressChanged.emit(
            progressPayload(state, version, cur, total, kind, reason, source='background'))

    def __showUpdateProgressBar(self, version, text, state='downloading'):
        title = self.tr("Update paused") if state == 'waiting' else self.tr("Downloading update")
        bar = self._updateProgressBar
        if bar is not None:
            bar.updateVersion = version  # what closing it dismisses
            # InfoBar has no setters: title / content are re-wrapped by its
            # protected _adjustText (the bar may already be deleted by Qt)
            bar.title, bar.content = title, text
            try:
                bar._adjustText()
            except RuntimeError:
                self._updateProgressBar = None
            return
        if self._updateProgressDismissed == version:
            return
        bar = InfoBar.info(
            title,
            text,
            duration=-1,
            orient=Qt.Vertical,
            isClosable=True,
            parent=self,
            position=InfoBarPosition.BOTTOM_RIGHT
        )
        bar.updateVersion = version
        bar.closedSignal.connect(lambda b=bar: self.__onUpdateProgressBarClosed(b))
        self._updateProgressBar = bar

    def __onUpdateProgressBarClosed(self, bar):
        # closed by the user (a programmatic close clears the reference first):
        # the download goes on; the bar is not shown again for this version
        if self._updateProgressBar is bar:
            self._updateProgressBar = None
            self._updateProgressDismissed = bar.updateVersion

    def __closeUpdateProgressBar(self):
        bar, self._updateProgressBar = self._updateProgressBar, None
        if bar is not None:
            try:
                bar.close()
            except RuntimeError:  # already deleted
                pass

    def __onUpdateProgressChanged(self, payload):
        # only the background download has a bar (the dialog shows its own progress)
        if payload.get('source') != 'background':
            return
        state, version = payload['state'], payload['version']
        if state == 'ready':
            self.__closeUpdateProgressBar()  # the ready bar takes over
            return
        text = progressText(state, version, payload['cur'], payload['total'],
                            payload['kind'], payload.get('reason'))
        if state == 'failed':
            self.__closeUpdateProgressBar()
            InfoBar.warning(
                self.tr("Update failed"),
                text,
                duration=8000,
                orient=Qt.Vertical,
                parent=self,
                position=InfoBarPosition.BOTTOM_RIGHT
            )
            return
        if self._updateProgressBar is not None:
            self.__showUpdateProgressBar(version, text, state)

    def __onRestartToUpdateRequested(self):
        # the settings card's "Restart to update": the ready bar's restart, also
        # after that bar was closed with "Later"
        from app.common.update_stage import repairBlocks
        marker = self._lastReadyMarker
        gaming = self.backgroundUpdater.gaming
        if marker and repairBlocks(marker.get('lastResult'), marker.get('version')):
            # a repair that keeps failing is never restarted into: the manual download
            self._lastReadyMarker = None
            signalBus.updateProgressChanged.emit(progressPayload('idle', ''))
            self.__onUpdateReady(marker)
            return
        if gaming is not False:
            InfoBar.info(
                self.tr("Update ready"),
                self.tr("Restart after the game to finish updating.") if gaming
                else self.tr("Restart once the client is ready to finish updating."),
                duration=5000,
                orient=Qt.Vertical,
                parent=self,
                position=InfoBarPosition.BOTTOM_RIGHT
            )
            return
        if not marker or not markerApplicable(marker):
            self._lastReadyMarker = None
            self.__showUpdateError(self.tr(
                "This update is no longer available; please check for updates again."))
            signalBus.updateProgressChanged.emit(progressPayload('idle', ''))
            return
        self.__restartToUpdate(marker)

    def __onUpdateReady(self, marker):
        version = marker.get("version", "")
        if self._updateReadyBar is not None:
            if self._updateReadyMarker == marker:
                return
            self._updateReadyBar.close()

        result = marker.get("lastResult") or {}
        logPath = os.path.join(platform.config_dir(), "updater.log")
        from app.common.update_stage import repairBlocks
        if repairBlocks(result, version):
            # even finishing the earlier rollback failed, or the package twice did not match
            # the install (failed:9, nothing changed): no retry, download by hand
            # (a release newer than the failed one gets the Retry bar below instead)
            bar = InfoBar.error(
                self.tr("Update did not complete"),
                (self.tr("This update does not match your install and could not be applied. "
                         "Please download the latest release manually. See the log: {}")
                 if result.get('status') == 'failed:9' else
                 self.tr("Seraphine could not repair a failed update. Please download the "
                         "latest release manually. See the log: {}")).format(logPath),
                duration=-1,
                orient=Qt.Vertical,
                isClosable=True,
                parent=self,
                position=InfoBarPosition.BOTTOM_RIGHT
            )
            self._lastReadyMarker = None  # nothing to restart into
            downloadButton = PrimaryPushButton(self.tr("Download"), bar)
            downloadButton.clicked.connect(
                lambda: webbrowser.open(GITHUB_URL + "/releases/latest"))
            bar.addWidget(downloadButton)
            bar.closedSignal.connect(lambda b=bar: self.__onUpdateReadyBarClosed(b))
            self._updateReadyBar = bar
            self._updateReadyMarker = marker
            self._updateReadyButton = None
            return

        if result:
            # the failed run may be for an earlier release than the one staged now
            # (a newer release staged after an unfinished restore keeps its result)
            version = result.get("version") or version
            if result.get("status") == "restore-failed":
                content = self.tr("Updating to v{} failed and could not be fully rolled "
                                  "back. See the log: {}").format(version, logPath)
            else:
                content = self.tr("Updating to v{} failed; the previous version was "
                                  "kept. See the log: {}").format(version, logPath)
            bar = InfoBar.warning(
                self.tr("Update did not complete"),
                content,
                duration=-1,
                orient=Qt.Vertical,
                isClosable=True,
                parent=self,
                position=InfoBarPosition.BOTTOM_RIGHT
            )
            restartText = self.tr("Retry")
        else:
            bar = InfoBar.success(
                self.tr("Update ready"),
                self.tr("Seraphine v{} is ready. Restart to finish updating.").format(version),
                duration=-1,
                orient=Qt.Vertical,
                isClosable=True,
                parent=self,
                position=InfoBarPosition.BOTTOM_RIGHT
            )
            restartText = self.tr("Restart now")

        buttons = QWidget(bar)
        layout = QHBoxLayout(buttons)
        layout.setContentsMargins(0, 0, 0, 0)
        restartButton = PrimaryPushButton(restartText, buttons)
        laterButton = PushButton(self.tr("Later"), buttons)
        layout.addWidget(restartButton)
        layout.addWidget(laterButton)
        layout.addStretch(1)
        bar.addWidget(buttons)

        restartButton.clicked.connect(lambda: self.__restartToUpdate(marker))
        laterButton.clicked.connect(bar.close)
        bar.closedSignal.connect(lambda b=bar: self.__onUpdateReadyBarClosed(b))

        self._updateReadyBar = bar
        self._updateReadyMarker = marker
        self._lastReadyMarker = marker
        self._updateReadyButton = (restartButton, restartText)

    def __onUpdateReadyBarClosed(self, bar):
        if self._updateReadyBar is bar:
            self._updateReadyBar = None
            self._updateReadyMarker = None
            self._updateReadyButton = None

    def __setRestartButtonBusy(self, busy):
        # the bar's button shows "Starting the updater..." and is disabled meanwhile
        if not self._updateReadyButton:
            return
        button, text = self._updateReadyButton
        try:
            button.setEnabled(not busy)
            button.setText(self.tr("Starting the updater...") if busy else text)
        except RuntimeError:  # the bar was already deleted
            self._updateReadyButton = None

    def __syncUpdaterGameStatus(self, status):
        # A game is starting: hide the restart prompt until it ends (the updater
        # holds it and offers it again), and pause any download
        if status in GAME_PHASES and self._updateReadyBar is not None:
            marker = self._updateReadyMarker
            bar, self._updateReadyBar, self._updateReadyMarker = \
                self._updateReadyBar, None, None
            bar.close()
            self.backgroundUpdater.heldMarker = marker
        self.backgroundUpdater.setGameStatus(status)

    def __showUpdateError(self, content):
        InfoBar.error(
            self.tr("Update failed"),
            content,
            duration=8000,
            orient=Qt.Vertical,
            parent=self,
            position=InfoBarPosition.BOTTOM_RIGHT
        )

    def __closeUpdateReadyBar(self):
        if self._updateReadyBar is not None:
            bar, self._updateReadyBar, self._updateReadyMarker = \
                self._updateReadyBar, None, None
            self._updateReadyButton = None
            bar.close()

    @asyncSlot()
    async def __restartToUpdate(self, marker):
        # "Restart now" and "Retry": one apply at a time (set before any await)
        if self._applyingUpdate:
            return
        self._applyingUpdate = True
        busy = started = False
        try:
            # Only apply exactly what the bar was created for, with nothing in flight
            if self.backgroundUpdater.isBusy():
                self.__showUpdateError(self.tr(
                    "An update is still being downloaded; you will be asked again when it is ready."))
                self.__closeUpdateReadyBar()
                return
            if not markerApplicable(marker):
                self.__showUpdateError(self.tr(
                    "This update is no longer available; please check for updates again."))
                self.__closeUpdateReadyBar()
                return

            self.__setRestartButtonBusy(True)
            busy = True
            try:
                started = await applyStagedUpdate(marker)
            except Exception as e:
                logger.exception("starting the updater failed", e, TAG)
                started = False

            if not started:
                self.__showUpdateError(self.tr("Could not start the updater. See the log: {}").format(
                    os.path.join(platform.config_dir(), "updater.log")))
                return

            # Same final step as the update dialog: stop the QThreads, then exit
            signalBus.terminateListeners.emit()
            sys.exit()
        finally:
            # also on cancellation (CancelledError is not an Exception)
            if busy and not started:
                self.__setRestartButtonBusy(False)
            self._applyingUpdate = False

    def __onShowNoticeMessageBox(self, msg):
        msgBox = NoticeMessageBox(msg, self.window())
        msgBox.exec()

    def __showWaitingMessageBox(self):
        self.tasklistEnabled = False

        msgBox = WaitingForLolMessageBox(self.window())

        if not msgBox.exec():
            signalBus.terminateListeners.emit()
            sys.exit()

    def __showNeedAdminMessageBox(self):
        msgBox = MessageBox(self.tr("Get cmdline error"), self.tr(
            "Try running Seraphine as an administrator"), self.window())
        msgBox.cancelButton.setVisible(False)
        msgBox.exec()

        signalBus.terminateListeners.emit()
        sys.exit()

    def __initSystemTray(self):
        self.trayIcon = QSystemTrayIcon(self)
        self.trayIcon.setToolTip("Seraphine")
        self.trayIcon.installEventFilter(ToolTipFilter(self.trayIcon))

        self.trayIcon.setIcon(QIcon("app/resource/images/logo.png"))

        careerAction = Action(Icon.PERSON, self.tr("Career"), self)
        searchAction = Action(Icon.SEARCH, self.tr("Search 👀"), self)
        gameInfoAction = Action(Icon.GAME, self.tr("Game Information"), self)
        settingsAction = Action(Icon.SETTING, self.tr("Settings"), self)
        quitAction = Action(Icon.EXIT, self.tr('Quit'), self)

        def showAndSwitch(interface):
            self.show()
            self.checkAndSwitchTo(interface)

        def quit():
            self.isTrayExit = True
            self.close()

        careerAction.triggered.connect(
            lambda: showAndSwitch(self.careerInterface))
        searchAction.triggered.connect(
            lambda: showAndSwitch(self.searchInterface))
        gameInfoAction.triggered.connect(
            lambda: showAndSwitch(self.gameInfoInterface))
        settingsAction.triggered.connect(
            lambda: showAndSwitch(self.settingInterface))
        quitAction.triggered.connect(quit)

        # qfluentwidgets fork's RoundMenu.__init__ signature is (title="",
        # parent=None); pass parent by keyword so the MainWindow isn't taken as
        # the title (would TypeError in setTitle). Was positional in the old dep.
        self.trayMenu = TmpSystemTrayMenu(parent=self)

        self.trayMenu.addAction(careerAction)
        self.trayMenu.addAction(searchAction)
        self.trayMenu.addAction(gameInfoAction)
        self.trayMenu.addSeparator()
        self.trayMenu.addAction(settingsAction)
        self.trayMenu.addAction(quitAction)

        self.trayIcon.setContextMenu(self.trayMenu)
        # 双击事件
        self.trayIcon.activated.connect(lambda reason: self.show(
        ) if reason == QSystemTrayIcon.DoubleClick else None)
        self.trayIcon.show()

    def show(self):
        self.activateWindow()
        self.setWindowState(self.windowState() & ~
                            Qt.WindowMinimized | Qt.WindowActive)
        self.showNormal()

    def __initListener(self):
        self.processListener.start()
        self.checkUpdateThread.start()
        self.checkNoticeThread.start()
        # self.minimizeThread.start()  # 该功能不再支持 -- By Hpero4

    async def __changeCareerToCurrentSummoner(self):
        summoner = await connector.getCurrentSummoner()
        self.currentSummoner = summoner
        self.careerInterface.setLoginSummonerPuuid(summoner['puuid'])

        asyncio.create_task(self.careerInterface.updateInterface(
            summoner=summoner))

    # a client whose start-up sequence failed is retried this many times, this many
    # seconds apart (the process listener re-detects it once runningPid is reset)
    CLIENT_START_RETRIES = 3
    CLIENT_START_RETRY_DELAY = 5

    @asyncSlot(int)
    async def __onLolClientStarted(self, pid):
        # Only the start-up steps (up to the unlock) are guarded: a failure after that,
        # e.g. in the status replay, is an ordinary failure of a connected session
        try:
            started = await self.__startClientSession(pid)
        except asyncio.CancelledError:
            raise
        except BaseException:  # the app's own exceptions (app/lol/exceptions.py) subclass BaseException
            await self.__onClientStartFailed(pid)
            raise  # still reaches the error policy (notice + log)

        self._clientStartFailures.pop(pid, None)
        if started is None:
            return

        championsInit, aramInitT, status, statusSeq = started
        await asyncio.gather(championsInit, aramInitT)
        # The downloads above can be slow: a websocket event that arrived meanwhile is newer
        # than the status fetched before them, and replaying that would supersede it
        if self._gameStatusSeq != statusSeq:
            return

        try:
            await self.__onGameStatusChanged(status)
        except asyncio.CancelledError:
            raise
        except BaseException:  # the app's own exceptions (app/lol/exceptions.py) subclass BaseException
            # The handler is an asyncSlot: its own task already reported this to the error
            # policy, exactly like a live gameflow event's failure; the session stays up
            pass

    async def __onClientStartFailed(self, pid):
        """
        The start-up sequence raised midway: the loading page / locked interface would stay
        up with nothing retrying. Go back to the consistent not-connected state (the same
        teardown as a client that ended) rather than unlocking a half-started session
        (no summoner, no game status). Then let the process listener detect the client
        again, a few times; after that a client restart (new pid) or close still works.
        """
        # per client: a restarted client (new pid) gets its own retries
        failures = self._clientStartFailures[pid] = self._clientStartFailures.get(pid, 0) + 1
        logger.error(f"client start-up failed (attempt {failures})", TAG)

        try:
            await self.__onLolClientEnded()
        except asyncio.CancelledError:
            raise
        except BaseException as e:  # the app's own exceptions (app/lol/exceptions.py) subclass BaseException
            logger.error(f"client start-up failed: teardown failed: {type(e).__name__}", TAG)

        if failures > self.CLIENT_START_RETRIES:
            return

        def retry():
            # still the client that failed (not ended / changed meanwhile): detect it again
            if self.processListener.runningPid == pid:
                self.processListener.runningPid = 0

        asyncio.get_running_loop().call_later(self.CLIENT_START_RETRY_DELAY, retry)

    async def __startClientSession(self, pid):
        """
        The start-up steps, up to unlocking the interface. Returns None when the connector
        did not start (the listener retries), else what __onLolClientStarted finishes with:
        (championsInit, aramInitT, status, statusSeq)
        """
        logger.info(f"League of Legends client started: {pid}", TAG)
        # game state unknown until fetched below: no update download / prompt meanwhile
        self.backgroundUpdater.setGameStatusUnknown()
        res = await self.__startConnector(pid)
        if not res:
            return None

        await opgg.start()
        self.checkAndSwitchTo(self.careerInterface)
        self.isClientProcessRunning = True

        await self.__changeCareerToCurrentSummoner()
        await self.__updateAvatarIconName()

        self.startInterface.hideLoadingPage()

        folder, status = await asyncio.gather(connector.getInstallFolder(),
                                              connector.getGameStatus())
        # the replay below is skipped if a live gameflow event arrived meanwhile
        statusSeq = self._gameStatusSeq
        # as soon as it is known (the full __onGameStatusChanged runs much later)
        self.__syncUpdaterGameStatus(status)

        self.__setLolInstallFolder(folder)

        asyncio.create_task(self.auxiliaryFuncInterface.initChampionList())

        self.auxiliaryFuncInterface.lockConfigCard.loadNowMode()

        # 加载大乱斗buff -- By Hpero4
        aramInitT = asyncio.create_task(AramBuff.checkAndUpdate())
        championsInit = asyncio.create_task(ChampionAlias.checkAndUpdate())

        asyncio.create_task(self.opggWindow.initWindow())
        self.opggWindow.setHomeInterfaceEnabled(False)

        # ---- 240413 ---- By Hpero4
        # 如果你希望 self.__onGameStatusChanged(status) 和 self.__unlockInterface() 并行执行, 可以这样使用:
        #     t = self.__onGameStatusChanged(status)
        #     self.__unlockInterface()
        #     await t
        #
        # 如果你希望等待 self.__onGameStatusChanged(status) 返回之后再执行 self.__unlockInterface() 可以这样使用:
        #     await self.__onGameStatusChanged(status)
        #     self.__unlockInterface()
        #
        # 而不是直接调用:
        #     self.__onGameStatusChanged(status)
        #     self.__unlockInterface()
        #
        # 此外 self.__onGameStatusChanged(status) 本身不是一个常规的异步函数, 它是使用 asyncSlot 装饰的槽函数,
        #   内部封装了task的新建过程, 并且会被立即加入到 QEventLoop 等待执行, 并返回一个Task实例;
        #
        # 如果 func a 是一个常规异步函数, func b 是一个常规的同步函数, 你应该这样使用它:
        #     t = asyncio.create_task(a())
        #     b()
        #     await t
        #
        # 项目中还有其他异步函数使用了await进行了额外的等待, 亦或是直接调用异步函数而没有使用await保证竞态的情况,
        # 这可能导致性能或是其他不可预期的问题, 这是只是一个例子;
        # ---- 240413 ---- By Hpero4

        self.__unlockInterface()
        return championsInit, aramInitT, status, statusSeq

        # Note 如果你希望测试大乱斗的数据弹框, 参考这个 -- By Hpero4
        # self.careerInterface.icon.aramInfo = AramBuff.getInfoByChampionId(75)

    async def __startConnector(self, pid):
        try:
            await connector.start(pid)
            return True
        except RetryMaximumAttempts:
            # 若超出最大尝试次数, 则认为 lcu 未就绪 (如大区排队中),
            # 捕获到该异常时不抛出, 等待下一个 emit
            await connector.close()

            if self.processListener.isRunning():
                self.processListener.runningPid = 0
            else:
                signalBus.tasklistNotFound.emit()

            return False

    @asyncSlot(int)
    async def __onLolClientChanged(self, pid):
        logger.critical(f"League of Legends client changed: {pid}", TAG)
        await self.__onLolClientEnded()
        self.processListener.runningPid = pid
        await self.__onLolClientStarted(pid)

    @asyncSlot()
    async def __onLolClientEnded(self):
        logger.critical("League of Legends client ended", TAG)

        if self.searchInterface.gameLoadingTask:
            self.searchInterface.puuid = 0
            self.searchInterface.gameLoadingTask = None

        liveClientPoller.stop()
        await connector.close()
        await self.__cancelLedgerRefresh()  # before its OP.GG session closes
        await opgg.close()

        self.isClientProcessRunning = False
        self.currentSummoner = None
        self.careerInterface.setLoginSummonerPuuid(None)
        # no client, no game: a paused update download may resume
        self.__syncUpdaterGameStatus(None)

        await self.__updateAvatarIconName()

        self.startInterface.showLoadingPage()
        self.careerInterface.setLoadingPageEnabled(True)
        self.opggWindow.setHomeInterfaceEnabled(True)

        self.setWindowTitle("Seraphine")

        self.checkAndSwitchTo(self.startInterface)
        self.__lockInterface()

    async def __updateAvatarIconName(self):
        if self.currentSummoner:
            try:
                iconId = self.currentSummoner['profileIconId']
                icon = await connector.getProfileIcon(iconId)
                name = (self.currentSummoner.get("gameName")
                        or self.currentSummoner['displayName'])

                server = SERVERS_NAME.get(connector.server) or connector.server
                name += self.tr(" (") + server + self.tr(")")

                subset = SERVERS_SUBSET.get(connector.server)

                if subset and not self.avatarWidget.toolTip():
                    tooltip = self.tr(", ").join(subset)
                    self.avatarWidget.setToolTip(tooltip)
                    self.avatarWidget.installEventFilter(
                        ToolTipFilter(self.avatarWidget, 0,
                                      ToolTipPosition.TOP)
                    )

            except asyncio.CancelledError:
                raise
            except BaseException as e:  # the app's own exceptions (app/lol/exceptions.py) subclass BaseException
                # the type only: an aiohttp error's repr carries the request headers
                logger.warning(f"avatar: profile icon / name failed: {type(e).__name__}", TAG)
                icon = "app/resource/images/game.png"
                name = self.tr("Start LOL")
                self.avatarWidget.setToolTip("")
        else:
            icon = "app/resource/images/game.png"
            name = self.tr("Start LOL")
            self.avatarWidget.setToolTip("")

        self.avatarWidget.avatar = QImage(icon).scaled(
            24, 24, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.avatarWidget.name = name

        self.avatarWidget.repaint()

    def __setLolInstallFolder(self, folder: str):
        folder = folder.replace("\\", "/")
        folder = folder.replace("LeagueClient", "TCLS")
        folder = f"{folder[:1].upper()}{folder[1:]}"

        current: list = cfg.get(cfg.lolFolder)

        if folder.lower() not in [item.lower() for item in current]:
            new = copy.deepcopy(current)
            new.append(folder)
            cfg.set(cfg.lolFolder, new)

    @asyncSlot(dict)
    async def __onCurrentSummonerProfileChanged(self, data: dict):
        self.currentSummoner = data

        await asyncio.gather(self.__updateAvatarIconName(),
                             self.careerInterface.updateNameIconExp(data))

        logger.debug(f"Update Summoner Info : {data}", TAG)

    def __autoStartLolClient(self):
        if self.isClientProcessRunning:
            return

        if not cfg.get(cfg.enableStartLolWithApp):
            return

        if self.tasklistEnabled:
            path = getTasklistPath()
            pid = getLolClientPid(path)
        else:
            pid = getLolClientPidSlowly()

        if pid == 0:
            self.__startLolClient()

    def __startLolClient(self):
        if not platform.IS_WINDOWS:
            # macOS: 没有 client.exe/LeagueClient.exe, 用 `open` 唤起 .app bundle.
            appPath = platform.mac_lol_app_path(cfg.get(cfg.lolFolder)[0])
            if appPath:
                subprocess.Popen(['open', appPath])
                self.__showStartLolSuccessInfo()
            else:
                # 仅当 .app 确实缺失时才报路径错误 (非空跑也不再误报).
                self.__showLolClientPathErrorInfo()
            return

        for clientName in ("client.exe", "LeagueClient.exe"):
            path = f'{cfg.get(cfg.lolFolder)[0]}/{clientName}'
            if os.path.exists(path):
                os.popen(f'"{path}"')
                self.__showStartLolSuccessInfo()
                break
        else:
            self.__showLolClientPathErrorInfo()

    def __onAvatarWidgetClicked(self):
        if not self.isClientProcessRunning:
            self.__startLolClient()
        else:
            self.careerInterface.backToMeButton.clicked.emit()
            self.checkAndSwitchTo(self.careerInterface)

    def __showStartLolSuccessInfo(self):
        InfoBar.success(
            title=self.tr("Start LOL successfully"),
            orient=Qt.Vertical,
            content="",
            isClosable=True,
            position=InfoBarPosition.BOTTOM_RIGHT,
            duration=5000,
            parent=self,
        )

    def __showLolClientPathErrorInfo(self):
        InfoBar.error(
            title=self.tr("Invalid path"),
            content=self.tr(
                "Please set the correct directory of the LOL client in the setting page"),
            orient=Qt.Vertical,
            isClosable=True,
            position=InfoBarPosition.BOTTOM_RIGHT,
            duration=5000,
            parent=self,
        )

    def checkAndSwitchTo(self, interface):
        index = self.stackedWidget.indexOf(interface)

        if not self.stackedWidget.currentIndex() == index:
            self.navigationInterface.widget(interface.objectName()).click()

    def __unlockInterface(self):
        self.searchInterface.setEnabled(True)
        self.auxiliaryFuncInterface.setClientConnected(True)
        # the saved list stays usable offline; only opening a row needs the client
        self.savedPlayersInterface.setClientConnected(True)

    def __lockInterface(self):
        self.searchInterface.setEnabled(False)
        self.auxiliaryFuncInterface.setClientConnected(False)
        self.savedPlayersInterface.setClientConnected(False)

    def __onSystemThemeChanged(self):
        # 监听线程已把 qconfig.theme 切到新的系统主题; 这里重新套用样式表, 并让自绘控件
        # (战绩行 / 段位卡等按主题缓存的内容) 重画
        updateStyleSheet(lazy=True)
        qconfig.themeChangedFinished.emit()
        for window in (self, self.opggWindow):
            for widget in [window] + window.findChildren(QWidget):
                widget.update()

    def __terminateListeners(self):
        self.themeListener.terminate()
        self.processListener.terminate()
        self.checkUpdateThread.terminate()
        if self.manualCheckThread is not None:
            try:
                self.manualCheckThread.terminate()
            except RuntimeError:  # already deleted after finishing
                pass
        self.checkNoticeThread.terminate()

    @asyncClose
    async def closeEvent(self, a0) -> None:
        # 首次点击 关闭 按钮
        if cfg.get(cfg.enableCloseToTray) is None:
            msgBox = MessageBox(
                self.tr("Do you wish to exit?"),
                self.tr(
                    "Choose action for close button (you can modify it at any time in the settings page)"),
                self
            )

            msgBox.yesButton.setText(self.tr('Minimize'))
            msgBox.cancelButton.setText(self.tr('Exit'))
            self.update()

            cfg.set(cfg.enableCloseToTray, msgBox.exec())

        if not cfg.get(cfg.enableCloseToTray) or self.isTrayExit:
            self.__terminateListeners()
            self.opggWindow.close()
            await liveClientPoller.aclose()
            self.goldOverlay.close()
            await self.__closeLedger()

            cfg.set(cfg.windowSize, self.windowSize)

            return super().closeEvent(a0)
        else:
            a0.ignore()
            self.hide()

    def __silentStart(self):
        if not cfg.get(cfg.enableSilent):
            return

        QTimer.singleShot(0, self.hide)

    @asyncSlot(str)
    async def __switchToSearchInterface(self, name):
        self.searchInterface.searchLineEdit.setText(name)
        self.checkAndSwitchTo(self.searchInterface)

        await self.searchInterface.onSearchButtonClicked()

    @asyncSlot(str)
    async def __switchToCareerInterface(self, puuid):
        if puuid == '00000000-0000-0000-0000-000000000000':
            return

        try:
            self.careerInterface.w.close()
        except:
            pass

        self.checkAndSwitchTo(self.careerInterface)
        await self.careerInterface.updateInterface(puuid=puuid)

    @asyncSlot(str)
    async def __onGameStatusChanged(self, status):
        # Each event is its own task (asyncSlot) and branches await: an older, slower one
        # (champ select loading) may finish after a newer one (a dodge back to Lobby).
        # After an await only the newest event may commit state (see current()).
        self._gameStatusSeq += 1
        seq = self._gameStatusSeq

        def current():
            return seq == self._gameStatusSeq

        self._gameStatus = status  # read by __doLobbyRefresh (LOBBY_REFRESH_STATUSES)
        # first, before any await: a starting game must pause the update download
        self.__syncUpdaterGameStatus(status)
        title = None
        isGaming = False

        # 离开选人后不再补揭示隐藏队友 (见 __refreshHiddenAllies), 也作废这次选人里
        # 还在加载的队友 / 敌方结果 (进游戏后由 __onGameStart 重新绘制)
        if status != 'ChampSelect':
            self._champSelectReady = False
            self._champSelectEpoch += 1

        if status == 'None':
            title = self.tr("Home")
            await self.__onGameEnd()
        elif status == 'ChampSelect':
            title = self.tr("Selecting Champions")

            # 在标题添加所处队伍
            side = await connector.getMapSide()
            if side:
                if side == 'blue':
                    mapSide = self.tr("Blue Team")
                else:
                    mapSide = self.tr("Red Team")

                title = title + " - " + mapSide

            # Left champ select while the side loaded: starting it now would re-open the
            # epoch the newer event closed (and show the op.gg window after a dodge)
            if not current():
                return

            await self.__onChampionSelectBegin()
        elif status == 'GameStart':
            title = self.tr("Gaming")
            await self.__onGameStart(current)
            isGaming = True
        elif status == 'InProgress':
            title = self.tr("Gaming")

            # 重连或正常进入游戏 (走 GameStart), 不需要更新数据
            if not self.isGaming:
                await self.__onGameStart(current)
            isGaming = True
        elif status == 'WaitingForStatus':
            title = self.tr("Waiting for status")
        elif status == 'EndOfGame':
            title = self.tr("End of game")
        elif status == 'Lobby':
            title = self.tr("Lobby")
            await self.__onGameEnd()
            await self.careerInterface.refresh()

            # The lobby setup belongs to the lobby phase, not to this event: it still holds
            # after Lobby -> Matchmaking / ReadyCheck, but not once champ select or the game
            # began during the career refresh (dropping enemies would clear their page)
            if self._gameStatus in LOBBY_REFRESH_STATUSES:
                # 进入大厅: 重置缓存, 触发 (防抖) 刷新车队成员战绩
                self._lobbyGameInfoCache = {}
                # A new lobby phase: a custom lobby's other team from before must not linger
                # (the refresh below repaints it if this is still that custom lobby)
                self.__dropLobbyEnemies()
                self._lobbyRefreshTimer.start()

        elif status == 'ReadyCheck':
            title = self.tr("Ready check")
            await self.__onMatchMade()
        elif status == 'Matchmaking':
            title = self.tr("Match making")
            await self.__onGameEnd()
        elif status == "Reconnect":  # 等待重连
            title = self.tr("Waiting reconnect")
            await self.__onReconnect()

        # A newer event owns isGaming, the live poller, op.gg on-top and the title
        if not current():
            return

        self.isGaming = isGaming
        if not isGaming:
            liveClientPoller.stop()  # also hides the item value panel / overlay

        if status != 'ChampSelect':
            self.opggWindow.setStaysOnTopEnabled(False)

        if title != None:
            self.setWindowTitle("Seraphine - " + title)

    async def __onMatchMade(self):
        if not cfg.get(cfg.enableAutoAcceptMatching):
            return

        async def accept():
            timeDelay = cfg.get(cfg.autoAcceptMatchingDelay)
            await asyncio.sleep(timeDelay)
            status = await connector.getReadyCheckStatus()

            if status.get("errorCode"):
                return

            if not status['playerResponse'] == 'Declined':
                await connector.acceptMatchMaking()

        asyncio.create_task(accept())

    async def __onReconnect(self):
        if not cfg.get(cfg.enableAutoReconnect):
            return

        async def reconnect():
            while await connector.getGameStatus() == "Reconnect":
                # 掉线立刻重连会无效
                await asyncio.sleep(.3)
                await connector.reconnect()

        asyncio.create_task(reconnect())

    def __onLobbyChanged(self, event):
        # 大厅成员变动: 防抖触发刷新 (仅查询新成员)
        self._lobbyRefreshTimer.start()

    @asyncSlot()
    async def __doLobbyRefresh(self):
        # Refreshes can overlap (the debounce does not serialise them): only the newest paints
        self._lobbyRefreshSeq += 1
        seq = self._lobbyRefreshSeq

        def current():
            # newest refresh, and still in a lobby phase (not champ select / in game)
            return seq == self._lobbyRefreshSeq and self._gameStatus in LOBBY_REFRESH_STATUSES

        if self.isGaming or not current():
            return

        try:
            lobby = await connector.getLobby()
        except asyncio.CancelledError:
            raise
        except BaseException as e:  # the app's own exceptions (app/lol/exceptions.py) subclass BaseException
            logger.warning(
                f"lobby refresh: getLobby failed: {type(e).__name__}, status={getattr(e, 'status', None)}", TAG)
            return

        if not isinstance(lobby, dict) or not current():
            return

        members = lobby.get('members') or []

        # 有几个显示几个 (含单人); 仅当完全无成员/不在大厅时切回生涯页
        if not members:
            # Left the lobby: its other team must not linger into a later champ select
            self.__dropLobbyEnemies()
            if self.stackedWidget.currentWidget() is self.gameInfoInterface:
                self.switchTo(self.careerInterface)
            return

        try:
            currentSummonerId = self.currentSummoner['summonerId']
        except (TypeError, KeyError):
            return

        # 大厅换了模式 (单双 -> 灵活 ...): 已缓存的战绩是按旧队列过滤的, 全部重查
        queueFilter = lobbyHistoryQueueFilter(lobby)
        if queueFilter != self._lobbyQueueFilter:
            self._lobbyQueueFilter = queueFilter
            self._lobbyGameInfoCache = {}

        teams = splitCustomLobbyTeams(lobby, currentSummonerId,
                                      self.currentSummoner.get('puuid'))
        if teams is not None:
            await self.__refreshCustomLobby(lobby, currentSummonerId, *teams, current=current)
            return

        # No longer a custom lobby: drop the other team it showed
        self.__dropLobbyEnemies()

        def showAlly(info):
            if current():
                self.gameInfoInterface.updateAllySummoners(info)

        info = await parseLobbyGameInfo(
            lobby, currentSummonerId, useSGP=True,
            cache=self._lobbyGameInfoCache,
            onPartial=showAlly)

        if not current():
            return

        self.gameInfoInterface.updateAllySummoners(info)
        self.checkAndSwitchTo(self.gameInfoInterface)

    def __dropLobbyEnemies(self):
        """Clear the enemy side if (and only if) it shows a custom lobby's other team"""
        if self._lobbyEnemyShown:
            self._lobbyEnemyShown = False
            self.gameInfoInterface.clearEnemy()

    async def __refreshCustomLobby(self, lobby, currentSummonerId, allyMembers, enemyMembers,
                                   current):
        """
        Custom lobby: load both teams already in the lobby (own team = ally). Both sides
        share the puuid cache, so only newly joined players are fetched and a player who
        switches teams is not fetched again; an emptied enemy side is cleared.
        @param current: () -> bool, False once a newer refresh or a non-lobby phase began
        """
        epoch = self._champSelectEpoch

        def live():
            # a newer refresh, champ select or the game took over meanwhile: they own the page
            return current() and epoch == self._champSelectEpoch and not self.isGaming

        def showAlly(info):
            if live():
                self.gameInfoInterface.updateAllySummoners(info)

        def showEnemy(info):
            if live():
                self._lobbyEnemyShown = True
                self.gameInfoInterface.updateEnemySummoners(info)

        allyInfo, enemyInfo = await asyncio.gather(
            parseLobbyGameInfo(lobby, currentSummonerId, useSGP=True,
                               cache=self._lobbyGameInfoCache, onPartial=showAlly,
                               members=allyMembers),
            parseLobbyGameInfo(lobby, currentSummonerId, useSGP=True,
                               cache=self._lobbyGameInfoCache, onPartial=showEnemy,
                               members=enemyMembers))

        if not live():
            return

        showAlly(allyInfo)
        if enemyInfo['summoners']:
            showEnemy(enemyInfo)
        else:
            # only clear lobby-loaded cards, never a previous game's reserved enemies
            self.__dropLobbyEnemies()

        self.checkAndSwitchTo(self.gameInfoInterface)

    # 进入英雄选择界面时触发
    async def __onChampionSelectBegin(self):
        self.championSelection.reset()

        self._champSelectEpoch += 1
        self._champSelectReady = False
        self._allyGameInfoCache = {}
        self._revealParticipantCount = 0
        self._lastRevealRefresh = time.monotonic()
        self._enemyGameInfoCache = {}
        self._enemyKey = None
        epoch = self._champSelectEpoch

        session = await connector.getChampSelectSession()
        queueId = await self.__champSelectQueueId(session)
        # left this champ select meanwhile (dodge / game start bumped the epoch): its
        # queue, enemies and op.gg window belong to the newer state
        if epoch != self._champSelectEpoch:
            return

        self._champSelectQueueId = queueId
        self.__refreshEnemies(session)

        if cfg.get(cfg.autoShowOpgg):
            self.opggWindow.show()

            if cfg.get(cfg.enableOpggOnTop):
                self.opggWindow.setStaysOnTopEnabled(True)

        currentSummonerId = self.currentSummoner['summonerId']

        def showPartial(info):
            # 每到一个队友先显示 (冷启动时一个人要两秒多); 已离开这次选人则不再刷新
            if epoch == self._champSelectEpoch:
                self.gameInfoInterface.updateAllySummoners(info)
                self.checkAndSwitchTo(self.gameInfoInterface)

        info = await parseAllyGameInfo(session, currentSummonerId, useSGP=True,
                                       currentPuuid=self.currentSummoner.get('puuid'),
                                       cache=self._allyGameInfoCache,
                                       onPartial=showPartial,
                                       queueId=self._champSelectQueueId)
        if epoch != self._champSelectEpoch:
            return

        self.gameInfoInterface.updateAllySummoners(info)
        self._champSelectReady = True

        self.checkAndSwitchTo(self.gameInfoInterface)

    async def __champSelectQueueId(self, session):
        """这次选人的队列: 选人 session 里有就用, 没有再问 gameflow; 都拿不到返回 None (不过滤)"""
        queueId = session.get('queueId') if isinstance(session, dict) else None
        if queueId:
            return queueId

        try:
            gameflow = await connector.getGameflowSession()
            return gameflow['gameData']['queue']['id']
        except Exception:
            return None

    async def __refreshHiddenAllies(self, session):
        """
        选人中仍有队友没显示时, 节流 (2s) 重读 Riot Client 选人聊天房名单;
        名单比上次长就重新解析己方 (缓存命中的队友不再请求), 直到 5 人齐全
        """
        if not cfg.get(cfg.revealHiddenTeammates) or not self._champSelectReady:
            return

        if self._revealRefreshing:
            return

        myTeam = session.get('myTeam') or []
        if len(self.gameInfoInterface.allyOrder) >= len(myTeam):
            return

        now = time.monotonic()
        if now - self._lastRevealRefresh < 2:
            return

        self._lastRevealRefresh = now
        self._revealRefreshing = True
        epoch = self._champSelectEpoch

        try:
            participants = await connector.getChampSelectChatParticipants()
            if len(participants) <= self._revealParticipantCount:
                return

            self._revealParticipantCount = len(participants)

            info = await parseAllyGameInfo(
                session, self.currentSummoner['summonerId'], useSGP=True,
                currentPuuid=self.currentSummoner.get('puuid'),
                cache=self._allyGameInfoCache,
                queueId=self._champSelectQueueId)

            # 解析期间可能已离开这次选人 (秒退 / 进游戏), 结果作废
            if epoch != self._champSelectEpoch or not self._champSelectReady:
                return

            if len(info['summoners']) > len(self.gameInfoInterface.allyOrder):
                self.gameInfoInterface.updateAllySummoners(info)
        except Exception as e:
            logger.error(f"reveal-hidden: refresh failed: {errorSummary(e)}", TAG)
        finally:
            self._revealRefreshing = False

    # 英雄选择时，英雄改变 / 楼层改变时触发
    @asyncSlot(dict)
    async def __onChampSelectChanged(self, data):
        data = data['data']

        phase = {
            'PLANNING': [autoShow],
            'BAN_PICK': [autoBan, autoPick, autoComplete, autoSwap, showOpggBuild],
            'FINALIZATION': [autoTrade, showOpggBuild],
        }

        for func in phase.get(data['timer']['phase'], []):
            if await func(data, self.championSelection):
                break

        # 更新头像
        await self.gameInfoInterface.updateAllyIcon(data['myTeam'])

        # 更新楼层顺序
        self.gameInfoInterface.updateAllySummonersOrder(data['myTeam'])

        # 后进入聊天房的隐藏队友
        await self.__refreshHiddenAllies(data)

        # 能看到身份的敌方: 加载战绩, 跟随其选的英雄
        self.__refreshEnemies(data)
        await self.gameInfoInterface.updateEnemyIcon(data.get('theirTeam') or [])

    def __refreshEnemies(self, session):
        """
        选人阶段, 敌方身份可见时 (自定义 / 锦标赛征召等; 排位里 Riot 隐藏敌方) 在后台加载
        敌方战绩, 每到一个人先显示; 可见的敌方变化时再加载一次 (已加载的走缓存)
        """
        enemies = visibleEnemies(session)
        if not enemies or self._enemyLoading or not self.currentSummoner:
            return

        key = frozenset(m.get('puuid') or m.get('summonerId') for m in enemies)
        if key == self._enemyKey:
            return

        self._enemyKey = key
        self._enemyLoading = True
        epoch = self._champSelectEpoch

        def show(info):
            if epoch == self._champSelectEpoch:
                self.gameInfoInterface.updateEnemySummoners(info)

        async def load():
            try:
                info = await parseEnemyChampSelectInfo(
                    session, self.currentSummoner['summonerId'], useSGP=True,
                    cache=self._enemyGameInfoCache, onPartial=show,
                    queueId=self._champSelectQueueId)
                show(info)
            except Exception as e:
                logger.error(f"load enemies in champ select failed: {errorSummary(e)}", TAG)
            finally:
                self._enemyLoading = False

        asyncio.ensure_future(load())

    # 进入游戏后触发
    async def __onGameStart(self, current):
        """
        @param current: () -> bool, False once a newer gameflow event arrived: it owns the
            live poller and the game info page from then on
        """
        # From here the enemy side belongs to the game, not to the lobby
        self._lobbyEnemyShown = False
        session = await connector.getGameflowSession()
        if not current():
            return

        currentSummonerId = self.currentSummoner['summonerId']

        queueId = session['gameData']['queue']['id']
        if queueId in (1700, 1090, 1100, 1110, 1130, 1160):  # 斗魂 云顶匹配 (排位)
            return

        # Live item value (game info panel + optional overlay); stopped when not gaming.
        # Not for Arena (1700 returned above, 1710 here): no two teams to compare
        if queueId != 1710:
            liveClientPoller.start()

        # 如果是进游戏后开的软件，需要先把友方信息更新上去
        async def paintAllySummonersInfo():
            # TODO 自定义时, 若队伍成员<5, 会触发重新加载导致性能浪费
            if self.gameInfoInterface.allyChampions and len(self.gameInfoInterface.allyChampions) >= 5:
                return

            info = await parseGameInfoByGameflowSession(
                session, currentSummonerId, "ally", useSGP=True,
                onPartial=self.gameInfoInterface.updateAllySummoners)

            self.gameInfoInterface.updateAllySummoners(info)

        # 将敌方的召唤师基本信息绘制上去
        async def paintEnemySummonersInfo():
            info = await parseGameInfoByGameflowSession(
                session, currentSummonerId, 'enemy', useSGP=True,
                onPartial=self.gameInfoInterface.updateEnemySummoners)

            # 这个 info 是已经按照游戏位置排序过的了（若排位）
            self.gameInfoInterface.updateEnemySummoners(info)

        # 更新己方召唤师楼层顺序至角色顺序
        async def sortAllySummonersByGameRole():
            order = getAllyOrderByGameRole(session, currentSummonerId)
            if order == None:
                return

            interface = self.gameInfoInterface

            if order == interface.allyOrder or len(order) != len(interface.allyOrder):
                return

            interface.setAllyOrder(order)

        # 绘制提示组队的颜色
        async def paintTeamColor():
            ally, enemy = getTeamColor(session, currentSummonerId)
            self.gameInfoInterface.updateTeamColor(ally, enemy)

        await paintAllySummonersInfo()
        await asyncio.gather(paintEnemySummonersInfo(),
                             sortAllySummonersByGameRole())
        await paintTeamColor()

        self.checkAndSwitchTo(self.gameInfoInterface)

    async def __onGameEnd(self):
        if not cfg.get(cfg.enableReserveGameinfo):
            self.gameInfoInterface.clear()

    def __checkWindowSize(self):
        if (dpi := self.devicePixelRatioF()) == 1.0:
            return

        w, h = platform.get_system_metrics(0), platform.get_system_metrics(1)
        size: QSize = self.size() * dpi

        if size.width() < w and size.height() < h:
            return

        for scale in [2.0, 1.75, 1.5, 1.25, 1]:
            if scale * self.width() < w and scale * self.height() < h:
                cfg.set(cfg.dpiScale, scale)
                break

        self.splashScreen.finish()
        msg = ChangeDpiMessageBox(self.window())
        msg.exec()

    @asyncSlot(str)
    async def __onCareerGameClicked(self, gameId):
        await self.__openGameDetailInSearch(
            self.careerInterface.getSummonerName(),
            self.careerInterface.puuid, gameId)

    @asyncSlot(str, str, str)
    async def __onGameInfoBarClicked(self, puuid, name, gameId):
        # 从对局信息界面点击某局卡片跳过来 (见 issue #11)
        await self.__openGameDetailInSearch(name, puuid, gameId)

    async def __openGameDetailInSearch(self, name, puuid, gameId):
        self.searchInterface.searchLineEdit.setText(name)

        # 默认将筛选条件设置为全部 -- By Hpero4
        self.searchInterface.filterComboBox.setCurrentIndex(0)

        # 先加载完再切换, 避免加载过程中换搜索目标导致puuid出错 -- By Hpero4
        await self.searchInterface.searchAndShowFirstPage(puuid)
        self.checkAndSwitchTo(self.searchInterface)
        self.searchInterface.loadingGameId = gameId

        # 先画框再加载对局 否则快速切换(如筛选或换人)会导致找不到widget -- By Hpero4
        self.searchInterface.waitingForDrawSelect(gameId)
        await self.searchInterface.updateGameDetailView(gameId, puuid)

    def showOpggWindow(self):
        self.opggWindow.show()
        self.opggWindow.showNormal()

        self.opggWindow.raise_()

    @asyncSlot()
    async def __refreshCareerInterface(self):
        if self.isClientProcessRunning:
            self.careerInterface.refreshButton.click()

    @asyncSlot()
    async def __cascadeSetMicaEffect(self):
        isMicaEnabled = cfg.get(cfg.micaEnabled)
        self.setMicaEffectEnabled(isMicaEnabled)
        self.opggWindow.setMicaEffectEnabled(isMicaEnabled)

    @asyncSlot()
    async def __onFixLCUButtonClicked(self):
        if self.isClientProcessRunning:
            await connector.playAgain()

    def exceptHook(self, ty, value, tb):
        # What happens is decided by error_policy (fatal only before start-up finished)
        decision = self.errorPolicy.decide(ty, value, tb)

        if decision.action == error_policy.SILENT:
            return  # counted; written with the next one-line entry

        if decision.action == error_policy.BRIEF:
            # error level: the default log level (40) drops warnings
            logger.error(f"Exception ({decision.reason}): {decision.text}", "Crash")
            return

        content = decision.text

        # Let the default hook print to stderr (thread-safe).
        self.oldHook(ty, value, tb)

        if decision.action != error_policy.FATAL:
            # same GUI-thread routing as the dialog below
            self.showExceptionNotice.emit(decision.action, decision.reason, content)
            return

        # sys.excepthook may fire on a worker thread — most often the
        # LolProcessExistenceListener QThread. Building ExceptionMessageBox (a
        # top-level window) off the GUI thread makes AppKit raise an NSException and
        # SIGABRT the whole process on macOS ("Seraphine quit unexpectedly"), turning
        # a loggable error into a hard crash. Route the dialog + shutdown to the main
        # thread: this signal is delivered directly when we're already on the GUI
        # thread, and as a queued (main-thread) call when emitted from a worker thread.
        self.showExceptionMessageBox.emit(content)

    def __logExceptionDiagnostics(self, content):
        logger.error(f"Exception occurred:\n{content}", "Crash")

        for call in connector.callStack:
            logger.error(call, "Crash")

        logger.error(str(self.searchInterface), "Crash")
        logger.error(str(self.gameInfoInterface), "Crash")
        logger.error(str(self.careerInterface), "Crash")
        logger.error(str(self.auxiliaryFuncInterface), "Crash")
        logger.error(str(self.settingInterface), "Crash")

    def __onShowExceptionNotice(self, action, reason, content):
        if action == error_policy.EXIT:
            # KeyboardInterrupt / SystemExit: end the process as before, without the dialog
            logger.error(f"Exception occurred:\n{content}", "Crash")
            signalBus.terminateListeners.emit()
            logger.error("Exit on interrupt", "Crash")
            sys.exit(1)

        if action != error_policy.NOTIFY:
            # a notice is already open, or the user was told already: log only
            logger.error(f"Exception occurred ({reason}), not shown:\n{content}", "Crash")
            return

        self.__logExceptionDiagnostics(content)

        details = f"Seraphine ver.{BETA or VERSION}\n{'-'*5}\n{content}"
        bar = InfoBar.error(
            self.tr('Exception occurred 😥'),
            self.tr("Seraphine is still running. Details are in the log."),
            orient=Qt.Vertical,
            isClosable=True,
            duration=15000,
            position=InfoBarPosition.BOTTOM_RIGHT,
            parent=self,
        )
        # For bug reports, like the fatal dialog's "Copy to clipboard"; no modal dialog,
        # so nothing blocks the user
        copyButton = PushButton(self.tr("Copy details"))

        def copy():
            pyperclip.copy(details)
            copyButton.setText(self.tr("Copied"))
            copyButton.setEnabled(False)

        copyButton.clicked.connect(copy)
        bar.addWidget(copyButton)
        bar.closedSignal.connect(self.errorPolicy.noticeClosed)

    def __onShowExceptionMessageBox(self, content):
        self.__logExceptionDiagnostics(content)

        title = self.tr('Exception occurred 😥')
        content = f"Seraphine ver.{BETA or VERSION}\n{'-'*5}\n{content}"

        w = ExceptionMessageBox(title, content, self.window())

        if w.exec():
            pyperclip.copy(content)

        signalBus.terminateListeners.emit()
        logger.error("Abnormal exit", "Crash")
        sys.exit()

    def __onCurrentStackedChanged(self, index):
        widget: SmoothScrollArea = self.stackedWidget.view.currentWidget()
        widget.delegate.vScrollBar.resetValue(0)

    def eventFilter(self, obj, e: QEvent):
        # Fix #553, #560
        if e.type() == QEvent.Type.Resize:
            self.windowSize = self.size()

        if e.type() == QEvent.Type.Move:
            self.resize(self.windowSize)

        return super().eventFilter(obj, e)
