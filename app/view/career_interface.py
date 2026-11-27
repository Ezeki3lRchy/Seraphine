import asyncio
from qasync import asyncSlot
import pyperclip
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QVBoxLayout, QSpacerItem,
                             QSizePolicy, QTableWidgetItem, QHeaderView,
                             QWidget, QFrame, QStackedWidget)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from ..common.qfluentwidgets import (TableWidget, PushButton, ComboBox,
                                     SmoothScrollArea, ToolTipFilter, setCustomStyleSheet,
                                     ToolTipPosition, ToolButton, IndeterminateProgressRing,
                                     Flyout, FlyoutViewBase, FlyoutAnimationType, InfoBar,
                                     InfoBarPosition, SegmentedWidget, FluentIcon, setFont)

from app.components.career_widgets import RankCard, RecentSummaryCard, CareerGameRow, tierText
from app.components.performance_widget import PerformancePanel
from app.components.play_style_widget import PlayStyleView
from app.components.champion_stats_widget import ChampionStatsView
from app.lol import season_stats, play_style, champion_stats
from app.components.champion_icon_widget import RoundIcon
from app.components.profile_level_icon_widget import RoundLevelAvatar
from app.components.summoner_name_button import SummonerName
from app.components.tier_graph_widget import TierGraphCard
from app.components.save_star_button import SaveStarButton
from app.lol import rank_history, saved_players
from app.lol.laning import attachLaningScores
from app.lol.avg_rank import attachAverageRanks
from app.components.color_label import ColorLabel
from app.components.animation_frame import ColorAnimationFrame
from app.common.style_sheet import StyleSheet
from app.common.icons import Icon
from app.common.signals import signalBus
from app.common.config import cfg
from app.common.logger import logger
from app.lol.connector import connector
from app.lol.tools import (parseGames, parseSummonerData, attachCareerDetails,
                           getRecentTeammates, parseDetailRankInfo)
from ..components.seraphine_interface import SeraphineInterface

TAG = "CareerInterface"


class NameLabel(QLabel):
    def text(self) -> str:
        return super().text().replace("🔒", '')


class TagLineLabel(QLabel):
    def text(self) -> str:
        return super().text().replace(" ", '')


