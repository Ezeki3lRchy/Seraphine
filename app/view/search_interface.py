import re
import threading
import time
import traceback
from asyncio import CancelledError, LifoQueue
from collections import OrderedDict

import pyperclip
from qasync import asyncSlot
import asyncio
from PySide6.QtWidgets import (QVBoxLayout, QHBoxLayout, QFrame,
                             QSpacerItem, QSizePolicy, QLabel, QStackedWidget, QWidget)
from PySide6.QtCore import Qt, Signal, QEvent, QRectF, QPointF
from PySide6.QtGui import QPixmap, QColor, QPainter, QPen, QFontMetrics

from ..common.logger import logger
from ..common.qfluentwidgets import (SmoothScrollArea, PushButton, ToolButton, InfoBar,
                                     InfoBarPosition, ToolTipFilter, ToolTipPosition,
                                     isDarkTheme, FlyoutViewBase, Flyout, Theme,
                                     IndeterminateProgressRing, ComboBox, StateToolTip,
                                     setCustomStyleSheet, TransparentPushButton)

from app.common.style_sheet import StyleSheet, ColorChangeable
from app.common.icons import Icon
from app.common.config import cfg
from app.common.signals import signalBus
from app.components.champion_icon_widget import RoundIcon, RoundedLabel
from app.components.match_widgets import ItemIcon
from app.components.match_detail_widgets import DetailTeamTable, TeamCompareBar, teamMaxima
from app.components.design_tokens import palette, numberFont, textFont, deathsColor
from app.components.scoreboard import (cachedContent, drawText, drawRuns, runsWidth,
                                       drawChampion, drawResultCard, stripeColor,
                                       resultType, queueShortName, compactAgo, durationText)
from app.components.search_line_edit import SearchLineEdit
from app.components.summoner_name_button import SummonerName
from app.components.color_label import ColorLabel, DeathsLabel
from app.lol.connector import connector
from app.lol.exceptions import SummonerGamesNotFound, SummonerNotFound
from app.lol.tools import parseGameData, parseGameDetailData, parseGamesDataConcurrently
from ..components.seraphine_interface import SeraphineInterface


TAG = "SearchInterface"


def asyncLockDecorator(lockName):
    """
    给任何一个方法加锁, return时释放
    args[0]是self, 通过getattr找到lockName
    """
    def decorator(func):
        async def wrapper(*args, **kwargs):
            lock = getattr(args[0], lockName)
            async with lock:
                return await func(*args, **kwargs)
        return wrapper
    return decorator


class GamesTab(QFrame):
    tabClicked = Signal(str)
    gameDetailReady = Signal(dict)
    loadFinish = Signal()

    def __init__(self, parnet=None):
        super().__init__(parnet)
        # Holds the self-painted GameTab rows, which stretch to the inner width
        # (mode name elided, time right-aligned, so wider macOS fonts can't clip).
        # The layout margins are pinned explicitly in __initLayout because Qt's
        # *default* layout margin differs by platform (~20px on macOS vs ~9 on
        # Windows).
        self.setFixedWidth(174)
        self.vBoxLayout = QVBoxLayout(self)

        self.stackWidget = QStackedWidget()
        self.buttonsLayout = QHBoxLayout()

        self.prevButton = ToolButton(Icon.CHEVRONLEFT)
        self.pageLabel = QLabel(" ")
        self.nextButton = ToolButton(Icon.CHEVRONRIGHT)

        # 从生涯界面里调过来，需要被选中但还没被选中的 gameId
        self.waitingForSelect = None

        # 当前选中的 queueId，-1 对应所有对局
        self.queueId = -1

        # 当前所在的页码
        self.currentPageNum = 0

        # 目前已经绘制好的最大页码
        self.maxPageNum = 0

        # 目前选中的 tab
        self.currentTabSelected = None

        # 所有的对局记录
        self.games = LifoQueue()  # LifoQueue 保证线程安全 -- By Hpero4

        # queueId 到对应 self.games 下标数组的映射
        # OrderedDict 保证线程安全 -- By Hpero4
        self.queueIdMap = OrderedDict({-1: []})

        self.__initWidget()
        self.__initLayout()
        self.__connectSignalToSlot()

    def __initWidget(self):
        self.pageLabel.setAlignment(Qt.AlignCenter)
        self.prevButton.setVisible(False)
        self.prevButton.setEnabled(False)
        self.nextButton.setVisible(False)
        self.nextButton.setEnabled(False)

    def __initLayout(self):
        # Pin margins so the card fit is identical on macOS and Windows (Qt's
        # per-platform default would otherwise clip the card on macOS).
        self.vBoxLayout.setContentsMargins(10, 10, 10, 10)

        defaultWidget = QWidget()
        layout = QVBoxLayout(defaultWidget)
        layout.setContentsMargins(0, 0, 0, 0)

        self.stackWidget.addWidget(defaultWidget)
        self.stackWidget.setCurrentIndex(self.currentPageNum)

        self.buttonsLayout.addWidget(self.prevButton)
        self.buttonsLayout.addWidget(self.pageLabel)
        self.buttonsLayout.addWidget(self.nextButton)

        self.vBoxLayout.addWidget(self.stackWidget)
        self.vBoxLayout.addSpacing(10)
        self.vBoxLayout.addLayout(self.buttonsLayout)

    def __connectSignalToSlot(self):
        self.prevButton.clicked.connect(self.__onPrevButtonClicked)
        self.nextButton.clicked.connect(self.__onNextButtonClicked)

    def updateQueueIdMap(self, games):
        for game in games:
            index = len(self.games)
            self.games.append(game)
            queueId = game['queueId']

            l: list = self.queueIdMap.get(queueId)

            if not l:
                self.queueIdMap[queueId] = [index]
            else:
                l.append(index)

            self.queueIdMap[-1].append(index)

    @asyncSlot()
    async def __onPrevButtonClicked(self):
        self.currentPageNum -= 1
        self.pageLabel.setText(f"{self.currentPageNum}")
        self.stackWidget.setCurrentIndex(self.currentPageNum)

        self.resetButtonEnabled()

    @asyncSlot()
    async def __onNextButtonClicked(self):
        self.currentPageNum += 1

        if self.currentPageNum > self.maxPageNum:
            # self.games 一定足够绘制下一页，因为否则这个按钮本身就点不了
            self.prepareNextPage()

        self.pageLabel.setText(f"{self.currentPageNum}")
        self.stackWidget.setCurrentIndex(self.currentPageNum)

        self.resetButtonEnabled()

    def showTheFirstPage(self):
        self.prepareNextPage()

        self.stackWidget.setCurrentIndex(1)
        self.currentPageNum = 1
        self.pageLabel.setText("1")

        self.nextButton.setVisible(True)
        self.prevButton.setVisible(True)
        self.pageLabel.setVisible(True)

        self.resetButtonEnabled()

    def resetButtonEnabled(self):
        prevEnable = not self.currentPageNum in [0, 1]
        self.prevButton.setEnabled(prevEnable)

        nextEnable = len(
            self.queueIdMap.get(self.queueId, [])) > self.currentPageNum * 10
        self.nextButton.setEnabled(nextEnable)

    def prepareNextPage(self):
        ''' 满足
        - 当前所在页码 == 已经绘制好的最大页码时
        - 在内存中的对局记录数量大于已经绘制好的数量时

        调用该函数，绘制下一页，加入 stackedWidget
        '''

        # 游戏数据在 self.games 数组中对应的下标
        indices = self.queueIdMap[self.queueId]

        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        begin = self.maxPageNum * 10

        # 防止能画上去的不够 10 个导致越界
        end = min(10 + begin, len(indices))

        for i in range(begin, end):
            tab = GameTab(self.games[indices[i]])
            waiting = self.waitingForSelect

            if waiting and tab.gameId == int(self.waitingForSelect):
                tab.setProperty('selected', True)
                tab.style().polish(tab)
                self.currentTabSelected = tab
                self.waitingForSelect = None

            layout.addWidget(tab, stretch=1)

        # 如果不够十个就给它填充一下
        if end - begin < 10:
            layout.addStretch(10 - (end - begin))
            spacing = layout.spacing()
            layout.addSpacing((10 - (end - begin)) * spacing)

        self.stackWidget.addWidget(widget)
        self.maxPageNum += 1

    def clearTabs(self):
        self.currentPageNum = 0
        self.maxPageNum = 0
        self.currentTabSelected = None

        # PySide6's QStackedWidget has no __len__ (PyQt5 did); use count().
        for i in reversed(range(self.stackWidget.count())):
            if i == 0:
                continue

            widget = self.stackWidget.widget(i)
            self.stackWidget.removeWidget(widget)

        self.stackWidget.setCurrentIndex(0)

    def clear(self):
        self.pageLabel.setText(" ")

        self.prevButton.setVisible(False)
        self.nextButton.setVisible(False)

        self.queueId = -1
        self.games = []
        self.queueIdMap = {-1: []}

        self.clearTabs()

    def clickFirstTab(self):
        widget = self.stackWidget.widget(1)
        tab = widget.layout().itemAt(0).widget()

        tab.setProperty("pressed", False)
        tab.setProperty("selected", True)
        tab.style().polish(tab)
        signalBus.gameTabClicked.emit(tab)


