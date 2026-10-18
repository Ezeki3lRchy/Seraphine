import os
from typing import Dict

from PySide6.QtCore import Signal, Qt, QRectF
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QFrame, QVBoxLayout,
                               QSizePolicy, QStackedWidget, QWidget)
from PySide6.QtGui import QColor, QPainter, QPainterPath, QFont

from ..common.qfluentwidgets import (SmoothScrollArea, TransparentTogglePushButton,
                                     ToolTipFilter, ToolTipPosition, setCustomStyleSheet)

from app.common.style_sheet import StyleSheet
from app.common.signals import signalBus
from app.components.champion_icon_widget import RoundIcon, RoundedLabel
from app.components.profile_level_icon_widget import RoundLevelAvatar
from app.components.summoner_name_button import SummonerName
from app.components.animation_frame import ColorAnimationFrame
from app.components.color_label import DeathsLabel
from app.components.match_widgets import (ItemIcon, TagPill, RankEmblem, gameTags)
from app.lol.tools import parseSummonerOrder, summarizeGames, translatePositionKey
from app.lol.connector import connector
from app.lol.aram import AramBuff
from app.components.seraphine_interface import SeraphineInterface


NBSP = "\u00a0"  # 不间断空格

# 每名玩家一列 (Reveal / OP.GG 风格), 列宽下限; 五列放不下时整页横向滚动
COLUMN_MIN_WIDTH = 228


class GameInfoInterface(SeraphineInterface):
    """
    对局信息页 (组队 / 选人 / 游戏中): 己方 / 敌方各一页, 每名玩家一列,
    自上而下为 段位卡 -> 近期概况 -> 近期对局 -> 近期英雄
    """

    def __init__(self, parent=None):
        super().__init__(parent)

        self.isAram = False
        self.vBoxLayout = QVBoxLayout(self)
        self.buttonsLayout = QHBoxLayout()

        self.allyButton = TransparentTogglePushButton(self.tr("Ally"))
        self.enemyButton = TransparentTogglePushButton(self.tr("Enemy"))

        self.stackedWidget = QStackedWidget()
        self.allyView = TeamColumnsView()
        self.enemyView = TeamColumnsView()
        self.allyScrollArea = SmoothScrollArea()
        self.enemyScrollArea = SmoothScrollArea()

        # 保存召唤师的英雄信息
        # {summonerId: championId}
        self.allyChampions = {}

        # 保存召唤师楼层的顺序，列表中为 summonerId
        self.allyOrder = []

        self.queueId = 0

        self.__initWidget()
        self.__initLayout()

        StyleSheet.GAME_INFO_INTERFACE.apply(self)

    def __initWidget(self):
        self.allyButton.setChecked(True)
        self.allyButton.clicked.connect(self.__onAllyButtonClicked)
        self.enemyButton.clicked.connect(self.__onEnemyButtonClicked)
        self.__setButtonsVisible(False)

        for scrollArea, view in ((self.allyScrollArea, self.allyView),
                                 (self.enemyScrollArea, self.enemyView)):
            scrollArea.setObjectName("gamesScrollArea")
            view.setObjectName("gamesScrollWidget")
            scrollArea.setWidget(view)
            scrollArea.setWidgetResizable(True)
            self.stackedWidget.addWidget(scrollArea)

        self.stackedWidget.setObjectName("summonersGamesView")
        self.stackedWidget.setCurrentIndex(0)

        light, dark = "rgba(0, 0, 0, 0.073)", "rgba(255, 255, 255, 0.053)"
        qss = '''
            TransparentTogglePushButton,
            TransparentTogglePushButton:hover,
            TransparentTogglePushButton:disabled {{
                border: 1px solid {};
                font: 14px 'Segoe UI', 'Microsoft YaHei';
            }}

            TransparentTogglePushButton:checked,
            TransparentTogglePushButton:checked:hover {{
                border: none;
            }}
        '''
        for button in (self.allyButton, self.enemyButton):
            button.setFixedWidth(120)
            setCustomStyleSheet(button, qss.format(light), qss.format(dark))

    def __initLayout(self):
        self.buttonsLayout.setContentsMargins(0, 0, 0, 0)
        self.buttonsLayout.addWidget(self.allyButton)
        self.buttonsLayout.addWidget(self.enemyButton)
        self.buttonsLayout.addStretch()

        self.vBoxLayout.setContentsMargins(30, 32, 30, 30)
        self.vBoxLayout.setSpacing(12)
        self.vBoxLayout.addLayout(self.buttonsLayout)
        self.vBoxLayout.addWidget(self.stackedWidget)

    def __setButtonsVisible(self, visible):
        for button in (self.allyButton, self.enemyButton):
            button.setVisible(visible)
            button.setEnabled(visible)

    def __onAllyButtonClicked(self):
        if self.allyButton.isChecked():
            self.enemyButton.setChecked(False)
            self.stackedWidget.setCurrentIndex(0)
        else:
            self.allyButton.setChecked(True)

    def __onEnemyButtonClicked(self):
        if self.enemyButton.isChecked():
            self.allyButton.setChecked(False)
            self.stackedWidget.setCurrentIndex(1)
        else:
            self.enemyButton.setChecked(True)

    def updateAllySummonersOrder(self, team: list):
        if len(self.allyOrder) == 0:
            return

        order = parseSummonerOrder(team)

        if order != self.allyOrder and len(order) == len(self.allyOrder):
            self.setAllyOrder(order)

    def setAllyOrder(self, order: list):
        """按 summonerId 列表重排己方各列 (选人楼层 / 游戏内分路)"""
        self.allyView.updateOrder(order)
        self.allyOrder = order

    def updateAllySummoners(self, info):
        if not info or len(info['summoners']) > 5:
            return

        self.isAram = info.get("isAram", False)

        self.allyChampions = info['champions']
        self.allyOrder = info['order']

        self.allyView.updateSummoners(info['summoners'], self.isAram)

        self.allyButton.setVisible(True)
        self.enemyButton.setVisible(True)
        self.allyButton.setEnabled(True)

    def updateEnemySummoners(self, info):
        if not info or len(info['summoners']) > 5:
            return

        self.enemyView.updateSummoners(info['summoners'])

        self.allyButton.setVisible(True)
        self.enemyButton.setVisible(True)
        self.enemyButton.setEnabled(True)

    async def updateAllyIcon(self, team):
        for new in team:
            if not new['championId'] or not new['summonerId']:
                continue

            summonerId = new['summonerId']
            column = self.allyView.items.get(summonerId)
            orig = self.allyChampions.get(summonerId)

            if not column or orig == None:
                continue

            newChampionId = new['championId']

            if orig != newChampionId and newChampionId:  # 若新头像是 0, 那就也不更新
                icon = await connector.getChampionIcon(newChampionId)
                self.allyChampions[summonerId] = newChampionId
                aramInfo = AramBuff.getInfoByChampionId(newChampionId) \
                    if self.isAram else None
                column.updateChampion(newChampionId, icon, aramInfo)

    def clearAlly(self):
        self.allyChampions = {}
        self.allyOrder = []
        self.allyView.clear()

    def clear(self):
        self.clearAlly()

        self.isAram = False
        self.enemyView.clear()

        self.allyButton.click()
        self.__setButtonsVisible(False)

    # 进入游戏时标记预组队颜色
    def updateTeamColor(self, allyColor, enemyColor):
        self.allyView.updateColor(allyColor)
        self.enemyView.updateColor(enemyColor)