class CareerInterface(SeraphineInterface):
    gameInfoBarClicked = Signal(str)
    iconLevelExpChanged = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.loginSummonerPuuid = None
        self.puuid = None
        self.showTagLine = False
        self.recentTeammatesInfo = None

        self.vBoxLayout = QVBoxLayout(self)
        self.IconNameHBoxLayout = QHBoxLayout()
        self.nameLevelVLayout = QVBoxLayout()
        self.icon = RoundLevelAvatar('app/resource/images/champion-0.png',
                                     0,
                                     1,
                                     parent=self)
        self.name = NameLabel(self.tr("Connecting..."))
        # self.serviceLabel = QLabel()
        self.tagLineLabel = TagLineLabel()
        self.copyButton = ToolButton(Icon.COPY)
        self.saveButton = SaveStarButton()
        self.nameButtonLayout = QHBoxLayout()
        self.nameTagLineLayout = QVBoxLayout()
        self.subtitleLayout = QHBoxLayout()

        self.buttonsLayout = QVBoxLayout()
        self.backToMeButton = PushButton(self.tr("Back to me"))
        self.refreshButton = PushButton(self.tr("Refresh"))
        self.searchButton = PushButton(self.tr("Game history"))

        self.tableLayout = QHBoxLayout()
        self.rankInfo = None
        self.rankTable = TableWidget(self)

        # OP.GG 风格: 单双 / 灵活段位卡 + 近期对局概况, 与战绩一起在滚动区里
        self.rankCardsLayout = QHBoxLayout()
        self.soloCard = RankCard(self.tr("Ranked Solo/Duo"))
        self.flexCard = RankCard(self.tr("Ranked Flex"))
        # 5 人排位 (LCU: RANKED_PREMADE_5x5), 有数据才显示, 放在第二行
        self.fivesRankLayout = QHBoxLayout()
        self.fivesCard = RankCard(self.tr("Ranked 5s"))
        self.tierGraphCard = TierGraphCard()
        self.summaryCard = RecentSummaryCard()
        # 近期 vs 本赛季 (赛季样本在页面显示后后台翻页拉取)
        self.performancePanel = PerformancePanel()
        # Overview (everything above) / Play style (season sample statistics); the choice
        # is kept when another summoner is opened
        self.viewSwitch = SegmentedWidget()
        self.careerMode = 'overview'
        self.playStyleView = PlayStyleView()
        self.playStyleArea = SmoothScrollArea()
        self.playStyleContent = QWidget()
        self.playStyleLayout = QVBoxLayout(self.playStyleContent)
        # Champions (per-champion season rows with lane matchups, same sample)
        self.championsView = ChampionStatsView()
        self.championsArea = SmoothScrollArea()
        self.championsContent = QWidget()
        self.championsLayout = QVBoxLayout(self.championsContent)
        self.seasonTask = None
        self.playStyleIconTask = None
        self.championIconTasks = []
        self.championIconIds = set()  # icons loaded or in flight for this summoner
        self.laningTask = None
        self.avgRankTask = None
        self.teammatesTask = None
        self.detailsTask = None  # second stage: full game per row, then the tasks above
        # Bumped by every updateInterface call; only the newest load may paint
        self._loadSeq = 0
        self.scrollContent = QWidget()
        self.scrollContentLayout = QVBoxLayout(self.scrollContent)

        self.recentInfoHLayout = QHBoxLayout()
        self.recent20GamesLabel = QLabel(
            self.tr('Recent matches') + " " + self.tr('(Last') + " None " +
            self.tr('games)'))
        self.winsLabel = ColorLabel(self.tr("Wins:") + " None", 'win')
        self.lossesLabel = ColorLabel(self.tr("Losses:") + " None", 'lose')
        self.kdaLabel = QLabel(self.tr("KDA:") + " None / None / None" + self.tr("(") + "0" + self.tr(")"))
        self.championsCard = ChampionsCard()
        self.recentTeamButton = PushButton(self.tr("Recent teammates"))
        self.filterComboBox = ComboBox()
        self.recentTeammatesFlyout: Flyout = None

        self.gameInfoAreaLayout = QHBoxLayout()
        self.gameInfoArea = SmoothScrollArea()
        self.gameInfoLayout = QVBoxLayout()
        self.gameInfoWidget = QWidget()

        self.progressRing = IndeterminateProgressRing()

        self.games = []

        self.loadGamesTask = None

        self.__initWidget()
        self.__initLayout()
        self.__connectSignalToSlot()

    def __initWidget(self):
        # self.serviceLabel.setAlignment(Qt.AlignRight)
        # self.serviceLabel.setObjectName("tagLineLabel")
        # self.serviceLabel.setContentsMargins(0, 0, 5, 0)

        self.tagLineLabel.setVisible(False)
        self.tagLineLabel.setAlignment(Qt.AlignCenter)

        self.copyButton.setFixedSize(26, 26)
        self.copyButton.setEnabled(False)
        self.copyButton.setToolTip(self.tr("Copy summoner name to ClipBoard"))
        self.copyButton.installEventFilter(
            ToolTipFilter(self.copyButton, 500, ToolTipPosition.TOP))
        self.saveButton.setFixedSize(26, 26)

        self.name.setObjectName("name")
        self.name.setAlignment(Qt.AlignCenter)
        self.tagLineLabel.setObjectName("tagLineLabel")
        self.nameLevelVLayout.setObjectName("nameLevelVLayout")

        self.recent20GamesLabel.setObjectName('rencent20GamesLabel')
        self.winsLabel.setObjectName('winsLabel')
        self.lossesLabel.setObjectName('lossesLabel')

        self.kdaLabel.setObjectName('kdaLabel')
        self.recentInfoHLayout.setObjectName("recentInfoHLayout")
        self.gameInfoArea.setObjectName('gameInfoArea')
        self.gameInfoWidget.setObjectName("gameInfoWidget")
        self.scrollContent.setObjectName("gameInfoWidget")
        self.soloCard.setObjectName("careerCard")
        self.flexCard.setObjectName("careerCard")
        self.fivesCard.setObjectName("careerCard")
        self.fivesCard.hide()
        self.summaryCard.setObjectName("careerCard")

        self.backToMeButton.setEnabled(False)

        self.viewSwitch.addItem('overview', self.tr("Overview"),
                                lambda: self.__setCareerMode('overview'), FluentIcon.HOME)
        self.viewSwitch.addItem('playStyle', self.tr("Play style"),
                                lambda: self.__setCareerMode('playStyle'), FluentIcon.PIE_SINGLE)
        self.viewSwitch.addItem('champions', self.tr("Champions"),
                                lambda: self.__setCareerMode('champions'), FluentIcon.PEOPLE)
        self.viewSwitch.setCurrentItem('overview')
        # a bigger, bolder switch than the default so the second view is not missed
        for item in self.viewSwitch.items.values():
            setFont(item, 14, QFont.DemiBold)
            item.setMinimumWidth(150)
            item.setFixedHeight(38)
        self.playStyleArea.setObjectName('gameInfoArea')
        self.playStyleContent.setObjectName("gameInfoWidget")
        self.championsArea.setObjectName('gameInfoArea')
        self.championsContent.setObjectName("gameInfoWidget")

        self.recentTeamButton.setEnabled(True)

        self.rankTable.setRowCount(2)
        self.rankTable.setColumnCount(9)
        self.rankTable.verticalHeader().hide()
        self.rankTable.setWordWrap(False)
        self.rankTable.setHorizontalHeaderLabels([
            self.tr('Game Type'),
            self.tr('Total'),
            self.tr('Win Rate'),
            self.tr('Wins'),
            self.tr('Losses'),
            self.tr('Tier'),
            self.tr('LP'),
            self.tr("Highest tier"),
            self.tr("Previous end tier"),
        ])

        self.rankInfo = [[
            self.tr('Ranked Solo'),
        ], [
            self.tr('Ranked Flex'),
        ]]

        self.filterComboBox.addItems([
            self.tr('All'),
            self.tr('Normal'),
            self.tr("A.R.A.M."),
            self.tr("Ranked Solo"),
            self.tr("Ranked Flex")
        ])
        self.filterComboBox.setCurrentIndex(0)
        self.winsLabel.setToolTip(
            self.tr("Remakes or Customs do not count in statistics"))
        self.winsLabel.installEventFilter(
            ToolTipFilter(self.winsLabel, 500, ToolTipPosition.TOP))
        self.lossesLabel.setToolTip(
            self.tr("Remakes or Customs do not count in statistics"))
        self.lossesLabel.installEventFilter(
            ToolTipFilter(self.lossesLabel, 500, ToolTipPosition.TOP))
        self.kdaLabel.setToolTip(
            self.tr("Remakes or Customs do not count in statistics"))
        self.kdaLabel.installEventFilter(
            ToolTipFilter(self.kdaLabel, 500, ToolTipPosition.TOP))

        self.__updateTable()

        StyleSheet.CAREER_INTERFACE.apply(self)
        self.initTableStyle()

    def __initLayout(self):
        self.subtitleLayout.setContentsMargins(0, 0, 0, 0)
        # self.subtitleLayout.addWidget(self.serviceLabel)
        self.subtitleLayout.addWidget(self.tagLineLabel)

        self.nameTagLineLayout.setContentsMargins(0, 0, 0, 0)
        self.nameTagLineLayout.addWidget(self.name)
        self.nameTagLineLayout.addLayout(self.subtitleLayout)
        self.nameTagLineLayout.setSpacing(0)

        self.nameButtonLayout.setContentsMargins(0, 0, 0, 0)
        self.nameButtonLayout.addLayout(self.nameTagLineLayout)
        self.nameButtonLayout.addSpacing(5)
        self.nameButtonLayout.addWidget(self.copyButton)
        self.nameButtonLayout.addWidget(self.saveButton)

        self.nameLevelVLayout.addSpacerItem(
            QSpacerItem(1, 25, QSizePolicy.Minimum, QSizePolicy.Fixed))
        self.nameLevelVLayout.addLayout(self.nameButtonLayout)
        # self.nameLevelVLayout.addWidget(self.level, alignment=Qt.AlignCenter)
        self.nameLevelVLayout.addSpacerItem(
            QSpacerItem(1, 25, QSizePolicy.Minimum, QSizePolicy.Fixed))

        self.recentInfoHLayout.setSpacing(15)
        self.recentInfoHLayout.addWidget(self.recent20GamesLabel,
                                         alignment=Qt.AlignCenter)
        self.recentInfoHLayout.addWidget(self.winsLabel,
                                         alignment=Qt.AlignCenter)
        self.recentInfoHLayout.addWidget(self.lossesLabel,
                                         alignment=Qt.AlignCenter)
        self.recentInfoHLayout.addWidget(self.kdaLabel,
                                         alignment=Qt.AlignCenter)
        self.recentInfoHLayout.addSpacerItem(
            QSpacerItem(1, 1, QSizePolicy.Expanding, QSizePolicy.Minimum))
        self.recentInfoHLayout.addWidget(
            self.championsCard, alignment=Qt.AlignCenter)
        self.recentInfoHLayout.addWidget(
            self.recentTeamButton, alignment=Qt.AlignCenter)
        self.recentInfoHLayout.addWidget(self.filterComboBox,
                                         alignment=Qt.AlignCenter)

        # 这俩玩意的高度居然不一样，看着难受，手动让它俩一样
        # 33 == self.filterComboBox.height()
        self.recentTeamButton.setFixedHeight(33)

        self.IconNameHBoxLayout.addSpacing(
            self.backToMeButton.sizeHint().width())
        self.IconNameHBoxLayout.addItem(
            QSpacerItem(1, 1, QSizePolicy.Expanding, QSizePolicy.Minimum))
        self.IconNameHBoxLayout.addWidget(self.icon)
        self.IconNameHBoxLayout.addSpacing(20)
        self.IconNameHBoxLayout.addLayout(self.nameLevelVLayout)
        self.IconNameHBoxLayout.addItem(
            QSpacerItem(1, 1, QSizePolicy.Expanding, QSizePolicy.Minimum))

        self.buttonsLayout.addWidget(self.backToMeButton)
        self.buttonsLayout.addWidget(self.refreshButton)
        self.buttonsLayout.addWidget(self.searchButton)
        self.IconNameHBoxLayout.addLayout(self.buttonsLayout)

        self.gameInfoWidget.setLayout(self.gameInfoLayout)
        self.gameInfoLayout.setContentsMargins(0, 0, 0, 0)

        self.rankCardsLayout.setContentsMargins(0, 0, 0, 0)
        self.rankCardsLayout.setSpacing(12)
        self.rankCardsLayout.addWidget(self.soloCard)
        self.rankCardsLayout.addWidget(self.flexCard)
        self.fivesRankLayout.setContentsMargins(0, 0, 0, 0)
        self.fivesRankLayout.setSpacing(12)
        self.fivesRankLayout.addWidget(self.fivesCard, 1)
        self.fivesRankLayout.addStretch(1)

        self.scrollContentLayout.setContentsMargins(0, 0, 0, 0)
        self.scrollContentLayout.setSpacing(10)
        self.scrollContentLayout.addLayout(self.rankCardsLayout)
        self.scrollContentLayout.addLayout(self.fivesRankLayout)
        self.scrollContentLayout.addWidget(self.tierGraphCard)
        self.scrollContentLayout.addWidget(self.summaryCard)
        self.scrollContentLayout.addWidget(self.performancePanel)
        self.scrollContentLayout.addLayout(self.recentInfoHLayout)
        self.scrollContentLayout.addWidget(self.gameInfoWidget, stretch=1)

        self.gameInfoArea.setWidget(self.scrollContent)
        self.gameInfoArea.setWidgetResizable(True)
        self.gameInfoArea.setViewportMargins(0, 0, 5, 0)

        self.vBoxLayout.addWidget(self.progressRing, alignment=Qt.AlignCenter)

        self.playStyleLayout.setContentsMargins(0, 0, 0, 0)
        self.playStyleLayout.addWidget(self.playStyleView)
        self.playStyleLayout.addStretch(1)
        self.playStyleArea.setWidget(self.playStyleContent)
        self.playStyleArea.setWidgetResizable(True)
        self.playStyleArea.setViewportMargins(0, 0, 5, 0)

        self.championsLayout.setContentsMargins(0, 0, 0, 0)
        self.championsLayout.addWidget(self.championsView)
        self.championsLayout.addStretch(1)
        self.championsArea.setWidget(self.championsContent)
        self.championsArea.setWidgetResizable(True)
        self.championsArea.setViewportMargins(0, 0, 5, 0)

        self.vBoxLayout.addLayout(self.IconNameHBoxLayout)
        self.vBoxLayout.addSpacing(12)
        self.vBoxLayout.addWidget(self.viewSwitch, alignment=Qt.AlignLeft)
        self.vBoxLayout.addSpacing(10)
        self.vBoxLayout.addWidget(self.gameInfoArea)
        self.vBoxLayout.addWidget(self.playStyleArea)
        self.vBoxLayout.addWidget(self.championsArea)
        self.vBoxLayout.addSpacing(10)

        self.vBoxLayout.setContentsMargins(30, 32, 30, 20)

        self.setLoadingPageEnabled(True)

    def setLoadingPageEnabled(self, enable):
        self.gameInfoArea.delegate.vScrollBar.resetValue(0)
        self.gameInfoArea.verticalScrollBar().setSliderPosition(0)

        self.icon.setVisible(not enable)
        self.name.setVisible(not enable)
        self.copyButton.setVisible(not enable)
        self.saveButton.setVisible(not enable)
        self.refreshButton.setVisible(not enable)
        self.backToMeButton.setVisible(not enable)
        self.searchButton.setVisible(not enable)
        # 段位表 / 胜负 / KDA / 常用英雄 已由段位卡与概况卡取代
        self.rankTable.setVisible(False)
        self.championsCard.setVisible(False)
        self.winsLabel.setVisible(False)
        self.lossesLabel.setVisible(False)
        self.kdaLabel.setVisible(False)
        self.recent20GamesLabel.setVisible(not enable)
        self.filterComboBox.setVisible(not enable)
        self.recentTeamButton.setVisible(not enable)
        self.gameInfoArea.setVisible(not enable and self.careerMode == 'overview')
        self.playStyleArea.setVisible(not enable and self.careerMode == 'playStyle')
        self.championsArea.setVisible(not enable and self.careerMode == 'champions')
        self.viewSwitch.setVisible(not enable)
        self.tagLineLabel.setVisible(not enable and self.showTagLine)

        self.progressRing.setVisible(enable)

    def __setCareerMode(self, mode):
        self.careerMode = mode
        self.gameInfoArea.setVisible(mode == 'overview')
        self.playStyleArea.setVisible(mode == 'playStyle')
        self.championsArea.setVisible(mode == 'champions')

    def __updateTable(self):
        for i, line in enumerate(self.rankInfo):
            for j, data in enumerate(line):
                item = QTableWidgetItem(data)
                item.setTextAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.rankTable.setItem(i, j, item)

        self.rankTable.resizeColumnsToContents()
        self.rankTable.resizeRowsToContents()
        # self.table.setFixedWidth(self.table.viewportSizeHint().width())
        self.rankTable.setFixedHeight(
            self.rankTable.viewportSizeHint().height() + 4)
        self.rankTable.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch)

    def initTableStyle(self):
        light = '''
            QHeaderView::section:horizontal {
                border: none;
                border-bottom: 1px solid rgba(0, 0, 0, 0.095);
            }

            QTableView {
                border: 1px solid rgba(0, 0, 0, 0.095); 
                border-radius: 6px;
                background: rgba(255, 255, 255, 0.667);
            }
        '''

        dark = '''
            QHeaderView::section:horizontal {
                border: none;
                border-bottom: 1px solid rgb(35, 35, 35);
            }

            QTableView {
                border: 1px solid rgb(35, 35, 35); 
                border-radius: 6px;
                background: rgba(255, 255, 255, 0.051);
            }
        '''

        setCustomStyleSheet(self.rankTable, light, dark)

    def __connectSignalToSlot(self):
        self.backToMeButton.clicked.connect(self.__changeToCurrentSummoner)
        self.refreshButton.clicked.connect(self.refresh)
        self.searchButton.clicked.connect(
            lambda: signalBus.toSearchInterface.emit(self.getSummonerName()))
        self.filterComboBox.currentIndexChanged.connect(
            self.__onfilterComboBoxChanged)
        self.copyButton.clicked.connect(
            lambda: pyperclip.copy(self.getSummonerName()))

        self.recentTeamButton.clicked.connect(
            self.__onRecentTeammatesButtonClicked)
        self.championsView.opponentsWanted.connect(self.__onChampionExpanded)
        signalBus.lolClientEnded.connect(self.__onLolClientEnded)

    async def updateNameIconExp(self, info):
        if not self.isLoginSummoner():
            return

        name = info.get("gameName") or info['displayName']
        name = name if info['privacy'] == 'PUBLIC' else f"{name}🔒"
        icon = await connector.getProfileIcon(info['profileIconId'])
        level = info['summonerLevel']
        xpSinceLastLevel = info['xpSinceLastLevel']
        xpUntilNextLevel = info['xpUntilNextLevel']

        self.name.setText(name)
        levelStr = str(level) if level != -1 else "None"
        self.icon.updateIcon(icon, xpSinceLastLevel,
                             xpUntilNextLevel, levelStr)

        self.repaint()

    @asyncSlot()
    async def __changeToCurrentSummoner(self):
        self.setLoadingPageEnabled(True)
        summoner = await connector.getCurrentSummoner()
        if await self.updateInterface(summoner=summoner):
            self.setLoadingPageEnabled(False)

    @asyncSlot()
    async def refresh(self):
        if self.puuid:
            index = self.filterComboBox.currentIndex()
            if not await self.updateInterface(puuid=self.puuid):
                return
            self.filterComboBox.setCurrentIndex(index)
            self.__onfilterComboBoxChanged(index)

    async def updateInterface(self, puuid=None, summoner=None):
        '''
        通过 `puuid` 或 `summoner` 更新界面

        Returns False when a newer call superseded this one (it then leaves the page,
        the loading state and the background tasks to the newer call), True otherwise.
        '''

        # 不能同时为空
        assert summoner or puuid

        # Loads can overlap (e.g. A is slow, then B is opened): only the newest paints
        self._loadSeq += 1
        seq = self._loadSeq

        self.setLoadingPageEnabled(True)
        self.recentTeammatesInfo = None
        if self.teammatesTask:  # 否则上一位的结果可能在加载期间写回 recentTeammatesInfo
            self.teammatesTask.cancel()
            self.teammatesTask = None

        if self.recentTeammatesFlyout:
            self.recentTeammatesFlyout.close()
            self.recentTeammatesFlyout = None

        try:
            if summoner is None:
                summoner = await connector.getSummonerByPuuid(puuid)
                if seq != self._loadSeq:
                    return False

            if 'errorCode' in summoner:
                # a saved player this client cannot find (another server, or deleted);
                # other errors (a client still starting) say nothing about the player
                if puuid and summoner.get('httpStatus') == 404 \
                        and saved_players.markUnreachable(puuid):
                    signalBus.savedPlayersChanged.emit()
                InfoBar.error(self.tr("Get summoner infomation error"),
                              self.tr("The server returned abnormal content."),
                              orient=Qt.Vertical,
                              position=InfoBarPosition.BOTTOM_RIGHT,
                              duration=5000,
                              parent=self.window())

                self.setLoadingPageEnabled(False)
                return True

            self.loadGamesTask = asyncio.create_task(
                connector.getSummonerGamesByPuuid(summoner['puuid'], 0, cfg.get(cfg.careerGamesNumber) - 1))
            rankTask = asyncio.create_task(
                connector.getRankedStatsByPuuid(summoner['puuid']))

            info = await parseSummonerData(summoner, rankTask, self.loadGamesTask)
            if seq != self._loadSeq:
                return False
            await self.repaintInterface(info)
            return True
        except BaseException:
            # CancelledError included: nothing else would take the spinner down (the
            # refresh / back buttons are hidden while loading); a superseded call leaves
            # the loading page to the newer one
            if seq == self._loadSeq:
                self.setLoadingPageEnabled(False)
            raise

    async def repaintInterface(self, info):
        name = info['name'] if info['isPublic'] else f"{info['name']}🔒"
        icon = info['icon']
        level = info['level']
        xpSinceLastLevel = info['xpSinceLastLevel']
        xpUntilNextLevel = info['xpUntilNextLevel']
        puuid = info['puuid']
        rankInfo = info['rankInfo']
        games = info['games']

        # self.serviceLabel.setText(SERVERS_NAME.get(
        #     connector.server) or connector.server)
        # subset = SERVERS_SUBSET.get(connector.server)
        # if subset:
        #     self.serviceLabel.setToolTip(" ".join(subset))
        #     self.serviceLabel.installEventFilter(
        #         ToolTipFilter(self.serviceLabel, 500, ToolTipPosition.BOTTOM))

        if len(info['tagLine']):
            self.showTagLine = True
            self.tagLineLabel.setText(f"# {info['tagLine']}")
        else:
            self.showTagLine = False
            self.tagLineLabel.setText("")

        levelStr = str(level) if level != -1 else "None"
        self.icon.updateIcon(icon, xpSinceLastLevel,
                             xpUntilNextLevel, levelStr)
        self.name.setText(name)

        self.puuid = puuid

        if 'queueMap' in rankInfo:
            self.rankInfo = parseDetailRankInfo(rankInfo)
            self.copyButton.setEnabled(True)
        else:
            self.rankInfo = [[
                self.tr('Ranked Solo'),
            ], [
                self.tr('Ranked Flex'),
            ]]
            self.copyButton.setEnabled(False)

        if not self.isLoginSummoner():
            for i in range(0, 2):
                for j in [1, 2, 4]:
                    self.rankInfo[i][j] = '--'

        self.__updateTable()

        queueMap = rankInfo.get('queueMap') or {}
        rank_history.record(puuid, queueMap)

        riotId = f"{info['name']}#{info['tagLine']}" if info['tagLine'] else info['name']
        # [] = unranked; None = the rank request failed (keeps the saved rank)
        solo = queueMap.get('RANKED_SOLO_5x5') or {}
        if 'queueMap' not in rankInfo:
            rank = None
        elif tierText(solo.get('tier'), solo.get('division')):
            rank = (solo['tier'], solo.get('division'))
        else:
            rank = ()
        self.saveButton.setPlayer(puuid, riotId, info['iconId'], rank)
        if saved_players.refresh(puuid, riotId, info['iconId'], rank):
            signalBus.savedPlayersChanged.emit()
        self.tierGraphCard.setPuuid(puuid)
        self.soloCard.updateRank(queueMap.get('RANKED_SOLO_5x5'))
        self.flexCard.updateRank(queueMap.get('RANKED_FLEX_SR'))

        fives = queueMap.get('RANKED_PREMADE_5x5') or {}
        self.fivesCard.updateRank(fives)
        self.fivesCard.setVisible(bool(tierText(fives.get('tier'), fives.get('division'))
                                       or fives.get('wins') or fives.get('losses')))

        if 'gameCount' in games:
            self.recent20GamesLabel.setText(
                self.tr('Recent matches') + " " + self.tr('(Last') +
                f" {len(games['games'])} " + self.tr('games)')
            )
            self.winsLabel.setText(self.tr('Wins:') + f" {games['wins']}")
            self.lossesLabel.setText(self.tr('Losses:') + f" {games['losses']}")

            kda = self.tr('KDA:') + \
                f" {games['kills']} / {games['deaths']} / {games['assists']} "
            kda += self.tr("(")
            kda += f"{(games['kills'] + games['assists']) / (1 if games['deaths'] == 0 else games['deaths']):.1f}"
            kda += self.tr(")")

            self.kdaLabel.setText(kda)

        else:
            self.recent20GamesLabel.setText(
                self.tr('Recent matches') + " " + self.tr('(Last') + " None " +
                self.tr('games)')
            )
            self.winsLabel.setText(self.tr('Wins:') + " 0")
            self.lossesLabel.setText(self.tr('Losses:') + " 0")
            self.kdaLabel.setText(
                self.tr('KDA:') + " 0 / 0 / 0 " + self.tr("(") + "0" + self.tr(")"))

        self.games = games

        self.__updateGameInfo()

        self.backToMeButton.setEnabled(not self.isLoginSummoner())

        if 'champions' in info:
            self.championsCard.updateChampions(info['champions'])

        self.setLoadingPageEnabled(False)

        # 换人 / 刷新: 上一位召唤师的后台任务 (完整对局 / 近期队友 / 对线得分 / 赛季样本) 不再需要
        for task in (self.detailsTask, self.teammatesTask, self.laningTask, self.avgRankTask,
                     self.seasonTask, self.playStyleIconTask, *self.championIconTasks):
            if task:
                task.cancel()
        self.detailsTask = self.teammatesTask = self.laningTask = self.seasonTask = None
        self.avgRankTask = self.playStyleIconTask = None
        # the previous summoner's second stage can finish while this one loads and write its
        # teammates here; this summoner's arrive with its own second stage
        self.recentTeammatesInfo = None
        self.championIconTasks = []
        self.championIconIds = set()

        recent = season_stats.aggregate((self.games or {}).get('games'))
        self.performancePanel.setRecent(recent)
        self.playStyleArea.delegate.vScrollBar.resetValue(0)
        self.championsArea.delegate.vScrollBar.resetValue(0)
        if not recent:
            self.playStyleView.setEmpty()
            self.championsView.setEmpty()
        else:
            self.playStyleView.setLoading(self.games['games'])
            self.championsView.setLoading()

        if self.games:
            self.detailsTask = asyncio.create_task(
                self.__updateGameDetails(puuid, rankInfo, self.games.get('games') or [],
                                         (info.get('name'), info.get('tagLine'))))

    async def __updateGameDetails(self, puuid, rankInfo, games, riotId=None):
        """
        第二阶段: 战绩行显示后逐局拉完整对局, 补上徽章 / 评分 / 参团率与右侧 10 名玩家, 补完一局
        就刷新那一行; 全部补完后再起近期队友 / 对线得分 / 平均段位 / 赛季样本 (它们要用这些结果)
        """
        def onGame(game):
            if puuid == self.puuid:
                row = self.__gameRow(game['gameId'])
                if row:
                    row.refreshDetail()

        # at most connector.maxRefCnt rows waiting: a summoner opened next does not queue
        # behind twenty of this one's requests
        try:
            details = await attachCareerDetails(games, puuid, onGame, limit=connector.maxRefCnt)
        except Exception as e:
            # the stages below still run (they fetch what they miss), so no loading state stays
            logger.warning(f"career game details failed: {type(e).__name__}", TAG)
            details = None
        if puuid != self.puuid:
            return

        self.summaryCard.updateGames(self.summaryCard.games)  # kill participation
        recent = season_stats.aggregate(games)
        self.performancePanel.setRecent(recent)

        self.teammatesTask = asyncio.create_task(self.__updateRecentTeammates(puuid, details))
        self.laningTask = asyncio.create_task(self.__updateLaningScores(puuid, games))
        self.avgRankTask = asyncio.create_task(
            self.__updateAverageRanks(puuid, games, riotId))
        if recent:
            self.seasonTask = asyncio.create_task(
                self.__updateSeasonPerformance(puuid, rankInfo, games))

    def __gameRow(self, gameId):
        for i in range(self.gameInfoLayout.count()):
            row = self.gameInfoLayout.itemAt(i).widget()
            if isinstance(row, CareerGameRow) and row.gameId == gameId:
                return row
        return None

    async def __updateSeasonPerformance(self, puuid, rankInfo, recentGames):
        """赛季样本: 战绩翻页 (至多 season_stats.SEASON_CAP 局); 取不到用排位胜负兜底胜率"""
        try:
            games, source, info = await season_stats.fetchSeasonGames(puuid)
        except Exception:
            games, source, info = None, '', {}
        if puuid != self.puuid:
            return
        # only the newest games: the cap was reached or a later page failed
        capped = bool(info.get('capped') or info.get('partial'))

        season = season_stats.aggregate(games)
        if season is None:
            self.performancePanel.setSeason(season_stats.rankedSeasonFallback(rankInfo), 'ranked')
            self.playStyleView.setEmpty()
            self.championsView.setEmpty()
            return

        self.performancePanel.setSeason(season, source, capped)
        # Play style re-aggregates the same sample (no extra history requests)
        manager = getattr(connector, 'manager', None)
        self.playStyleView.setSample(games, source, capped,
                                     getattr(manager, 'champRoles', None) or {}, recentGames,
                                     getattr(manager, 'champs', None))
        self.championsView.setSample(games, source, capped, getattr(manager, 'champs', None))
        # icons in their own task so they never delay the kill participation fill below
        self.playStyleIconTask = asyncio.create_task(self.__loadPlayStyleIcons(puuid, games))
        self.__loadChampionIconIds(champion_stats.championIds(self.championsView.profiles))

        if source == 'lcu':
            # LCU 战绩列表没有全队击杀: 近期参团率 + 少量补拉完整对局
            try:
                await season_stats.fillLcuKp(games, recentGames, puuid)
            except Exception as e:
                logger.warning(f"kill participation fill failed: {type(e).__name__}", TAG)
                return
            if puuid == self.puuid:
                self.performancePanel.setSeason(season_stats.aggregate(games), source, capped)
                self.playStyleView.setSample(games, source, capped)
                self.championsView.setSample(games, source, capped)

    async def __loadPlayStyleIcons(self, puuid, games):
        try:
            icons = await play_style.championIcons(play_style.topChampionIds(games))
        except Exception as e:
            logger.warning(f"play style icons failed: {type(e).__name__}", TAG)
            return
        if puuid == self.puuid:
            self.playStyleView.setChampionIcons(icons)

    async def __loadChampionIcons(self, puuid, championIds):
        """Champions view icons: its rows up front, a row's lane opponents when expanded"""
        try:
            icons = await play_style.championIcons(championIds)
        except Exception as e:
            logger.warning(f"champion view icons failed: {type(e).__name__}", TAG)
            return
        if puuid == self.puuid:
            self.championsView.setChampionIcons(icons)

    def __loadChampionIconIds(self, championIds):
        """one task per batch; ids already loaded or in flight are not asked for again"""
        self.championIconTasks = [t for t in self.championIconTasks if not t.done()]
        ids = [cid for cid in championIds if cid not in self.championIconIds]
        if ids:
            self.championIconIds.update(ids)
            self.championIconTasks.append(
                asyncio.create_task(self.__loadChampionIcons(self.puuid, ids)))

    def __onChampionExpanded(self, championId):
        self.__loadChampionIconIds(
            champion_stats.opponentIds(self.championsView.profiles, championId))

    def __onLolClientEnded(self):
        """the client closed: the full games, season sample and icon tasks can only fail from here on"""
        if any(task and not task.done() for task in (self.detailsTask, self.seasonTask)):
            # stop the loading states instead of leaving them spinning
            self.performancePanel.setSeason(None)
            self.playStyleView.setEmpty()
            self.championsView.setEmpty()
        for task in (self.detailsTask, self.seasonTask, self.playStyleIconTask,
                     *self.championIconTasks):
            if task:
                task.cancel()
        self.detailsTask = self.seasonTask = self.playStyleIconTask = None
        self.championIconTasks = []
        self.championIconIds = set()

    async def __updateLaningScores(self, puuid, games):
        """战绩行显示后逐局补拉时间线算对线得分, 算完一局就刷新当前显示的那一行"""
        def onGame(game):
            if puuid != self.puuid:
                return
            row = self.__gameRow(game['gameId'])
            if row:
                row.refreshLaning()

        try:
            await attachLaningScores(games, puuid, onGame)
        except Exception as e:
            logger.warning(f"laning scores failed: {type(e).__name__}", TAG)

    async def __updateAverageRanks(self, puuid, games, riotId=None):
        """战绩行显示后逐局补上对局平均段位 (OP.GG / 开局时的记录 / 当前段位估算), 补上一局就刷新那一行"""
        def onGame(game):
            if puuid != self.puuid:
                return
            for i in range(self.gameInfoLayout.count()):
                row = self.gameInfoLayout.itemAt(i).widget()
                if isinstance(row, CareerGameRow) and row.gameId == game['gameId']:
                    row.refreshAvgRank()

        try:
            await attachAverageRanks(games, onGame, riotId)
        except Exception as e:
            logger.warning(f"average ranks failed: {type(e).__name__}", TAG)

    def __updateGameInfo(self):
        while self.gameInfoLayout.count():
            item = self.gameInfoLayout.takeAt(0)
            if item is not None and item.widget():
                item.widget().deleteLater()

        if 'gameCount' in self.games:

            for bar in [CareerGameRow(game, self.puuid) for game in self.games['games']]:
                self.gameInfoLayout.addWidget(bar)
                self.gameInfoLayout.addSpacing(5)

            self.gameInfoLayout.addSpacerItem(
                QSpacerItem(1, 1, QSizePolicy.Minimum, QSizePolicy.Expanding))

        self.summaryCard.updateGames(self.games.get('games', []) if self.games else [])

    def __onfilterComboBoxChanged(self, index):
        self.gameInfoArea.delegate.vScrollBar.resetValue(0)
        self.gameInfoArea.verticalScrollBar().setSliderPosition(0)

        while self.gameInfoLayout.count():
            item = self.gameInfoLayout.takeAt(0)
            if item is not None and item.widget():
                item.widget().deleteLater()

        if index == 1:
            targetId = 430
        elif index == 2:
            targetId = 450
        elif index == 3:
            targetId = 420
        elif index == 4:
            targetId = 440
        else:
            targetId = 0

        hitGames, kills, deaths, assists, wins, losses = parseGames(
            self.games.get("games", []), targetId)

        for game in hitGames:
            bar = CareerGameRow(game, self.puuid)
            self.gameInfoLayout.addWidget(bar)
            self.gameInfoLayout.addSpacing(5)

        self.summaryCard.updateGames(hitGames)

        self.recent20GamesLabel.setText(
            self.tr('Recent matches') + " " + self.tr('(Last') + f" {len(hitGames)} " +
            self.tr('games)')
        )
        self.winsLabel.setText(self.tr('Wins:') + f" {wins}")
        self.lossesLabel.setText(self.tr('Losses:') + f" {losses}")
        kda = self.tr('KDA:') + f" {kills} / {deaths} / {assists}"
        kda += self.tr("(")
        kda += f"{(kills + assists) / (1 if deaths == 0 else deaths):.1f}"
        kda += self.tr(")")
        self.kdaLabel.setText(kda)

        self.gameInfoLayout.addSpacerItem(
            QSpacerItem(1, 1, QSizePolicy.Minimum, QSizePolicy.Expanding))

    def setLoginSummonerPuuid(self, name):
        self.loginSummonerPuuid = name

    def getSummonerName(self):
        if self.showTagLine:
            res = f'{self.name.text()}{self.tagLineLabel.text()}'
        else:
            res = self.name.text()

        return res

    def isLoginSummoner(self):
        return self.loginSummonerPuuid == None or self.loginSummonerPuuid == self.puuid

    def __onRecentTeammatesButtonClicked(self):
        view = TeammatesFlyOut()

        if self.recentTeammatesInfo:
            view.setLoadingPageEnabled(False)
            view.updateSummoners(self.recentTeammatesInfo)

        self.recentTeammatesFlyout = Flyout.make(
            view, self.recentTeamButton, self, FlyoutAnimationType.DROP_DOWN)
        self.recentTeammatesFlyout.closed.connect(
            self.__resetRecentTeammatesFlyout)

    def __resetRecentTeammatesFlyout(self):
        self.recentTeammatesFlyout = None

    async def __updateRecentTeammates(self, puuid, details):
        info = await getRecentTeammates(self.games['games'], puuid, details)
        if puuid != self.puuid:
            return
        self.recentTeammatesInfo = info

        if self.recentTeammatesFlyout:
            self.recentTeammatesFlyout.close()
            self.__onRecentTeammatesButtonClicked()