class GameDetailView(QFrame):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.hBoxLayout = QHBoxLayout(self)
        self.stackedWidget = QStackedWidget()

        self.infoPage = QWidget()
        self.vBoxLayout = QVBoxLayout(self.infoPage)
        self.titleBar = GameTitleBar()

        self.scrollWidget = QWidget()
        self.scrollArea = SmoothScrollArea()
        self.scrollLayout = QVBoxLayout()

        self.teamView1 = TeamView()
        self.teamView2 = TeamView()

        # OP.GG 风格: 两队各一张表 + 中间的目标 / 击杀 / 经济对比 (斗魂竞技场仍用 TeamView)
        self.detailTeam1 = DetailTeamTable()
        self.compareBar = TeamCompareBar()
        self.detailTeam2 = DetailTeamTable()

        self.extraTeamView1 = TeamView()
        self.extraTeamView2 = TeamView()
        self.extraTeamView3 = TeamView()
        self.extraTeamView4 = TeamView()
        self.extraTeamView5 = TeamView()
        self.extraTeamView6 = TeamView()

        self.loadingPage = QWidget()
        self.loadingPageLayout = QHBoxLayout(self.loadingPage)
        self.processRing = IndeterminateProgressRing()

        self.__initLayout()

    def clear(self):
        # PySide6/shiboken6 deletes the C++ QWidgetItem when it is removed from
        # the layout, so the old `itemAt(i)` + `removeItem(item)` + `item.widget()`
        # pattern raised "Internal C++ object already deleted". `takeAt(0)` removes
        # AND returns the item with ownership transferred to Python, so it stays valid.
        while self.vBoxLayout.count():
            item = self.vBoxLayout.takeAt(0)
            if item is not None and item.widget():
                item.widget().deleteLater()

        self.titleBar = GameTitleBar()

        self.scrollWidget = QWidget()
        self.scrollArea = SmoothScrollArea()
        self.scrollLayout = QVBoxLayout()

        self.teamView1 = TeamView()
        self.teamView2 = TeamView()

        # OP.GG 风格: 两队各一张表 + 中间的目标 / 击杀 / 经济对比 (斗魂竞技场仍用 TeamView)
        self.detailTeam1 = DetailTeamTable()
        self.compareBar = TeamCompareBar()
        self.detailTeam2 = DetailTeamTable()

        self.extraTeamView1 = TeamView()
        self.extraTeamView2 = TeamView()
        self.extraTeamView3 = TeamView()
        self.extraTeamView4 = TeamView()
        self.extraTeamView5 = TeamView()
        self.extraTeamView6 = TeamView()

        self.__initLayout()

    def __initLayout(self):
        self.scrollArea.setObjectName("gameScrollArea")
        # self.scrollArea.setVerticalScrollBarPolicy(
        #     Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scrollWidget.setObjectName("scrollWidget")

        self.scrollWidget.setLayout(self.scrollLayout)
        self.scrollArea.setWidget(self.scrollWidget)
        self.scrollArea.setWidgetResizable(True)

        self.loadingPageLayout.addWidget(self.processRing)

        self.vBoxLayout.addWidget(self.titleBar)

        self.scrollLayout.setContentsMargins(0, 0, 0, 0)
        self.scrollLayout.addWidget(self.detailTeam1)
        self.scrollLayout.addWidget(self.compareBar)
        self.scrollLayout.addWidget(self.detailTeam2)
        self.scrollLayout.addWidget(self.teamView1)
        self.scrollLayout.addWidget(self.teamView2)

        self.scrollLayout.addWidget(self.extraTeamView1)
        self.scrollLayout.addWidget(self.extraTeamView2)
        self.scrollLayout.addWidget(self.extraTeamView3)
        self.scrollLayout.addWidget(self.extraTeamView4)
        self.scrollLayout.addWidget(self.extraTeamView5)
        self.scrollLayout.addWidget(self.extraTeamView6)

        self.vBoxLayout.addWidget(self.scrollArea)

        self.extraTeamView1.setVisible(False)
        self.extraTeamView2.setVisible(False)
        self.extraTeamView3.setVisible(False)
        self.extraTeamView4.setVisible(False)
        self.extraTeamView5.setVisible(False)
        self.extraTeamView6.setVisible(False)
        self.detailTeam1.setVisible(False)
        self.compareBar.setVisible(False)
        self.detailTeam2.setVisible(False)
        self.scrollLayout.addStretch()

        self.stackedWidget.addWidget(self.loadingPage)
        self.stackedWidget.addWidget(self.infoPage)
        self.stackedWidget.setCurrentIndex(1)

        self.hBoxLayout.setContentsMargins(0, 0, 0, 0)
        self.hBoxLayout.addWidget(self.stackedWidget)

        # self.vBoxLayout.addSpacerItem(
        #     QSpacerItem(1, 1, QSizePolicy.Minimum, QSizePolicy.Expanding))

    def setLoadingPageEnabled(self, enable: bool):
        index = 0 if enable else 1
        self.stackedWidget.setCurrentIndex(index)

    def updateGame(self, game):
        if not game:
            InfoBar.error(self.tr("Get game infomation failed"),
                          self.tr("The server returned abnormal content."),
                          orient=Qt.Vertical,
                          duration=5000,
                          position=InfoBarPosition.BOTTOM_RIGHT,
                          parent=self.window())

            return

        isCherry = game["queueId"] == 1700
        self.titleBar.updateTitleBar(game)

        team1 = game["teams"][100]
        team2 = game["teams"][200]

        # 普通两队对局: OP.GG 风格详情表; 斗魂竞技场: 原来的 8 队视图
        for w in (self.detailTeam1, self.compareBar, self.detailTeam2):
            w.setVisible(not isCherry)
        self.teamView1.setVisible(isCherry)
        self.teamView2.setVisible(isCherry)

        if not isCherry:
            maxDealt, maxTaken = teamMaxima((team1, team2))
            secs = game.get('durationSecs') or 0
            self.detailTeam1.updateTeam(team1, self.tr("Blue team"), maxDealt, maxTaken, secs)
            self.detailTeam2.updateTeam(team2, self.tr("Red team"), maxDealt, maxTaken, secs)
            self.compareBar.setTeams(team1, team2)

        self.teamView1.updateTeam(team1, isCherry, self.tr("1st"))
        self.teamView1.updateSummoners(team1["summoners"])

        self.teamView2.updateTeam(team2, isCherry, self.tr("2nd"))
        self.teamView2.updateSummoners(team2["summoners"])

        self.extraTeamView1.setVisible(isCherry)
        self.extraTeamView2.setVisible(isCherry)
        self.extraTeamView3.setVisible(isCherry)
        self.extraTeamView4.setVisible(isCherry)
        self.extraTeamView5.setVisible(isCherry)
        self.extraTeamView6.setVisible(isCherry)

        if isCherry:
            team3 = game["teams"][300]
            team4 = game["teams"][400]
            team5 = game["teams"][500]
            team6 = game["teams"][600]
            team7 = game["teams"][700]
            team8 = game["teams"][800]

            self.extraTeamView1.updateTeam(team3, isCherry, self.tr("3rd"))
            self.extraTeamView1.updateSummoners(team3["summoners"])

            self.extraTeamView2.updateTeam(team4, isCherry, self.tr("4th"))
            self.extraTeamView2.updateSummoners(team4["summoners"])

            self.extraTeamView3.updateTeam(team5, isCherry, self.tr("5th"))
            self.extraTeamView3.updateSummoners(team5["summoners"])

            self.extraTeamView4.updateTeam(team6, isCherry, self.tr("6th"))
            self.extraTeamView4.updateSummoners(team6["summoners"])

            self.extraTeamView5.updateTeam(team7, isCherry, self.tr("7th"))
            self.extraTeamView5.updateSummoners(team7["summoners"])

            self.extraTeamView6.updateTeam(team8, isCherry, self.tr("8th"))
            self.extraTeamView6.updateSummoners(team8["summoners"])