class TeamColumnsView(QFrame):
    """一队至多 5 列, 每名玩家一列"""

    def __init__(self, parent=None):
        super().__init__(parent)

        self.hBoxLayout = QHBoxLayout(self)
        self.hBoxLayout.setContentsMargins(0, 0, 0, 0)
        self.hBoxLayout.setSpacing(0)
        self.items: Dict[int, PlayerColumn] = {}

    def __relayout(self, columns):
        while self.hBoxLayout.count():
            self.hBoxLayout.takeAt(0)

        for i, column in enumerate(columns):
            self.hBoxLayout.addWidget(column, stretch=1)
            column.setProperty("isLast", i == len(columns) - 1)
            column.style().polish(column)

        # 不足 5 人时空出位置, 列宽与满员时一致
        if len(columns) < 5:
            self.hBoxLayout.addStretch(5 - len(columns))

    def updateSummoners(self, summoners, isAram=False):
        self.clear()

        columns = []
        for summoner in summoners:
            if not summoner:
                continue

            aramInfo = None
            if isAram and summoner["championId"]:
                aramInfo = AramBuff.getInfoByChampionId(summoner["championId"])

            column = PlayerColumn(summoner, aramInfo, self)
            self.items[summoner["summonerId"]] = column
            columns.append(column)

        self.__relayout(columns)

    def updateOrder(self, order):
        if not all(i in self.items for i in order):
            return

        self.__relayout([self.items[i] for i in order])

    def updateColor(self, colors):
        for summonerId, color in colors.items():
            column = self.items.get(summonerId)
            if column:
                column.updateTeamColor(color)

    def clear(self):
        while self.hBoxLayout.count():
            item = self.hBoxLayout.takeAt(0)
            if item is not None and item.widget():
                item.widget().deleteLater()

        self.items = {}