class TeammatesFlyOut(FlyoutViewBase):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.vBoxLayout = QVBoxLayout(self)
        self.stackedWidget = QStackedWidget()

        self.loadingPageWidget = QWidget()
        self.infoPageWidget = QWidget()

        self.loadingVBoxLayout = QVBoxLayout(self.loadingPageWidget)
        self.infopageVBoxLayout = QVBoxLayout(self.infoPageWidget)

        self.processRing = IndeterminateProgressRing()

        self.__initLayout()

    def __initLayout(self):
        self.vBoxLayout.setContentsMargins(0, 0, 0, 0)
        self.loadingVBoxLayout.addWidget(
            self.processRing, alignment=Qt.AlignCenter)

        self.vBoxLayout.addWidget(self.stackedWidget)

        self.stackedWidget.addWidget(self.loadingPageWidget)
        self.stackedWidget.addWidget(self.infoPageWidget)

        self.stackedWidget.setFixedHeight(352)
        self.stackedWidget.setFixedWidth(490)

    def clear(self):
        while self.infopageVBoxLayout.count():
            item = self.infopageVBoxLayout.takeAt(0)
            if item is not None and item.widget():
                item.widget().deleteLater()

    def updateSummoners(self, info):
        for summoner in info['summoners']:
            infoBar = TeammateInfoBar(summoner)
            self.infopageVBoxLayout.addWidget(infoBar, stretch=1)

        length = len(info['summoners'])
        spacing = self.infopageVBoxLayout.spacing()

        if length < 5:
            self.infopageVBoxLayout.addStretch(5 - length)
            self.infopageVBoxLayout.addSpacing(spacing * (5 - length))

    def setLoadingPageEnabled(self, enable):
        index = 0 if enable else 1
        self.stackedWidget.setCurrentIndex(index)