class TeamView(QFrame, ColorChangeable):
    def __init__(self, parent=None):
        # PySide6 rejects parent= keyword in this MI context; pass positionally.
        QFrame.__init__(self, parent)
        ColorChangeable.__init__(self, type=None)

        self.vBoxLayout = QVBoxLayout(self)
        self.titleBarWidget = QWidget()
        self.titleBarLayout = QHBoxLayout(self.titleBarWidget)
        self.summonersLayout = QVBoxLayout()

        self.teamResultLabel = ColorLabel()
        self.towerIconLabel = RoundedLabel(radius=0, borderWidth=0)
        self.towerKillsLabel = QLabel()
        self.inhibitorIconLabel = RoundedLabel(radius=0, borderWidth=0)
        self.inhibitorKillsLabel = QLabel()
        self.baronIconLabel = RoundedLabel(radius=0, borderWidth=0)
        self.baronKillsLabel = QLabel()
        self.dragonIconLabel = RoundedLabel(radius=0, borderWidth=0)
        self.dragonKillsLabel = QLabel()
        self.riftHeraldIconLabel = RoundedLabel(radius=0, borderWidth=0)
        self.riftHeraldKillsLabel = QLabel()

        self.bansButton = TransparentPushButton(self.tr("Bans"))
        self.csIconLabel = RoundedLabel(radius=0, borderWidth=0)
        self.goldIconLabel = RoundedLabel(radius=0, borderWidth=0)
        self.dmgIconLabel = QLabel()

        self.kills = QLabel()
        self.slash1 = QLabel()
        self.deaths = DeathsLabel()
        self.slash2 = QLabel()
        self.assists = QLabel()

        self.isToolTipInit = False

        self.bansFlyOut = None
        self.bansInfo = None

        self.__initWidget()
        self.__initLayout()

        cfg.themeChanged.connect(self.__updateIconColor)

    def __initWidget(self):
        self.titleBarWidget.setObjectName("titleBarWidget")

        self.teamResultLabel.setObjectName("teamResult")
        self.towerIconLabel.setFixedSize(22, 22)
        self.inhibitorIconLabel.setFixedSize(18, 18)
        self.baronIconLabel.setFixedSize(17, 17)
        self.dragonIconLabel.setFixedSize(18, 18)
        self.riftHeraldIconLabel.setFixedSize(18, 18)

        self.bansButton.setVisible(False)

        self.kills.setFixedWidth(23)
        self.kills.setAlignment(Qt.AlignCenter)
        self.deaths.setFixedWidth(23)
        self.deaths.setAlignment(Qt.AlignCenter)
        self.assists.setFixedWidth(23)
        self.assists.setAlignment(Qt.AlignCenter)
        self.slash1.setFixedWidth(10)
        self.slash1.setAlignment(Qt.AlignCenter)
        self.slash2.setFixedWidth(10)
        self.slash2.setAlignment(Qt.AlignCenter)

        self.csIconLabel.setFixedSize(18, 18)
        self.goldIconLabel.setFixedSize(18, 18)
        self.dmgIconLabel.setFixedWidth(70)
        self.dmgIconLabel.setAlignment(Qt.AlignCenter)

        self.csIconLabel.setVisible(False)
        self.goldIconLabel.setVisible(False)

        self.dmgIconLabel.setObjectName("dmgIconLabel")
        self.bansButton.clicked.connect(self.__onBansButtonClicked)

    def __initToolTip(self):
        self.towerIconLabel.setToolTip(self.tr("Tower destroyed"))
        self.towerIconLabel.setAlignment(Qt.AlignCenter)
        self.inhibitorIconLabel.setToolTip(self.tr("Inhibitor destroyed"))
        self.inhibitorIconLabel.setAlignment(Qt.AlignCenter)
        self.baronIconLabel.setToolTip(self.tr("Baron Nashor killed"))
        self.baronIconLabel.setAlignment(Qt.AlignCenter)
        self.dragonIconLabel.setToolTip(self.tr("Dragon killed"))
        self.dragonIconLabel.setAlignment(Qt.AlignCenter)
        self.riftHeraldIconLabel.setToolTip(
            self.tr("Rift Herald / Horde killed"))
        self.riftHeraldIconLabel.setAlignment(Qt.AlignCenter)

        self.towerIconLabel.installEventFilter(ToolTipFilter(
            self.towerIconLabel, 500, ToolTipPosition.TOP))
        self.inhibitorIconLabel.installEventFilter(ToolTipFilter(
            self.inhibitorIconLabel, 500, ToolTipPosition.TOP))
        self.baronIconLabel.installEventFilter(ToolTipFilter(
            self.baronIconLabel, 500, ToolTipPosition.TOP))
        self.dragonIconLabel.installEventFilter(ToolTipFilter(
            self.dragonIconLabel, 500, ToolTipPosition.TOP))
        self.riftHeraldIconLabel.installEventFilter(ToolTipFilter(
            self.riftHeraldIconLabel, 500, ToolTipPosition.TOP))

        self.towerKillsLabel.setToolTip(self.tr("Tower destroyed"))
        self.inhibitorKillsLabel.setToolTip(self.tr("Inhibitor destroyed"))
        self.baronKillsLabel.setToolTip(self.tr("Baron Nashor killed"))
        self.dragonKillsLabel.setToolTip(self.tr("Dragon killed"))
        self.riftHeraldKillsLabel.setToolTip(
            self.tr("Rift Herald / Horde killed"))

        self.towerKillsLabel.installEventFilter(ToolTipFilter(
            self.towerKillsLabel, 500, ToolTipPosition.TOP))
        self.inhibitorKillsLabel.installEventFilter(ToolTipFilter(
            self.inhibitorKillsLabel, 500, ToolTipPosition.TOP))
        self.baronKillsLabel.installEventFilter(ToolTipFilter(
            self.baronKillsLabel, 500, ToolTipPosition.TOP))
        self.dragonKillsLabel.installEventFilter(ToolTipFilter(
            self.dragonKillsLabel, 500, ToolTipPosition.TOP))
        self.riftHeraldKillsLabel.installEventFilter(ToolTipFilter(
            self.riftHeraldKillsLabel, 500, ToolTipPosition.TOP))

        self.csIconLabel.setToolTip(self.tr("Minions killed"))
        self.goldIconLabel.setToolTip(self.tr("Gold earned"))
        self.dmgIconLabel.setToolTip(self.tr("Damage dealed to champions"))
        self.csIconLabel.installEventFilter(ToolTipFilter(
            self.csIconLabel, 500, ToolTipPosition.TOP))
        self.goldIconLabel.installEventFilter(ToolTipFilter(
            self.goldIconLabel, 500, ToolTipPosition.TOP))
        self.dmgIconLabel.installEventFilter(ToolTipFilter(
            self.dmgIconLabel, 500, ToolTipPosition.TOP))

    def __initLayout(self):
        self.teamResultLabel.setFixedHeight(49)
        self.teamResultLabel.setFixedWidth(60)

        self.titleBarLayout.setContentsMargins(7, 0, 1, 0)
        self.titleBarLayout.setSpacing(0)
        self.titleBarLayout.addWidget(self.teamResultLabel)
        self.titleBarLayout.addSpacing(18)
        self.titleBarLayout.addWidget(self.towerIconLabel)
        self.titleBarLayout.addWidget(self.towerKillsLabel)
        self.titleBarLayout.addSpacing(18)
        self.titleBarLayout.addWidget(self.inhibitorIconLabel)
        self.titleBarLayout.addSpacing(5)
        self.titleBarLayout.addWidget(self.inhibitorKillsLabel)
        self.titleBarLayout.addSpacing(18)
        self.titleBarLayout.addWidget(self.baronIconLabel)
        self.titleBarLayout.addSpacing(5)
        self.titleBarLayout.addWidget(self.baronKillsLabel)
        self.titleBarLayout.addSpacing(18)
        self.titleBarLayout.addWidget(self.dragonIconLabel)
        self.titleBarLayout.addSpacing(5)
        self.titleBarLayout.addWidget(self.dragonKillsLabel)
        self.titleBarLayout.addSpacing(18)
        self.titleBarLayout.addWidget(self.riftHeraldIconLabel)
        self.titleBarLayout.addSpacing(5)
        self.titleBarLayout.addWidget(self.riftHeraldKillsLabel)
        self.titleBarLayout.addSpacerItem(QSpacerItem(
            1, 1, QSizePolicy.Expanding, QSizePolicy.Minimum))
        self.titleBarLayout.addWidget(self.bansButton)
        self.titleBarLayout.addSpacing(63)
        self.titleBarLayout.addWidget(self.kills)
        self.titleBarLayout.addSpacing(1)
        self.titleBarLayout.addWidget(self.slash1)
        self.titleBarLayout.addSpacing(1)
        self.titleBarLayout.addWidget(self.deaths)
        self.titleBarLayout.addSpacing(1)
        self.titleBarLayout.addWidget(self.slash2)
        self.titleBarLayout.addSpacing(1)
        self.titleBarLayout.addWidget(self.assists)
        self.titleBarLayout.addSpacing(21)
        self.titleBarLayout.addWidget(self.csIconLabel)
        self.titleBarLayout.addSpacing(43)
        self.titleBarLayout.addWidget(self.goldIconLabel)
        self.titleBarLayout.addSpacing(28)
        self.titleBarLayout.addWidget(self.dmgIconLabel)
        self.titleBarLayout.addSpacing(7)

        self.summonersLayout.setContentsMargins(2, 4, 2, 2)
        self.summonersLayout.setSpacing(4)

        self.vBoxLayout.setContentsMargins(0, 0, 0, 4)
        self.vBoxLayout.setSpacing(0)
        self.vBoxLayout.addWidget(self.titleBarWidget)
        self.vBoxLayout.addSpacing(8)
        self.vBoxLayout.addLayout(self.summonersLayout)

    def updateTeam(self, team, isCherry, result):
        if not self.isToolTipInit:
            self.isToolTipInit = True
            self.__initToolTip()

        win = team['win']
        baronIcon = team['baronIcon']
        baronKills = team['baronKills']
        dragonIcon = team['dragonIcon']
        dragonKills = team['dragonKills']
        riftHeraldIcon = team['riftHeraldIcon']
        riftHeraldKills = team['riftHeraldKills']
        hordeKills = team['hordeKills']
        inhibitorIcon = team['inhibitorIcon']
        inhibitorKills = team['inhibitorKills']
        towerIcon = team['towerIcon']
        towerKills = team['towerKills']
        kills = team['kills']
        deaths = team['deaths']
        assists = team['assists']
        bans = team['bans']

        if isCherry:
            self.teamResultLabel.setText(result)
        elif win == "Win":
            self.teamResultLabel.setText(self.tr("Winner"))
            self.teamResultLabel.setType('win')
            self.setType('win')
        else:
            self.teamResultLabel.setText(self.tr("Loser"))
            self.teamResultLabel.setType('lose')
            self.setType('lose')

        self.towerKillsLabel.setText(str(towerKills))
        self.inhibitorKillsLabel.setText(str(inhibitorKills))
        self.baronKillsLabel.setText(str(baronKills))
        self.dragonKillsLabel.setText(str(dragonKills))
        self.riftHeraldKillsLabel.setText(f"{riftHeraldKills} / {hordeKills}")

        self.towerIconLabel.setPicture(towerIcon)
        self.inhibitorIconLabel.setPicture(inhibitorIcon)
        self.baronIconLabel.setPicture(baronIcon)
        self.dragonIconLabel.setPicture(dragonIcon)
        self.riftHeraldIconLabel.setPicture(riftHeraldIcon)

        self.dmgIconLabel.setText("DMG")

        color = "white" if isDarkTheme() else "black"
        self.goldIconLabel.setPicture(f"app/resource/images/Gold_{color}.png")
        self.csIconLabel.setPicture(f"app/resource/images/Minions_{color}.png")

        if len(bans) != 0:
            self.bansButton.setVisible(True)
            self.bansInfo = bans

            if self.bansFlyOut:
                self.bansFlyOut.close()
        else:
            self.bansButton.setVisible(False)
            self.bansInfo = None

        self.csIconLabel.setVisible(True)
        self.goldIconLabel.setVisible(True)

        self.kills.setText(f"{kills}")
        self.deaths.setText(f"{deaths}")
        self.assists.setText(f"{assists}")
        self.slash1.setText("/")
        self.slash2.setText("/")

    def updateSummoners(self, summoners):
        while self.summonersLayout.count():
            item = self.summonersLayout.takeAt(0)
            if item is not None and item.widget():
                item.widget().deleteLater()

        for summoner in summoners:
            infoBar = SummonerInfoBar(summoner)

            self.summonersLayout.addWidget(infoBar, stretch=1)

        if len(summoners) != 5:
            self.summonersLayout.addStretch(5 - len(summoners))
            spacing = self.summonersLayout.spacing()
            self.summonersLayout.addSpacing(spacing * (5 - len(summoners)))

    def __updateIconColor(self, theme: Theme):
        color = "white" if theme == Theme.DARK else "black"
        self.goldIconLabel.setPicture(f"app/resource/images/Gold_{color}.png")
        self.csIconLabel.setPicture(f"app/resource/images/Minions_{color}.png")

    def __onBansButtonClicked(self):
        flyout = BansFlyoutView(self.bansInfo)
        self.bansFlyOut = Flyout.make(flyout, self.bansButton, self)

    def setColor(self, c1, c2, c3, c4):
        # self.setStyleSheet(
        #     f"TeamView {{border-color: {c4.name(QColor.HexArgb)};}}")

        self.titleBarWidget.setStyleSheet(f"""
            #titleBarWidget {{
                background-color: {c1.name(QColor.HexArgb)};
            }}
        """)

        return