class PlayerColumn(QFrame):
    """单名玩家的一列"""

    def __init__(self, info: dict, aramInfo=None, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(COLUMN_MIN_WIDTH)

        self.info = info
        self.gamesInfo = [g for g in info.get('gamesInfo') or [] if g]
        self.mastery = info.get('mastery') or {}

        self.vBoxLayout = QVBoxLayout(self)
        self.header = PlayerHeader(info, aramInfo, self)

        self.gamesTitle = QLabel(self.tr("Recent games"))
        self.gamesTitle.setObjectName("sectionTitle")
        self.gameRows = []
        for game in self.gamesInfo:
            row = GameRow(game, self)
            row.clicked.connect(
                lambda gid=row.gameId: signalBus.gameInfoBarClicked.emit(
                    info['puuid'], self.header.summonerName.text(), str(gid)))
            self.gameRows.append(row)

        self.championsTitle = QLabel(self.tr("Recent champions"))
        self.championsTitle.setObjectName("sectionTitle")
        summary = summarizeGames(self.gamesInfo, limit=5)
        self.championRows = [ChampionStatRow(item, self.mastery.get(item['championId']))
                             for item in summary['topChampions']]

        self.__initLayout()

    def __initLayout(self):
        self.vBoxLayout.setContentsMargins(8, 4, 8, 8)
        self.vBoxLayout.setSpacing(6)
        self.vBoxLayout.addWidget(self.header)

        if self.gameRows:
            self.vBoxLayout.addSpacing(4)
            self.vBoxLayout.addWidget(self.gamesTitle)
            for row in self.gameRows:
                self.vBoxLayout.addWidget(row)

        if self.championRows:
            self.vBoxLayout.addSpacing(4)
            self.vBoxLayout.addWidget(self.championsTitle)
            for row in self.championRows:
                self.vBoxLayout.addWidget(row)

        self.vBoxLayout.addStretch()

    def updateChampion(self, championId, icon, aramInfo=None):
        self.header.updateChampion(championId, icon, aramInfo)

    def updateTeamColor(self, team):
        self.header.updateTeamColor(team)


def kdaColor(kda):
    """KDA 数值的颜色分档; 普通档位返回 None (用默认文字色)"""
    if 3 <= kda < 4:
        return QColor(0, 163, 80)
    elif 4 <= kda < 5:
        return QColor(0, 147, 255)
    elif 5 <= kda:
        return QColor(240, 111, 0)

    return None


def winRateColor(winRate):
    if winRate >= 60:
        return QColor(240, 111, 0)
    elif winRate >= 50:
        return QColor(0, 163, 80)
    elif winRate < 45:
        return QColor(191, 36, 42)

    return None


def colorSpan(text, color=None, bold=False):
    """富文本片段; 富文本里的颜色不受主题 QSS 的 QLabel color 影响"""
    style = []
    if color is not None:
        style.append(f"color: {color.name()}")
    if bold:
        style.append("font-weight: bold")

    if not style:
        return text

    return f"<span style='{'; '.join(style)}'>{text}</span>"


def winRateText(wins, losses):
    total = wins + losses
    if not total:
        return "--"

    winRate = round(wins / total * 100)
    return f"{colorSpan(f'{winRate}%', winRateColor(winRate), bold=True)} ({wins}W {losses}L)"


def shortModeName(game):
    """短模式名: 单双 / 灵活区分开, 其余模式名过长时截断 (完整名在悬浮提示里)"""
    queueId = game.get('queueId')
    if queueId == 420:
        return "Solo"
    if queueId == 440:
        return "Flex"

    name = (game.get('name') or "").replace("排位赛 ", "")
    if not name:
        # 新 / 限时模式在队列表里可能没有名字, 退回 gameMode 或队列号
        gameMode = game.get('gameMode')
        name = gameMode.title() if gameMode else f"Queue {queueId}"

    # 卡片上位置有限: "ARAM: Mayhem" 取冒号后, "Normal (Draft Pick)" 取第一个词
    if ':' in name:
        name = name.split(':', 1)[1].strip()
    name = name.split(' (')[0]

    return name if len(name) <= 8 else name[:7] + "…"


def shortNumber(n):
    if n >= 1000000:
        return f"{n / 1000000:.2f}M"
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def tierText(tier, division):
    """LCU 原始段位 -> 'Diamond II' / 'Master'; 取不到为空串"""
    if not tier or tier in ('NONE', 'UNRANKED'):
        return ""

    division = "" if division in ("", "NA", None) else division
    return f"{tier.title()} {division}".strip()


class WinLossBar(QWidget):
    """OP.GG 风格赛季胜负条: 蓝 (胜) / 红 (负), 两端写场次"""

    WIN = QColor(83, 131, 232)
    LOSS = QColor(232, 64, 87)

    def __init__(self, wins, losses, parent=None):
        super().__init__(parent)
        self.wins, self.losses = wins, losses
        self.setFixedHeight(18)
        self.setMinimumWidth(80)

    def paintEvent(self, e):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        rect = QRectF(self.rect())
        total = max(self.wins + self.losses, 1)
        winWidth = rect.width() * self.wins / total

        clip = QPainterPath()
        clip.addRoundedRect(rect, 4, 4)
        painter.setClipPath(clip)

        painter.fillRect(QRectF(0, 0, winWidth, rect.height()), self.WIN)
        painter.fillRect(QRectF(winWidth, 0, rect.width() - winWidth, rect.height()),
                         self.LOSS)

        font = QFont('Segoe UI')
        font.setPixelSize(11)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(Qt.white)
        textRect = rect.adjusted(6, 0, -6, 0)
        painter.drawText(textRect, Qt.AlignLeft | Qt.AlignVCenter, f"{self.wins}W")
        painter.drawText(textRect, Qt.AlignRight | Qt.AlignVCenter, f"{self.losses}L")


class PlayerHeader(ColorAnimationFrame):
    """
    一列顶部的玩家卡片:

        [头像 + 熟练度]  名字 #tag
        [段位大徽章]     Diamond II · 95 LP
                        [胜 ===== | == 负] 50%
                        Peak Master · S-1 Diamond I
        Flex  Master 0 LP · 55% (100W 82L)
        近期: KDA · 胜率 · 连胜 · 重开
        分路 · MVP / SVP 次数
        当前英雄: 熟练度 · 近期战绩
    """

    def __init__(self, info: dict, aramInfo=None, parent=None):
        super().__init__(type='default', parent=parent)
        self._pressedBackgroundColor = self._hoverBackgroundColor
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        self.info = info
        self.gamesInfo = [g for g in info.get('gamesInfo') or [] if g]
        self.mastery = info.get('mastery') or {}

        self.vBoxLayout = QVBoxLayout(self)
        self.topLayout = QHBoxLayout()
        self.nameLayout = QVBoxLayout()

        self.avatar = RoundLevelAvatar(info['icon'],
                                       info['xpSinceLastLevel'],
                                       info['xpUntilNextLevel'],
                                       58, info["level"], aramInfo=aramInfo)
        # 当前英雄熟练度, 盖在头像左下角 (位置在 resizeEvent 里摆)
        self.masteryPill = TagPill("", "#3b4a6b", 10, self)
        self.masteryPill.setVisible(False)

        fateFlag = info["fateFlag"]
        nameColor = None
        if fateFlag:
            nameColor = "#bf242a" if fateFlag == "enemy" else "#057748"
        self.summonerName = SummonerName(
            info['name'], isPublic=info["isPublic"], color=nameColor,
            tagLine=info['tagLine'], tips=info["recentlyChampionName"])
        self.summonerName.setObjectName("playerName")
        self.summonerName.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.summonerName.clicked.connect(
            lambda: signalBus.toCareerInterface.emit(info['puuid']))

        self.tagLabel = QLabel(f"#{info['tagLine']}" if info.get('tagLine') else "")
        self.tagLabel.setObjectName("secondaryLabel")

        solo, flex = info['rankInfo']['solo'], info['rankInfo']['flex']

        # 单双排段位卡
        self.rankLayout = QHBoxLayout()
        self.rankTextLayout = QVBoxLayout()
        self.emblem = RankEmblem(solo.get('tierKey'), 48)
        self.rankLabel = QLabel()
        self.rankLabel.setObjectName("rankLabel")
        self.rankLabel.setWordWrap(True)
        self.winLossLayout = QHBoxLayout()
        self.winLossBar = None
        self.winRateLabel = QLabel()
        self.winRateLabel.setObjectName("bodyLabel")
        self.peakLabel = QLabel()
        self.peakLabel.setObjectName("secondaryLabel")
        self.peakLabel.setWordWrap(True)
        self.__initSoloRank(solo)

        # 灵活组排一行
        self.flexLayout = QHBoxLayout()
        self.flexEmblem = RankEmblem(flex.get('tierKey'), 24)
        self.flexLabel = QLabel()
        self.flexLabel.setObjectName("secondaryLabel")
        self.flexLabel.setWordWrap(True)
        self.__initFlexRank(flex)

        summary = summarizeGames(self.gamesInfo, info.get('championId') or 0)

        self.recentLabel = QLabel(self.__recentText(info, summary))
        self.recentLabel.setObjectName("bodyLabel")
        self.recentLabel.setWordWrap(True)
        self.recentLabel.setToolTip(self.tr("Recent games (remakes excluded)"))
        self.recentLabel.installEventFilter(
            ToolTipFilter(self.recentLabel, 0, ToolTipPosition.TOP))

        self.rolesLabel = QLabel(self.__rolesText(summary))
        self.rolesLabel.setObjectName("bodyLabel")
        self.rolesLabel.setWordWrap(True)

        self.pickLabel = QLabel()
        self.pickLabel.setObjectName("bodyLabel")
        self.pickLabel.setWordWrap(True)
        self.__updatePick(info.get('championId') or 0, summary)

        self.__initLayout()

    def __initSoloRank(self, solo):
        ranked = tierText(solo.get('tierKey'), solo.get('division'))
        if ranked:
            lp = solo.get('lp')
            self.rankLabel.setText(
                f"{ranked}" + (f" · {lp} LP" if lp not in ("", None) else ""))
        else:
            self.rankLabel.setText(self.tr("Unranked"))

        wins, losses = solo.get('wins', 0), solo.get('losses', 0)
        if losses:
            self.winLossBar = WinLossBar(wins, losses)
            winRate = round(wins / (wins + losses) * 100)
            self.winRateLabel.setText(
                colorSpan(f"{winRate}%", winRateColor(winRate), bold=True))
        elif wins:
            # LCU 对其他玩家常把 losses 返回 0 (实测 "211-0"), 不画会误导的胜负条
            self.winRateLabel.setText(f"{wins}W " + self.tr("this season"))

        peak = []
        highest = tierText(solo.get('highestTier'), solo.get('highestDivision'))
        # 不间断空格: 只在 " · " 处换行, 不把 "Last season Emerald III" 拆开
        if highest:
            peak.append(f"{self.tr('Peak')} {highest}".replace(' ', NBSP))
        previous = tierText(solo.get('previousTier'), solo.get('previousDivision'))
        if previous:
            peak.append(f"{self.tr('Last season')} {previous}".replace(' ', NBSP))
        self.peakLabel.setText(" · ".join(peak))
        self.peakLabel.setVisible(bool(peak))

        tip = self.tr("Ranked Solo / Duo")
        for w in (self.emblem, self.rankLabel):
            w.setToolTip(tip)
            w.installEventFilter(ToolTipFilter(w, 0, ToolTipPosition.TOP))

    def __initFlexRank(self, flex):
        ranked = tierText(flex.get('tierKey'), flex.get('division'))
        text = f"{self.tr('Flex')}  "
        if ranked:
            lp = flex.get('lp')
            text += ranked + (f" {lp} LP" if lp not in ("", None) else "")
            wins, losses = flex.get('wins', 0), flex.get('losses', 0)
            if losses:
                text += " · " + winRateText(wins, losses)
            elif wins:
                text += f" · {wins}W"
        else:
            text += self.tr("Unranked")
            self.flexEmblem.setVisible(False)

        self.flexLabel.setText(text)

    def __recentText(self, info, summary):
        wins = info.get('wins', 0)
        losses = info.get('losses', 0)

        k, d, a = info['kda']
        kda = (k + a) / max(d, 1)

        parts = [f"KDA {colorSpan(f'{kda:.2f}', kdaColor(kda), bold=True)}",
                 winRateText(wins, losses)]

        streak = summary['streak']
        if streak and streak[1] >= 3:
            if streak[0] == 'win':
                parts.append(colorSpan(self.tr("%1W streak").replace("%1", str(streak[1])),
                                       QColor(0, 163, 80), bold=True))
            else:
                parts.append(colorSpan(self.tr("%1L streak").replace("%1", str(streak[1])),
                                       QColor(191, 36, 42), bold=True))

        remakes = info.get('remakes', 0)
        if remakes > 0:
            parts.append(colorSpan(self.tr("%1 remake").replace("%1", str(remakes)),
                                   QColor(191, 36, 42)))

        return f"{self.tr('Recent')}: " + " · ".join(parts)

    def __rolesText(self, summary):
        parts = [f"{translatePositionKey(key)} {count}"
                 for key, count in summary['positions'][:2]]

        for badge, color in (('mvp', QColor('#d4a017')), ('svp', QColor('#8e6bd8'))):
            if summary[badge]:
                parts.append(colorSpan(f"{summary[badge]} {badge.upper()}", color, bold=True))

        return " · ".join(parts)

    def __updatePick(self, championId, summary):
        """当前所选英雄: 熟练度 (头像左下角 + 文字) 与近期战绩"""
        if not championId:
            self.pickLabel.setVisible(False)
            self.masteryPill.setVisible(False)
            return

        parts = []
        mastery = self.mastery.get(championId)
        if mastery:
            self.masteryPill.setText(f"M{mastery['level']}")
            self.masteryPill.setToolTip(
                f"{self.tr('Mastery')} {mastery['level']} · {mastery['points']:,} pts")
            self.masteryPill.installEventFilter(
                ToolTipFilter(self.masteryPill, 0, ToolTipPosition.TOP))
            self.masteryPill.setVisible(True)
            parts.append(f"M{mastery['level']} {shortNumber(mastery['points'])}")
        else:
            self.masteryPill.setVisible(False)

        current = summary['currentChampion']
        if current:
            games = current['wins'] + current['losses']
            winRate = round(current['wins'] / games * 100) if games else 0
            parts.append(
                f"{current['games']}G "
                f"{colorSpan(f'{winRate}%', winRateColor(winRate), bold=True)}"
                f" {current['kda']:.1f} KDA")
        else:
            parts.append(self.tr("no recent games"))

        name = connector.manager.champs.get(championId, "") \
            if getattr(connector, 'manager', None) else ""
        self.pickLabel.setText(f"{self.tr('Pick')}{' ' + name if name else ''}: "
                               + " · ".join(parts))
        self.pickLabel.setVisible(True)
        self.__placeMastery()

    def updateChampion(self, championId, icon, aramInfo=None):
        self.avatar.updateIcon(icon)
        if aramInfo:
            self.avatar.updateAramInfo(aramInfo)

        self.__updatePick(championId, summarizeGames(self.gamesInfo, championId))

    def __initLayout(self):
        self.nameLayout.setContentsMargins(0, 0, 0, 0)
        self.nameLayout.setSpacing(0)
        self.nameLayout.addStretch()
        self.nameLayout.addWidget(self.summonerName)
        self.nameLayout.addWidget(self.tagLabel)
        self.nameLayout.addStretch()

        self.topLayout.setContentsMargins(0, 0, 0, 0)
        self.topLayout.setSpacing(8)
        self.topLayout.addWidget(self.avatar)
        self.topLayout.addLayout(self.nameLayout, stretch=1)

        self.winLossLayout.setContentsMargins(0, 0, 0, 0)
        self.winLossLayout.setSpacing(6)
        if self.winLossBar:
            self.winLossLayout.addWidget(self.winLossBar, stretch=1)
        self.winLossLayout.addWidget(self.winRateLabel)
        if not self.winLossBar:
            self.winLossLayout.addStretch()

        self.rankTextLayout.setContentsMargins(0, 0, 0, 0)
        self.rankTextLayout.setSpacing(3)
        self.rankTextLayout.addStretch()
        self.rankTextLayout.addWidget(self.rankLabel)
        self.rankTextLayout.addLayout(self.winLossLayout)
        self.rankTextLayout.addWidget(self.peakLabel)
        self.rankTextLayout.addStretch()

        self.rankLayout.setContentsMargins(0, 0, 0, 0)
        self.rankLayout.setSpacing(6)
        self.rankLayout.addWidget(self.emblem)
        self.rankLayout.addLayout(self.rankTextLayout, stretch=1)

        self.flexLayout.setContentsMargins(0, 0, 0, 0)
        self.flexLayout.setSpacing(4)
        self.flexLayout.addWidget(self.flexEmblem)
        self.flexLayout.addWidget(self.flexLabel, stretch=1)

        self.vBoxLayout.setContentsMargins(10, 8, 10, 8)
        self.vBoxLayout.setSpacing(5)
        self.vBoxLayout.addLayout(self.topLayout)
        self.vBoxLayout.addLayout(self.rankLayout)
        self.vBoxLayout.addLayout(self.flexLayout)
        self.vBoxLayout.addWidget(self.recentLabel)
        if self.rolesLabel.text():
            self.vBoxLayout.addWidget(self.rolesLabel)
        self.vBoxLayout.addWidget(self.pickLabel)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.__placeMastery()

    def showEvent(self, e):
        super().showEvent(e)
        self.__placeMastery()

    def __placeMastery(self):
        if not self.masteryPill.isVisible():
            return

        self.masteryPill.adjustSize()
        g = self.avatar.geometry()
        self.masteryPill.move(g.left() - 2, g.bottom() - self.masteryPill.height() + 2)
        self.masteryPill.raise_()

    def updateTeamColor(self, team):
        if team in [0, 1]:
            self.setType(f"team{team+1}")


class GameRow(ColorAnimationFrame):
    """
    近期一局 (OP.GG 风格):

        [英雄]  K / D / A              KP 55%
                2.83 KDA        305 CS (8.1)
        [物品 x 7]                     Solo
        [Double kill] [MVP]      10/19 · 27m
    """

    def __init__(self, game, parent=None):
        if game['remake']:
            type = 'remake'
        elif game['win']:
            type = 'win'
        else:
            type = 'lose'

        super().__init__(type=type, parent=parent)
        self._pressedBackgroundColor = self._hoverBackgroundColor

        self.gameId = game['gameId']

        self.vBoxLayout = QVBoxLayout(self)
        self.topLayout = QHBoxLayout()
        self.kdaLayout = QVBoxLayout()
        self.kdaNumbersLayout = QHBoxLayout()
        self.rightLayout = QVBoxLayout()
        self.itemsLayout = QHBoxLayout()
        self.tagsLayout = QHBoxLayout()

        self.championIcon = RoundIcon(game['championIcon'], 34, 2, 2)

        self.kills = QLabel(str(game['kills']))
        self.slash1 = QLabel("/")
        self.deaths = DeathsLabel(str(game['deaths']))
        self.slash2 = QLabel("/")
        self.assists = QLabel(str(game['assists']))

        kda = (game['kills'] + game['assists']) / max(game['deaths'], 1)
        self.kdaRatio = QLabel(f"{kda:.2f} KDA")
        self.kdaRatio.setObjectName("kdaRatio")
        color = None if game['remake'] else kdaColor(kda)
        if color:
            self.kdaRatio.setStyleSheet(f"color: {color.name()};")

        kp = game.get('kp')
        self.kpLabel = QLabel(f"KP {round(kp * 100)}%" if kp is not None else "")
        self.kpLabel.setObjectName("bodyLabel")
        self.kpLabel.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        secs = game.get('durationSecs') or 0
        minutes = secs / 60
        self.modeLabel = QLabel(shortModeName(game))
        self.modeLabel.setObjectName("secondaryLabel")
        self.modeLabel.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        itemIds = game.get('itemIds') or [0] * len(game.get('itemIcons', []))
        self.items = [ItemIcon(icon, itemId, 20)
                      for icon, itemId in zip(game.get('itemIcons', []), itemIds)]

        cs = game.get('cs', 0)
        csText = f"{cs} CS"
        if minutes >= 1:
            csText += f" ({cs / minutes:.1f})"
        self.csLabel = QLabel(csText)
        self.csLabel.setObjectName("secondaryLabel")
        self.csLabel.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.tags = [TagPill(text, color) for text, color in gameTags(game)]

        self.dateLabel = QLabel(
            game['shortTime'] + (f" · {int(minutes)}m" if secs else ""))
        self.dateLabel.setObjectName("secondaryLabel")
        self.dateLabel.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.__initWidget(game)
        self.__initLayout()

    def __initWidget(self, game):
        for label, name in ((self.kills, "kills"), (self.slash1, "slash"),
                            (self.deaths, "deaths"), (self.slash2, "slash"),
                            (self.assists, "assists")):
            label.setObjectName(name)

        tips = [game['name'], game.get('time', '')]
        if game.get('position'):
            tips.append(f"{self.tr('Position')}: {game['position']}")
        if game.get('duration'):
            tips.append(f"{self.tr('Duration')}: {game['duration']}")
        if game.get('champLevel'):
            tips.append(f"{self.tr('Level')}: {game['champLevel']}")
        tips.append(f"{self.tr('Damage')}: {shortNumber(game.get('damage', 0))}")
        tips.append(f"{self.tr('Gold')}: {shortNumber(game.get('gold', 0))}")
        tips.append(f"{self.tr('Vision')}: {game.get('visionScore', 0)}")
        if game.get('scoreRank'):
            tips.append(
                f"{self.tr('Seraphine score')}: #{game['scoreRank']} / {game['scorePlayers']}")
            tips.append(self.tr(
                "MVP: best score on the winning team, SVP: best on the losing team"))
        self.setToolTip("\n".join(t for t in tips if t))
        self.installEventFilter(ToolTipFilter(self, 300, ToolTipPosition.TOP))

    def __initLayout(self):
        self.kdaNumbersLayout.setContentsMargins(0, 0, 0, 0)
        self.kdaNumbersLayout.setSpacing(3)
        for label in (self.kills, self.slash1, self.deaths, self.slash2, self.assists):
            self.kdaNumbersLayout.addWidget(label)
        self.kdaNumbersLayout.addStretch()

        self.kdaLayout.setContentsMargins(0, 0, 0, 0)
        self.kdaLayout.setSpacing(0)
        self.kdaLayout.addLayout(self.kdaNumbersLayout)
        self.kdaLayout.addWidget(self.kdaRatio)

        self.rightLayout.setContentsMargins(0, 0, 0, 0)
        self.rightLayout.setSpacing(0)
        self.rightLayout.addWidget(self.kpLabel)
        self.rightLayout.addWidget(self.csLabel)

        self.topLayout.setContentsMargins(0, 0, 0, 0)
        self.topLayout.setSpacing(8)
        self.topLayout.addWidget(self.championIcon)
        self.topLayout.addLayout(self.kdaLayout, stretch=1)
        self.topLayout.addLayout(self.rightLayout)

        self.itemsLayout.setContentsMargins(0, 0, 0, 0)
        self.itemsLayout.setSpacing(1)
        for item in self.items:
            self.itemsLayout.addWidget(item)
        self.itemsLayout.addStretch()
        self.itemsLayout.addWidget(self.modeLabel)

        self.tagsLayout.setContentsMargins(0, 0, 0, 0)
        self.tagsLayout.setSpacing(4)
        for tag in self.tags:
            self.tagsLayout.addWidget(tag)
        self.tagsLayout.addStretch()
        self.tagsLayout.addWidget(self.dateLabel)

        self.vBoxLayout.setContentsMargins(8, 6, 8, 6)
        self.vBoxLayout.setSpacing(4)
        self.vBoxLayout.addLayout(self.topLayout)
        self.vBoxLayout.addLayout(self.itemsLayout)
        self.vBoxLayout.addLayout(self.tagsLayout)


class ChampionStatRow(QFrame):
    """近期英雄一行: 头像 | KDA | 场次 | 胜率 | 熟练度"""

    def __init__(self, item, mastery=None, parent=None):
        super().__init__(parent)
        self.hBoxLayout = QHBoxLayout(self)

        self.icon = RoundIcon(item['icon'], 26, 2, 1)

        self.kdaLabel = QLabel(f"{item['kda']:.2f} KDA")
        self.kdaLabel.setObjectName("bodyLabel")
        color = kdaColor(item['kda'])
        if color:
            self.kdaLabel.setStyleSheet(f"color: {color.name()};")

        self.gamesLabel = QLabel(f"{item['games']}G")
        self.gamesLabel.setObjectName("secondaryLabel")

        games = item['wins'] + item['losses']
        winRate = round(item['wins'] / games * 100) if games else 0
        self.winRateLabel = QLabel(
            colorSpan(f"{winRate}%", winRateColor(winRate), bold=True))
        self.winRateLabel.setObjectName("bodyLabel")

        self.masteryLabel = QLabel(f"M{mastery['level']}" if mastery else "")
        self.masteryLabel.setObjectName("secondaryLabel")

        name = connector.manager.champs.get(item['championId'], "") \
            if getattr(connector, 'manager', None) else ""
        tip = f"{name} · {item['wins']}W {item['losses']}L · {item['kda']:.2f} KDA"
        if mastery:
            tip += f"\n{self.tr('Mastery')} {mastery['level']} · {mastery['points']:,} pts"
        self.setToolTip(tip)
        self.installEventFilter(ToolTipFilter(self, 0, ToolTipPosition.TOP))

        self.hBoxLayout.setContentsMargins(4, 1, 4, 1)
        self.hBoxLayout.setSpacing(8)
        self.hBoxLayout.addWidget(self.icon)
        self.hBoxLayout.addWidget(self.kdaLabel, stretch=1)
        self.hBoxLayout.addWidget(self.gamesLabel)
        self.hBoxLayout.addWidget(self.winRateLabel)
        self.hBoxLayout.addWidget(self.masteryLabel)