class TeammateInfoBar(ColorAnimationFrame):
    # closed = Signal()

    def __init__(self, summoner, parent=None):
        super().__init__(type='default', parent=parent)
        self._pressedBackgroundColor = self._hoverBackgroundColor

        self.hBoxLayout = QHBoxLayout(self)

        self.icon = RoundIcon(summoner['icon'], 40, 4, 4)
        self.name = SummonerName(summoner['name'] or '?', tagLine=summoner.get('tagLine'))
        if summoner.get('tagLine'):
            self.name.setToolTip(f"{summoner['name']}#{summoner['tagLine']}")

        self.totalTitle = QLabel(self.tr("Total: "))
        self.totalLabel = QLabel(str(summoner["total"]))
        self.winsTitle = QLabel(self.tr("Wins: "))
        self.winsLabel = ColorLabel(str(summoner['wins']), 'win')
        self.lossesTitle = QLabel(self.tr("Losses: "))
        self.lossesLabel = ColorLabel(str(summoner['losses']), 'lose')

        self.__initWidget()
        self.__initLayout()

        self.setFixedHeight(62)

        self.name.clicked.connect(
            lambda: signalBus.toCareerInterface.emit(summoner['puuid']))

    def __initWidget(self):
        self.name.setFixedWidth(180)
        self.totalLabel.setFixedWidth(40)
        self.winsLabel.setFixedWidth(40)
        self.lossesLabel.setFixedWidth(40)

        self.totalLabel.setObjectName('totalLabel')
        self.winsLabel.setObjectName("winsLabel")
        self.lossesLabel.setObjectName("lossesLabel")

        self.totalTitle.setAlignment(Qt.AlignCenter)
        self.totalLabel.setAlignment(Qt.AlignCenter)
        self.winsTitle.setAlignment(Qt.AlignCenter)
        self.winsLabel.setAlignment(Qt.AlignCenter)
        self.lossesTitle.setAlignment(Qt.AlignCenter)
        self.lossesLabel.setAlignment(Qt.AlignCenter)

    def __initLayout(self):
        self.hBoxLayout.addWidget(self.icon)
        self.hBoxLayout.addWidget(self.name)
        self.hBoxLayout.addWidget(self.totalTitle)
        self.hBoxLayout.addWidget(self.totalLabel)
        self.hBoxLayout.addWidget(self.winsTitle)
        self.hBoxLayout.addWidget(self.winsLabel)
        self.hBoxLayout.addWidget(self.lossesTitle)
        self.hBoxLayout.addWidget(self.lossesLabel)


class ChampionsCard(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.hBoxLayout = QHBoxLayout(self)
        self.hBoxLayout.setContentsMargins(0, 0, 0, 0)

        self.setFixedHeight(33)

    def updateChampions(self, champions):
        self.clear()

        for champion in champions:
            icon = RoundIcon(champion['icon'], 28, 2, 2)

            toolTip = self.tr("Total: ") + str(champion['total']) + "   "
            toolTip += self.tr("Wins: ") + str(champion['wins']) + "   "
            toolTip += self.tr("Losses: ") + str(champion['losses']) + "   "
            toolTip += self.tr("Win Rate: ")
            toolTip += ("100" if champion['losses'] == 0 else "{:.2f}".format(
                champion['wins'] * 100 / (champion['wins'] + champion['losses']))) + "%"
            icon.setToolTip(toolTip)
            icon.installEventFilter(
                ToolTipFilter(icon, 0, ToolTipPosition.TOP))

            self.hBoxLayout.addWidget(icon, alignment=Qt.AlignCenter)

    def clear(self):
        while self.hBoxLayout.count():
            item = self.hBoxLayout.takeAt(0)
            if item is not None and item.widget():
                item.widget().deleteLater()