class BansFlyoutView(FlyoutViewBase):
    def __init__(self, bans, parent=None):
        super().__init__(parent)
        self.hBoxLayout = QHBoxLayout(self)

        for champion in bans:
            icon = RoundIcon(champion, 25, 0, 3)
            self.hBoxLayout.addWidget(icon)


class SummonerInfoBar(QFrame):
    def __init__(self, summoner, parent=None):
        super().__init__(parent)
        # self._pressedBackgroundColor = self._hoverBackgroundColor

        self.setFixedHeight(39)

        self.hBoxLayout = QHBoxLayout(self)
        self.runeIcon = RoundedLabel(radius=0, borderWidth=0)

        self.spellsLayout = QHBoxLayout()

        self.spell1Icon = RoundedLabel(radius=0, borderWidth=1)
        self.spell2Icon = RoundedLabel(radius=0, borderWidth=1)

        self.levelLabel = QLabel()
        self.championIconLabel = RoundIcon(summoner["championIcon"], 25, 0, 3)
        self.summonerName = SummonerName(
            summoner["summonerName"], isPublic=summoner["isPublic"])

        self.rankIcon = RoundedLabel(radius=0, borderWidth=0)

        self.itemsLayout = QHBoxLayout()
        self.items = []

        self.kills = QLabel()
        self.slash1 = QLabel("/")
        self.deaths = DeathsLabel()
        self.slash2 = QLabel("/")
        self.assists = QLabel()

        self.csLabel = QLabel()
        self.goldLabel = QLabel()
        self.demageLabel = QLabel()

        self.__initWidget(summoner)
        self.__initLayout()

        self.summonerName.clicked.connect(
            lambda: signalBus.toCareerInterface.emit(summoner["puuid"]))

    def __initWidget(self, summoner):
        self.isCurrent = summoner["isCurrent"]
        if self.isCurrent:
            self.setObjectName("currentSummonerWidget")
            self.setProperty("current", True)

        self.summonerName.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        self.summonerName.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Minimum)

        self.runeIcon.setPicture(QPixmap(summoner["runeIcon"]) if summoner.get("runeIcon") else QPixmap())
        self.runeIcon.setFixedSize(23, 23)
        self.spell1Icon.setFixedSize(18, 18)
        self.spell1Icon.setPicture(summoner["spell1Icon"])
        self.spell2Icon.setFixedSize(18, 18)
        self.spell2Icon.setPicture(summoner["spell2Icon"])

        self.levelLabel.setText(str(summoner["champLevel"]))
        self.levelLabel.setObjectName("levelLabel")
        self.levelLabel.setAlignment(Qt.AlignCenter)
        self.levelLabel.setFixedWidth(20)

        self.items = []
        itemIds = summoner.get('itemIds') or [0] * len(summoner['itemIcons'])
        for icon, itemId in zip(summoner['itemIcons'], itemIds):
            # 悬浮显示物品详情
            self.items.append(ItemIcon(icon, itemId, 23, radius=0, borderWidth=2))

        if summoner["rankInfo"]:
            if summoner['rankIcon'] != None and summoner['rankIcon'] != '':
                self.rankIcon.setPicture(summoner["rankIcon"])
                self.rankIcon.setFixedSize(30, 30)

                tier, divison, lp = summoner["tier"], summoner["division"], summoner["lp"]
                if tier != "":
                    self.rankIcon.setToolTip(f"{tier} {divison} {lp}")
                else:
                    self.rankIcon.setToolTip(self.tr("Unranked"))

                self.rankIcon.installEventFilter(
                    ToolTipFilter(self.rankIcon, 0, ToolTipPosition.TOP))
            else:
                self.rankIcon.setText(str(summoner['lp']))
                self.rankIcon.setFixedWidth(40)
                self.rankIcon.setAlignment(Qt.AlignCenter)

        self.kills.setText(f"{summoner['kills']}")
        self.kills.setFixedWidth(20)
        self.kills.setAlignment(Qt.AlignCenter)
        self.deaths.setText(f"{summoner['deaths']}")
        self.deaths.setFixedWidth(20)
        self.deaths.setAlignment(Qt.AlignCenter)
        self.assists.setText(f"{summoner['assists']}")
        self.assists.setFixedWidth(20)
        self.assists.setAlignment(Qt.AlignCenter)

        self.slash1.setFixedWidth(10)
        self.slash1.setAlignment(Qt.AlignCenter)
        self.slash2.setFixedWidth(10)
        self.slash2.setAlignment(Qt.AlignCenter)

        self.csLabel.setText(str(summoner["cs"]))
        self.csLabel.setAlignment(Qt.AlignCenter)
        self.csLabel.setFixedWidth(50)

        self.goldLabel.setText(str(summoner["gold"]))
        self.goldLabel.setAlignment(Qt.AlignCenter)
        self.goldLabel.setFixedWidth(60)

        self.demageLabel.setText(str(summoner["demage"]))
        self.demageLabel.setAlignment(Qt.AlignCenter)
        self.demageLabel.setFixedWidth(70)

    def __initLayout(self):
        self.spellsLayout.setContentsMargins(0, 0, 0, 0)
        self.spellsLayout.setSpacing(0)
        self.spellsLayout.addWidget(self.spell1Icon)
        self.spellsLayout.addWidget(self.spell2Icon)

        self.itemsLayout.setSpacing(0)
        self.spellsLayout.setContentsMargins(0, 0, 0, 0)

        for icon in self.items:
            self.itemsLayout.addWidget(icon)

        self.hBoxLayout.setContentsMargins(8, 0, 6, 2)
        self.hBoxLayout.addWidget(self.runeIcon)
        self.hBoxLayout.addLayout(self.spellsLayout)
        self.hBoxLayout.addWidget(self.levelLabel)
        self.hBoxLayout.addWidget(self.championIconLabel)
        self.hBoxLayout.addWidget(self.summonerName, alignment=Qt.AlignVCenter)
        self.hBoxLayout.addSpacing(4)
        self.hBoxLayout.addSpacerItem(QSpacerItem(
            1, 1, QSizePolicy.Minimum, QSizePolicy.Minimum))
        self.hBoxLayout.addSpacing(5)
        self.hBoxLayout.addWidget(self.rankIcon)
        self.hBoxLayout.addSpacing(5)
        self.hBoxLayout.addLayout(self.itemsLayout)
        self.hBoxLayout.addSpacing(22)
        self.hBoxLayout.addWidget(self.kills)
        self.hBoxLayout.addSpacing(-5)
        self.hBoxLayout.addWidget(self.slash1)
        self.hBoxLayout.addSpacing(-5)
        self.hBoxLayout.addWidget(self.deaths)
        self.hBoxLayout.addSpacing(-5)
        self.hBoxLayout.addWidget(self.slash2)
        self.hBoxLayout.addSpacing(-5)
        self.hBoxLayout.addWidget(self.assists)
        self.hBoxLayout.addSpacing(5)
        self.hBoxLayout.addWidget(self.csLabel)
        self.hBoxLayout.addWidget(self.goldLabel)
        self.hBoxLayout.addWidget(self.demageLabel)


class GameTitleBar(QFrame):
    """
    对局详情标题 (记分表风格), 自绘:

        ▌[地图]  Defeat  Ranked Solo/Duo                              Game ID
        ▌        Summoner's Rift   Duration 32m 39s   Played 2026/10/19 06:53   5662847234  [复制]

    胜负用左侧实色边条 + 浅色底 + 结果色大字; 其余信息为 "灰色标签 + 墨色数值", 不用点号串起来.
    """

    HEIGHT = 72
    MAP = 44

    def __init__(self, type: str = None, parent=None):
        super().__init__(parent)
        self.setFixedHeight(self.HEIGHT)
        # search_interface.qss 里旧的底色 / 边框改由 paintEvent 自绘 (控件自己的样式表优先)
        self.setStyleSheet("GameTitleBar { border: none; background: transparent; }")

        self.titleBarLayout = QHBoxLayout(self)
        self.copyGameIdButton = ToolButton(Icon.COPY)
        self.gameId = None
        self.game = None
        self.mapPixmap = QPixmap()
        self.resultText = ""
        self.type = type or 'lose'

        self.remake = None
        self.win = None

        self.__initWidget()
        self.__initLayout()
        self.__connectSignalToSlot()

    def __initWidget(self):
        self.copyGameIdButton.setVisible(False)
        self.copyGameIdButton.setFixedSize(34, 34)
        self.copyGameIdButton.setToolTip(self.tr("Copy game ID"))
        self.copyGameIdButton.installEventFilter(ToolTipFilter(
            self.copyGameIdButton, 500, ToolTipPosition.LEFT))

    def __initLayout(self):
        self.titleBarLayout.setContentsMargins(0, 0, 16, 0)
        self.titleBarLayout.addStretch(1)
        self.titleBarLayout.addWidget(self.copyGameIdButton)

    def updateTitleBar(self, game):
        isCherry = game["queueId"] == 1700

        self.remake = game['remake']
        self.win = game['win']
        self.type = resultType(game['win'], game['remake'])

        result = {'remake': self.tr("Remake"), 'win': self.tr("Victory"),
                  'lose': self.tr("Defeat")}[self.type]

        if isCherry:
            cherryResult = game["cherryResult"]
            if cherryResult == 1:
                result = self.tr("1st")
            elif cherryResult == 2:
                result = self.tr("2nd")
            elif cherryResult == 3:
                result = self.tr("3rd")
            elif cherryResult == 4:
                result = self.tr("4rd")
            elif cherryResult == 5:
                result = self.tr("5rd")
            elif cherryResult == 6:
                result = self.tr("6rd")
            elif cherryResult == 7:
                result = self.tr("7rd")
            else:
                result = self.tr("8th")

        self.gameId = game['gameId']
        self.game = game
        self.resultText = result
        self.mapPixmap = QPixmap(game['mapIcon']) if game.get('mapIcon') else QPixmap()

        self.copyGameIdButton.setVisible(True)
        self.update()

    def paintEvent(self, e):
        if not self.game:
            return

        game, pal = self.game, palette()
        ink, muted = pal['ink'], pal['muted']
        w, h = self.width(), self.HEIGHT

        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform
                         | QPainter.TextAntialiasing)
        drawResultCard(p, self.rect(), self.type, pal)

        x = 18
        if not self.mapPixmap.isNull():
            p.drawPixmap(QRectF(x, (h - self.MAP) / 2, self.MAP, self.MAP).toRect(),
                         self.mapPixmap)
        x += self.MAP + 14

        # 右侧: 对局 ID (复制按钮左边)
        button = self.copyGameIdButton
        right = (button.x() if button.isVisible() else w) - 12
        idFont = numberFont(14, 400)
        idText = str(self.gameId)
        idW = max(QFontMetrics(idFont).horizontalAdvance(idText),
                  QFontMetrics(textFont(12)).horizontalAdvance(self.tr("Game ID")))
        drawText(p, QRectF(right - idW, 14, idW, 18), self.tr("Game ID"), textFont(12), muted,
                 Qt.AlignRight | Qt.AlignVCenter)
        drawText(p, QRectF(right - idW, 34, idW, 22), idText, idFont, ink,
                 Qt.AlignRight | Qt.AlignVCenter)
        right -= idW + 24

        # 第一行: 结果 (结果色) + 模式; 同一基线
        def baselineRuns(x, baseline, runs):
            for string, font, color in runs:
                p.setFont(font)
                p.setPen(color)
                p.drawText(QPointF(x, baseline), string)
                x += QFontMetrics(font).horizontalAdvance(string)
            return x

        baselineRuns(x, 35, ((self.resultText, textFont(20, True), stripeColor(self.type, pal)),
                             ("   " + game['modeName'], textFont(14, True), ink)))

        # 第二行: 地图 / 时长 / 开始时间, 放不下就从后往前省略
        segments = [[(game['mapName'], textFont(13), ink)]]
        duration = durationText(game.get('durationSecs') or 0) or game.get('gameDuration', '')
        if duration:
            segments.append([(self.tr("Duration") + "  ", textFont(12), muted),
                             (duration, numberFont(13, 400), ink)])
        segments.append([(self.tr("Played") + "  ", textFont(12), muted),
                         (game['gameCreation'], numberFont(13, 400), ink)])
        gap = 22
        while len(segments) > 1 and \
                sum(runsWidth(s) for s in segments) + gap * (len(segments) - 1) > right - x:
            segments.pop()
        for runs in segments:
            x = baselineRuns(x, 57, runs) + gap

        p.end()

    def __connectSignalToSlot(self):
        self.copyGameIdButton.clicked.connect(
            lambda: pyperclip.copy(self.gameId))


class GamesView(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.vBoxLayout = QVBoxLayout(self)
        self.stackWidget = QStackedWidget()

        self.loadingPage = QWidget()
        self.infoPage = QWidget()

        self.infoLayout = QHBoxLayout(self.infoPage)
        self.gamesTab = GamesTab()
        self.gameDetailView = GameDetailView()

        self.loadingLayout = QHBoxLayout(self.loadingPage)
        self.processRing = IndeterminateProgressRing()

        self.__initLayout()

    def __initLayout(self):
        self.infoLayout.setContentsMargins(0, 0, 0, 0)
        self.infoLayout.setSpacing(0)

        self.infoLayout.addWidget(self.gamesTab)
        self.infoLayout.addWidget(self.gameDetailView)

        self.loadingLayout.addWidget(self.processRing)
        self.stackWidget.addWidget(self.loadingPage)
        self.stackWidget.addWidget(self.infoPage)

        self.vBoxLayout.setContentsMargins(0, 0, 0, 0)
        self.vBoxLayout.addWidget(self.stackWidget)
        self.stackWidget.setCurrentIndex(1)

    def setLoadingPageEnable(self, enable):
        index = 0 if enable else 1
        self.stackWidget.setCurrentIndex(index)


class GameTab(QWidget):
    """
    左侧对局列表的一行 ("赛后记分表" 风格), 整行自绘:

        ▌(英雄)  Solo/Duo        3h
        ▌        8 / 13 / 8

    胜负由左侧实色边条 + 浅色底表达; 悬停加深, 选中再加深并描结果色边.
    选中态沿用外部的 setProperty('selected', ...) 约定, 点击发 signalBus.gameTabClicked.
    """
    clicked = Signal()

    HEIGHT = 52
    ICON = 32
    STRIPE = 4

    def __init__(self, game=None, parent=None):
        super().__init__(parent)
        self.setFixedHeight(self.HEIGHT)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)
        self.setProperty("selected", False)

        self.game = game
        self.gameId = game["gameId"]
        self.remake = game['remake']
        self.win = game['win']
        self.resultType = resultType(game['win'], game['remake'])
        self.championPixmap = QPixmap(game["championIcon"])
        self.modeName = queueShortName(game.get('queueId'), game["name"])
        self.isHover = False
        self._cache = self._cacheKey = None

        self.setToolTip(f"{game['name']}\n{game.get('time', '')}".strip())

        self.clicked.connect(lambda: signalBus.gameTabClicked.emit(self))

    # ---------------- 交互 ----------------

    def event(self, e):
        # 选中 / 取消选中都是外部 setProperty('selected') + polish, 这里负责重绘
        if e.type() == QEvent.DynamicPropertyChange:
            self.update()
        return super().event(e)

    def enterEvent(self, e):
        self.isHover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.isHover = False
        self.update()
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        e.accept()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
        e.accept()

    # ---------------- 绘制 ----------------

    def paintEvent(self, e):
        pal = palette()
        selected = bool(self.property("selected"))

        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        drawResultCard(p, self.rect(), self.resultType, pal, radius=6, stripe=self.STRIPE,
                       tintAlpha=46 if selected else (30 if self.isHover else 15))
        if selected:
            p.setPen(QPen(stripeColor(self.resultType, pal), 1.5))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(QRectF(self.rect()).adjusted(0.75, 0.75, -0.75, -0.75), 6, 6)

        ago = compactAgo(self.game['timeStamp'], self.game.get('shortTime', ''))
        p.drawPixmap(0, 0, cachedContent(self, self.__draw, (ago,)))
        p.end()

    def __draw(self, p, pal):
        game, w, h = self.game, self.width(), self.height()
        ink, muted = pal['ink'], pal['muted']

        x = self.STRIPE + 8
        icon = QRectF(x, (h - self.ICON) / 2, self.ICON, self.ICON)
        drawChampion(p, self.championPixmap, icon, pal['gold'], 1.5, crop=3)

        # 第一行: 模式简称 + 多久前 (右对齐)
        tx, right = icon.right() + 9, w - 9
        ago = compactAgo(game['timeStamp'], game.get('shortTime', ''))
        agoFont = textFont(12)
        agoW = QFontMetrics(agoFont).horizontalAdvance(ago)
        drawText(p, QRectF(right - agoW, h / 2 - 21, agoW, 20), ago, agoFont, muted,
                 Qt.AlignRight | Qt.AlignVCenter)
        nameFont = textFont(13, True)
        name = QFontMetrics(nameFont).elidedText(self.modeName, Qt.ElideRight,
                                                 int(right - agoW - 6 - tx))
        drawText(p, QRectF(tx, h / 2 - 21, right - tx, 20), name, nameFont, ink)

        # 第二行: K / D / A (记分牌数字)
        bold, thin = numberFont(15), numberFont(15, 300)
        drawRuns(p, tx, h / 2 + 1, 20, ((str(game['kills']), bold, ink),
                                        (" / ", thin, muted),
                                        (str(game['deaths']), bold, deathsColor()),
                                        (" / ", thin, muted),
                                        (str(game['assists']), bold, ink)))


class SearchInterface(SeraphineInterface):
    summonerPuuidGetted = Signal(str)
    gamesNotFound = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)

        self.puuid = 0
        self.gameLoadingTask: asyncio.Task = None

        self.vBoxLayout = QVBoxLayout(self)

        self.searchLayout = QHBoxLayout()
        self.searchLineEdit = SearchLineEdit()
        self.careerButton = PushButton(self.tr("Career"))
        self.filterComboBox = ComboBox()

        self.gamesView = GamesView()
        self.currentSummonerName = None

        self.detailViewLoadTask = None
        self.loadingGameId = 0

        self.loadFirstPageLock = asyncio.Lock()

        self.__initWidget()
        self.__initLayout()
        self.__connectSignalToSlot()

    def __initWidget(self):
        self.searchLineEdit.setAlignment(Qt.AlignCenter)
        self.searchLineEdit.setClearButtonEnabled(True)
        self.searchLineEdit.setPlaceholderText(
            self.tr("Please input summoner name"))
        self.careerButton.setEnabled(False)
        self.filterComboBox.setEnabled(False)

        StyleSheet.SEARCH_INTERFACE.apply(self)

        self.filterComboBox.addItems([
            self.tr('All'),
            self.tr('Normal'),
            self.tr("A.R.A.M."),
            self.tr("Ranked Solo"),
            self.tr("Ranked Flex")
        ])
        self.filterComboBox.setCurrentIndex(0)

    def __initLayout(self):
        self.searchLayout.addWidget(self.searchLineEdit)
        self.searchLayout.addSpacing(5)
        self.searchLayout.addWidget(self.careerButton)
        self.searchLayout.addWidget(self.filterComboBox)

        self.vBoxLayout.addLayout(self.searchLayout)
        self.vBoxLayout.addSpacing(5)
        self.vBoxLayout.addWidget(self.gamesView)
        self.vBoxLayout.setContentsMargins(30, 32, 30, 30)

    def setEnabled(self, a0: bool) -> None:
        self.gamesView.gameDetailView.clear()
        self.gamesView.gamesTab.clear()
        self.searchLineEdit.clear()

        self.searchLineEdit.setEnabled(a0)
        self.searchLineEdit.searchButton.setEnabled(a0)

        if not a0:
            self.filterComboBox.setEnabled(a0)

        return super().setEnabled(a0)

    def __connectSignalToSlot(self):
        self.searchLineEdit.searchButton.setShortcut(Qt.Key.Key_Return)
        self.searchLineEdit.searchButton.clicked.connect(
            self.onSearchButtonClicked)

        self.careerButton.clicked.connect(
            lambda: signalBus.toCareerInterface.emit(self.puuid))
        self.filterComboBox.currentIndexChanged.connect(
            self.__onFilterComboxChanged)

        signalBus.gameTabClicked.connect(self.__onGameTabClicked)

    def __showSummonerNotFoundMsg(self):
        InfoBar.error(
            title=self.tr("Summoner not found"),
            content=self.tr("Please check the summoner's name and retry"),
            orient=Qt.Vertical,
            duration=5000,
            position=InfoBarPosition.BOTTOM_RIGHT,
            parent=self
        )

    # Fix: 超快速的在候选栏选中两次同样puuid会起两个task加载战绩, 100%干掉客户端 -- By Hpero4
    @asyncLockDecorator('loadFirstPageLock')
    async def searchAndShowFirstPage(self, puuid=None):
        name = self.searchLineEdit.text()
        if name == "":
            return False

        if re.match(r"\S+-\S+-\S+-\S+-\S+", name):
            summoner = await connector.getSummonerByPuuid(name)
        else:
            summoner = await connector.getSummonerByName(name)

        if 'errorCode' in summoner:
            self.__showSummonerNotFoundMsg()
            return False

        self.gamesView.setLoadingPageEnable(True)

        self.careerButton.setEnabled(True)
        self.filterComboBox.setEnabled(True)

        self.gamesView.gameDetailView.clear()

        # NOTE 如果是生涯和搜索反复横跳, 就不重新启 loadgames 任务了
        if puuid != self.puuid:
            if self.gameLoadingTask:
                self.gameLoadingTask.cancel()

                while not self.gameLoadingTask.cancelled() \
                        and not self.gameLoadingTask.done():
                    await asyncio.sleep(.3)

            self.puuid = summoner['puuid']
            self.gamesView.gamesTab.clear()

            # 先加载两页，让用户看着
            try:
                games = await connector.getSummonerGamesByPuuid(self.puuid, 0, 19)
            except SummonerGamesNotFound:
                games = []
            else:
                games = await parseGamesDataConcurrently(games['games'])

            if len(games) == 0:
                self.gamesView.gamesTab.nextButton.setVisible(False)
                self.gamesView.gamesTab.prevButton.setVisible(False)
                self.filterComboBox.setEnabled(False)
                self.gamesView.setLoadingPageEnable(False)
                return False

            self.gamesView.gamesTab.updateQueueIdMap(games)

            # 启动任务，往 gamesTab 里丢数据
            # NOTE 既然创建新任务, 并且刷新了self.puuid 就应该用self的, 否则就违背了loadGames判断的初衷

            self.gameLoadingTask = asyncio.create_task(
                self.__loadGames(self.puuid))

        self.gamesView.gamesTab.showTheFirstPage()
        self.gamesView.setLoadingPageEnable(False)

        self.__addSearchHistroy(name)

        return True

    def waitingForDrawSelect(self, gameId):
        '''
        从生涯界面点下面进来的时候，绘制选中的提示框
        '''
        tabs = self.gamesView.gamesTab

        # 遍历一下目前已经画好的第一页有没有这个 tab，有的话就直接画上
        # FIXME -- By Hpero4
        #  如果在绘制前Tabs的StackWidget改变, 会导致画错框甚至找不到绘制对象 AttributeError
        #  必须保证绘制时界面没有被改变; (目前暂时是将耗时操作移到画框后面)
        # FIXME -- By Hpero4
        #  如果选中的对局不在第一页中(> 11), 将不会画上选择框
        layout = tabs.stackWidget.widget(1).layout()
        for i in range(layout.count()):
            item = layout.itemAt(i)

            if not item.widget():
                continue

            widget: GameTab = item.widget()

            if widget.gameId == int(gameId):
                tabs.currentTabSelected = widget
                widget.setProperty("selected", True)
                widget.style().polish(widget)
                widget.repaint()
            else:
                widget.setProperty("selected", False)
                widget.style().polish(widget)

        if not tabs.currentTabSelected:
            tabs.waitingForSelect = gameId

    @asyncSlot()
    async def onSearchButtonClicked(self):
        if not await self.searchAndShowFirstPage():
            return

        self.filterComboBox.setCurrentIndex(0)  # 将筛选条件回调至"全部" -- By Hpero4
        self.gamesView.gamesTab.clickFirstTab()

    async def __loadGames(self, puuid):
        begIdx = 20
        endIdx = 29

        logger.debug(f"welcome load games task: {puuid}", TAG)

        # NOTE 换了查询目标, 若之前正在查, 先等 task 被 release 掉 -- By Hpero4
        while self.gameLoadingTask \
                and not self.gameLoadingTask.done() \
                and puuid != self.puuid:
            await asyncio.sleep(.2)

        logger.debug(f"start load {puuid}", TAG)
        # 连续查多个人时, 将前面正在查的task给release掉
        while self.puuid == puuid:
            # 为加载战绩详情让行
            while (
                (self.detailViewLoadTask and not self.detailViewLoadTask.done())
                or
                (self.window().careerInterface.loadGamesTask and not self.window(
                ).careerInterface.loadGamesTask.done())
            ):
                await asyncio.sleep(.2)

            t1 = time.time()
            try:
                games = await connector.getSummonerGamesByPuuidSlowly(
                    puuid, begIdx, endIdx)
            except SummonerGamesNotFound:
                # TODO 这里可以弹个窗  -- By Zzaphkiel
                # NOTE 触发 SummonerGamesNotFound 时, 异常信息会通过 connector 下发到 main_window 的 __onShowLcuConnectError
                #  理论上会有弹框提示  -- By Hpero4
                return
            t2 = time.time()

            logger.debug(
                f"load games {self.puuid} [{begIdx}-{endIdx}] finish {t2-t1}s", TAG)
            # 1000 局搜完了，或者正好上一次就是最后
            # 在切换了puuid时, 就不要再把数据刷到Games上了 -- By Hpero4
            if games['gameCount'] == 0 or self.puuid != puuid:
                return

            # 处理数据，交给 gamesTab，更新其 games 成员以及 queueIdMap
            games = await parseGamesDataConcurrently(games['games'])

            if self.puuid != puuid:
                return

            self.gamesView.gamesTab.updateQueueIdMap(games)

            # 如果用户下一页点得太猛，在还没加载完的时候点到了能绘制的最后一页
            # 由于 __onNextButtonClicked 的逻辑，在用户进入最后一页时，nextButton 会被设置为不可用。
            # 而现在，由于新的两页对局数据加载好了，可以绘制上去了，要让 button 变得可用
            self.gamesView.gamesTab.resetButtonEnabled()

            # 如果长度小于 10，也说明搜完了已经
            if len(games) < 10:
                return

            begIdx = endIdx + 1
            endIdx = begIdx + 9

            # 睡不睡都行
            await asyncio.sleep(.1)

    @asyncSlot(QWidget)
    async def __onGameTabClicked(self, tab: GameTab):
        tabs: GamesTab = self.gamesView.gamesTab
        cur: GameTab = tabs.currentTabSelected

        if tab is cur:
            return

        self.gamesView.gameDetailView.clear()

        tab.setProperty("selected", True)
        tab.style().polish(tab)

        if cur:
            cur.setProperty("selected", False)
            cur.style().polish(cur)

        tabs.currentTabSelected = tab
        self.loadingGameId = tab.gameId
        await self.updateGameDetailView(tab.gameId, self.puuid)

    async def updateGameDetailView(self, gameId, puuid):
        self.gamesView.gameDetailView.setLoadingPageEnabled(True)

        try:
            # NOTE self.detailViewLoadTask 用于标记详情正在加载, self.loadGame会为其让行 -- By Hpero4
            self.detailViewLoadTask = asyncio.create_task(
                connector.getGameDetailByGameId(gameId))
            game = await self.detailViewLoadTask

            # 加载GameDetail的过程中, 切换了搜索对象(self.puuid变更), 将后续任务pass -- By Hpero4
            if puuid != self.puuid:
                return

            self.detailViewLoadTask = asyncio.create_task(
                parseGameDetailData(puuid, game))
            game = await self.detailViewLoadTask
            self.gamesView.gameDetailView.updateGame(game)
        except CancelledError:
            raise
        except BaseException:
            # 拉取/解析对局详情失败时, 之前没有任何异常处理 -> 详情面板静默空白 (见 issue #7).
            # 现在记录 traceback, 并通过 updateGame(None) 复用已有的 "Get game infomation failed" 提示.
            logger.error(
                f"Failed to load game detail, gameId={gameId}: "
                f"{traceback.format_exc()}", TAG)
            self.gamesView.gameDetailView.updateGame(None)
        finally:
            self.gamesView.gameDetailView.setLoadingPageEnabled(False)

    @asyncSlot(int)
    async def __onFilterComboxChanged(self, index):
        self.gamesView.gameDetailView.clear()
        tabs = self.gamesView.gamesTab
        tabs.clearTabs()

        ids = (-1, 430, 450, 420, 440)
        tabs.queueId = ids[index]

        self.gamesView.setLoadingPageEnable(True)

        # 若后台加载已结束仍不足 10 局, 说明该队列本就少于 10 局, 必须退出避免死循环
        while len(self.gamesView.gamesTab.queueIdMap.get(tabs.queueId, [])) < 10:
            if self.gameLoadingTask is None or self.gameLoadingTask.done():
                break
            await asyncio.sleep(.2)

        enable = tabs.queueIdMap.get(tabs.queueId) is not None
        tabs.prevButton.setVisible(enable)
        tabs.nextButton.setVisible(enable)
        tabs.nextButton.setVisible(enable)

        self.gamesView.setLoadingPageEnable(False)

        if enable:
            self.gamesView.gamesTab.showTheFirstPage()

    def __addSearchHistroy(self, name):
        history: list = cfg.get(cfg.searchHistory).split(',')

        if name in history:
            history.remove(name)

        history.insert(0, name)
        cfg.set(cfg.searchHistory, ",".join(
            [t for t in history if t][:10]), True)
