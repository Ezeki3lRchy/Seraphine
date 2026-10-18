"""
生涯页 OP.GG 风格控件: 段位卡, 近期对局概况 (胜负环形图 / KDA / 参团率 / 常用英雄 / 分路),
以及单局战绩行 (标签 / 参团率 / 物品详情 / 本局 10 名玩家).
"""
import time

from PySide6.QtCore import Qt, QRectF, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QFont, QPixmap
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QVBoxLayout, QLabel, QWidget,
                               QGridLayout, QSizePolicy)

from app.common.qfluentwidgets import ToolTipFilter, ToolTipPosition, isDarkTheme
from app.common.signals import signalBus
from app.components.animation_frame import ColorAnimationFrame
from app.components.champion_icon_widget import RoundIcon, RoundedLabel
from app.components.color_label import DeathsLabel
from app.components.game_infobar_widget import RoundIconWithLevel
from app.components.match_widgets import (ItemIcon, TagPill, RankEmblem, gameTags)
from app.lol.tools import summarizeGames

WIN_COLOR = QColor(83, 131, 232)
LOSS_COLOR = QColor(232, 64, 87)


def tierText(tier, division):
    """LCU 原始段位 -> 'Diamond II' / 'Master'; 取不到为空串"""
    if not tier or tier in ('NONE', 'UNRANKED'):
        return ""

    division = "" if division in ("", "NA", None) else division
    return f"{tier.title()} {division}".strip()


def timeAgo(timeStamp):
    """毫秒时间戳 -> '3 minutes ago' / '2 hours ago' / '5 days ago'"""
    secs = max(time.time() - timeStamp / 1000, 0)

    for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if secs >= size:
            n = int(secs // size)
            return f"{n} {unit}{'s' if n > 1 else ''} ago"

    return "just now"


def durationText(secs):
    return f"{secs // 60}m {secs % 60:02d}s" if secs else ""


def labelWith(text, objectName, parent=None):
    label = QLabel(text, parent)
    label.setObjectName(objectName)
    return label


class RankCard(QFrame):
    """
    OP.GG 风格段位卡:

        Ranked Solo/Duo
        [徽章]  Diamond 1                451W 453L
                48 LP                 Win rate 50%
        Peak Master · Last season Diamond I
    """

    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.vBoxLayout = QVBoxLayout(self)
        self.mainLayout = QHBoxLayout()
        self.tierLayout = QVBoxLayout()
        self.recordLayout = QVBoxLayout()

        self.titleLabel = labelWith(title, "cardTitle")
        self.emblem = RankEmblem('', 72)
        self.tierLabel = labelWith("", "tierLabel")
        self.lpLabel = labelWith("", "secondaryLabel")
        self.recordLabel = labelWith("", "secondaryLabel")
        self.winRateLabel = labelWith("", "secondaryLabel")
        self.peakLabel = labelWith("", "secondaryLabel")
        self.peakLabel.setWordWrap(True)

        for label in (self.recordLabel, self.winRateLabel):
            label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.__initLayout()

    def __initLayout(self):
        self.tierLayout.setSpacing(2)
        self.tierLayout.addStretch()
        self.tierLayout.addWidget(self.tierLabel)
        self.tierLayout.addWidget(self.lpLabel)
        self.tierLayout.addStretch()

        self.recordLayout.setSpacing(2)
        self.recordLayout.addStretch()
        self.recordLayout.addWidget(self.recordLabel)
        self.recordLayout.addWidget(self.winRateLabel)
        self.recordLayout.addStretch()

        self.mainLayout.setSpacing(12)
        self.mainLayout.addWidget(self.emblem)
        self.mainLayout.addLayout(self.tierLayout, stretch=1)
        self.mainLayout.addLayout(self.recordLayout)

        self.vBoxLayout.setContentsMargins(16, 12, 16, 12)
        self.vBoxLayout.setSpacing(6)
        self.vBoxLayout.addWidget(self.titleLabel)
        self.vBoxLayout.addLayout(self.mainLayout)
        self.vBoxLayout.addWidget(self.peakLabel)

    def updateRank(self, entry: dict):
        """entry: LCU ranked-stats 的 queueMap 某一项 (可为空)"""
        entry = entry or {}
        tier = tierText(entry.get('tier'), entry.get('division'))

        self.emblem.setTier(entry.get('tier') if tier else '')
        self.tierLabel.setText(tier or self.tr("Unranked"))
        self.lpLabel.setText(f"{entry.get('leaguePoints', 0)} LP" if tier else "")

        wins, losses = entry.get('wins', 0), entry.get('losses', 0)
        if wins + losses and losses:
            self.recordLabel.setText(f"{wins}W {losses}L")
            self.winRateLabel.setText(
                f"{self.tr('Win rate')} {round(wins / (wins + losses) * 100)}%")
        elif wins:
            # LCU 对其他玩家常把 losses 返回 0, 不算胜率
            self.recordLabel.setText(f"{wins}W")
            self.winRateLabel.setText("")
        else:
            self.recordLabel.setText("")
            self.winRateLabel.setText("")

        peak = []
        highest = tierText(entry.get('highestTier'), entry.get('highestDivision'))
        if highest:
            peak.append(f"{self.tr('Peak')} {highest}")
        previous = tierText(entry.get('previousSeasonEndTier'),
                            entry.get('previousSeasonEndDivision'))
        if previous:
            peak.append(f"{self.tr('Last season')} {previous}")
        self.peakLabel.setText(" · ".join(peak))
        self.peakLabel.setVisible(bool(peak))


class DonutChart(QWidget):
    """胜 (蓝) / 负 (红) 环形图, 中间写胜率"""

    def __init__(self, size=96, parent=None):
        super().__init__(parent)
        self.wins = self.losses = 0
        self.setFixedSize(size, size)

    def setRecord(self, wins, losses):
        self.wins, self.losses = wins, losses
        self.update()

    def paintEvent(self, e):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        width = 12
        rect = QRectF(width / 2, width / 2, self.width() - width, self.height() - width)
        total = self.wins + self.losses

        pen = QPen(LOSS_COLOR if total else QColor(128, 128, 128, 80), width)
        pen.setCapStyle(Qt.FlatCap)
        painter.setPen(pen)
        painter.drawArc(rect, 0, 360 * 16)

        if total:
            pen.setColor(WIN_COLOR)
            painter.setPen(pen)
            # 从 12 点方向逆时针画胜场
            painter.drawArc(rect, 90 * 16, int(360 * 16 * self.wins / total))

        font = QFont('Segoe UI')
        font.setPixelSize(16)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor('white') if isDarkTheme() else QColor(WIN_COLOR))
        painter.drawText(self.rect(), Qt.AlignCenter,
                         f"{round(self.wins / total * 100)}%" if total else "--")


class RoleBars(QWidget):
    """常用分路 (排位): 5 根竖条 + 分路图标"""

    ROLES = [('TOP', 'top'), ('JUNGLE', 'jng'), ('MIDDLE', 'mid'),
             ('BOTTOM', 'bot'), ('UTILITY', 'sup')]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.counts = {}
        self.setFixedSize(5 * 30, 96)

    def setCounts(self, counts: dict):
        self.counts = counts
        names = {'TOP': 'Top', 'JUNGLE': 'Jungle', 'MIDDLE': 'Mid',
                 'BOTTOM': 'Bot', 'UTILITY': 'Support'}
        self.setToolTip("\n".join(f"{names[k]}: {counts.get(k, 0)}"
                                  for k, _ in self.ROLES))
        self.update()

    def paintEvent(self, e):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        total = max(sum(self.counts.values()), 1)
        barHeight, iconSize = 70, 18
        track = QColor(128, 128, 128, 60)

        for i, (key, icon) in enumerate(self.ROLES):
            x = i * 30 + 9
            painter.fillRect(QRectF(x, 0, 12, barHeight), track)

            h = barHeight * self.counts.get(key, 0) / total
            painter.fillRect(QRectF(x, barHeight - h, 12, h), WIN_COLOR)

            pixmap = QPixmap(f"app/resource/images/icon-position-{icon}.svg")
            painter.drawPixmap(i * 30 + 6, barHeight + 6, iconSize, iconSize, pixmap)


class RecentSummaryCard(QFrame):
    """
    近期对局概况 (OP.GG "Recent games" 卡片):

        [胜负环]  20G 8W 11L        近期常用英雄           常用分路 (排位)
                  7.1 / 9.3 / 5.3   [头像] 56% (5W 4L) ...   | | | | |
                  1.33 : 1
                  P/Kill 49%
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.hBoxLayout = QHBoxLayout(self)

        self.donut = DonutChart()
        self.statsLayout = QVBoxLayout()
        self.recordLabel = labelWith("", "secondaryLabel")
        self.averageLabel = labelWith("", "bodyLabel")
        self.kdaLabel = labelWith("", "bigNumberLabel")
        self.kpLabel = labelWith("", "kpLabel")

        self.championsLayout = QVBoxLayout()
        self.championsTitle = labelWith("", "secondaryLabel")
        self.championRows = []

        self.rolesLayout = QVBoxLayout()
        self.rolesTitle = labelWith(self.tr("Preferred role (ranked)"), "secondaryLabel")
        self.roleBars = RoleBars()

        self.__initLayout()

    def __initLayout(self):
        self.statsLayout.setSpacing(1)
        self.statsLayout.addStretch()
        for w in (self.recordLabel, self.averageLabel, self.kdaLabel, self.kpLabel):
            self.statsLayout.addWidget(w)
        self.statsLayout.addStretch()

        self.championsLayout.setSpacing(4)
        self.championsLayout.addWidget(self.championsTitle)
        self.championsLayout.addStretch()

        self.rolesLayout.setSpacing(6)
        self.rolesLayout.addWidget(self.rolesTitle, alignment=Qt.AlignHCenter)
        self.rolesLayout.addWidget(self.roleBars, alignment=Qt.AlignHCenter)
        self.rolesLayout.addStretch()

        self.hBoxLayout.setContentsMargins(16, 12, 16, 12)
        self.hBoxLayout.setSpacing(18)
        self.hBoxLayout.addWidget(self.donut)
        self.hBoxLayout.addLayout(self.statsLayout)
        self.hBoxLayout.addSpacing(10)
        self.hBoxLayout.addLayout(self.championsLayout, stretch=1)
        self.hBoxLayout.addLayout(self.rolesLayout)

    def updateGames(self, games):
        games = [g for g in games or [] if g]
        counted = [g for g in games if not g.get('remake')]
        wins = sum(1 for g in counted if g['win'])
        losses = len(counted) - wins

        self.donut.setRecord(wins, losses)
        self.recordLabel.setText(f"{len(games)}G {wins}W {losses}L")

        n = max(len(counted), 1)
        k = sum(g['kills'] for g in counted)
        d = sum(g['deaths'] for g in counted)
        a = sum(g['assists'] for g in counted)
        self.averageLabel.setText(
            f"{k / n:.1f} / <span style='color:#e84057'>{d / n:.1f}</span> / {a / n:.1f}")
        self.kdaLabel.setText(f"{(k + a) / max(d, 1):.2f} : 1")

        kps = [g['kp'] for g in counted if g.get('kp') is not None]
        self.kpLabel.setText(
            f"P/Kill {round(sum(kps) / len(kps) * 100)}%" if kps else "")

        summary = summarizeGames(games, limit=3)

        for row in self.championRows:
            self.championsLayout.removeWidget(row)
            row.deleteLater()
        self.championRows = []

        self.championsTitle.setText(
            self.tr("Recent %1 games played champions").replace("%1", str(len(games))))
        for i, item in enumerate(summary['topChampions']):
            row = ChampionSummaryRow(item)
            self.championRows.append(row)
            self.championsLayout.insertWidget(1 + i, row)

        self.roleBars.setCounts(dict(summary['positions']))


class ChampionSummaryRow(QWidget):
    def __init__(self, item, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        games = item['wins'] + item['losses']
        winRate = round(item['wins'] / games * 100) if games else 0
        color = "#e84057" if winRate >= 60 else ""

        icon = RoundIcon(item['icon'], 26, 2, 1)
        record = labelWith(
            f"<span style='color:{color}'>{winRate}%</span> ({item['wins']}W {item['losses']}L)"
            if color else f"{winRate}% ({item['wins']}W {item['losses']}L)", "bodyLabel")
        kda = labelWith(f"{item['kda']:.2f}:1 KDA", "secondaryLabel")

        layout.addWidget(icon)
        layout.addWidget(record)
        layout.addWidget(kda)
        layout.addStretch()


class CareerGameRow(ColorAnimationFrame):
    """
    生涯页一局 (OP.GG 战绩行):

        Ranked Solo/Duo  [英雄+等级][技能][符文]  14 / 4 / 8    P/Kill 63%   玩家1  玩家6
        3 minutes ago                             5.50:1 KDA   CS 232 (9.0)  ...    ...
        Victory          [物品 x 7]                            Gold 12.1k
        25m 53s          [Double kill] [MVP]
    """

    def __init__(self, game: dict, currentPuuid=None, parent=None):
        if game['remake']:
            type = 'remake'
        elif game['win']:
            type = 'win'
        else:
            type = 'lose'

        super().__init__(type=type, parent=parent)
        self.gameId = game['gameId']
        self.setProperty('pressed', False)

        self.hBoxLayout = QHBoxLayout(self)

        # 左: 模式 / 多久前 / 结果 / 时长
        self.infoLayout = QVBoxLayout()
        self.modeLabel = labelWith(game['name'], "modeLabel")
        self.modeLabel.setStyleSheet(
            f"color: {(LOSS_COLOR if type == 'lose' else WIN_COLOR).name()};")
        self.agoLabel = labelWith(timeAgo(game['timeStamp']), "secondaryLabel")
        self.resultLabel = labelWith(
            {'remake': self.tr("Remake"), 'win': self.tr("Victory"),
             'lose': self.tr("Defeat")}[type], "resultLabel")
        self.durationLabel = labelWith(durationText(game.get('durationSecs') or 0),
                                       "secondaryLabel")
        for w in (self.modeLabel, self.agoLabel, self.resultLabel, self.durationLabel):
            w.setToolTip(game.get('time', ''))

        # 英雄 / 技能 / 符文
        self.championIcon = RoundIconWithLevel(game["championIcon"], game["champLevel"])
        self.spellsLayout = QVBoxLayout()
        self.spell1 = RoundedLabel(game["spell1Icon"], 3, 0)
        self.spell2 = RoundedLabel(game["spell2Icon"], 3, 0)
        self.rune = RoundedLabel(game["runeIcon"], 0, 0)
        for w in (self.spell1, self.spell2, self.rune):
            w.setFixedSize(24, 24)

        # KDA
        self.kdaLayout = QVBoxLayout()
        self.kdaNumbersLayout = QHBoxLayout()
        self.kills = labelWith(str(game['kills']), "kdaNumber")
        self.deaths = DeathsLabel(str(game['deaths']))
        self.deaths.setObjectName("kdaNumber")
        self.assists = labelWith(str(game['assists']), "kdaNumber")
        kda = (game['kills'] + game['assists']) / max(game['deaths'], 1)
        self.kdaRatio = labelWith(f"{kda:.2f}:1 KDA", "secondaryLabel")

        # 物品 + 标签
        self.itemsLayout = QVBoxLayout()
        self.itemIconsLayout = QHBoxLayout()
        itemIds = game.get('itemIds') or [0] * len(game['itemIcons'])
        self.items = [ItemIcon(icon, itemId, 26)
                      for icon, itemId in zip(game['itemIcons'], itemIds)]
        self.tagsLayout = QHBoxLayout()
        self.tags = [TagPill(text, color, 12) for text, color in gameTags(game)]

        # 参团率 / 补刀 / 金币
        self.statsLayout = QVBoxLayout()
        kp = game.get('kp')
        self.kpLabel = labelWith(f"P/Kill {round(kp * 100)}%" if kp is not None else "",
                                 "kpLabel")
        secs = game.get('durationSecs') or 0
        cs = game.get('cs', 0)
        csText = f"CS {cs}" + (f" ({cs / (secs / 60):.1f})" if secs >= 60 else "")
        self.csLabel = labelWith(csText, "secondaryLabel")
        self.goldLabel = labelWith(
            f"{self.tr('Gold')} {game.get('gold', 0):,}", "secondaryLabel")
        tips = [f"{self.tr('Damage')}: {game.get('damage', 0):,}",
                f"{self.tr('Vision')}: {game.get('visionScore', 0)}"]
        if game.get('scoreRank'):
            tips.append(f"{self.tr('Seraphine score')}: "
                        f"#{game['scoreRank']} / {game['scorePlayers']}")
            tips.append(self.tr(
                "MVP: best score on the winning team, SVP: best on the losing team"))
        for w in (self.kpLabel, self.csLabel, self.goldLabel):
            w.setToolTip("\n".join(tips))
            w.installEventFilter(ToolTipFilter(w, 300, ToolTipPosition.TOP))

        # 本局 10 名玩家
        self.playersLayout = QGridLayout()
        self.players = self.__playerWidgets(game.get('participants') or [], currentPuuid)

        self.__initLayout()

        self.clicked.connect(
            lambda: signalBus.careerGameBarClicked.emit(str(self.gameId)))

    def __playerWidgets(self, participants, currentPuuid):
        teams = {}
        for p in participants:
            teams.setdefault(p['teamId'], []).append(p)

        widgets = []
        for column, team in enumerate(list(teams.values())[:2]):
            for row, p in enumerate(team[:5]):
                widgets.append((PlayerChip(p, p.get('puuid') == currentPuuid), row, column))

        return widgets

    def __initLayout(self):
        self.infoLayout.setSpacing(1)
        self.infoLayout.addWidget(self.modeLabel)
        self.infoLayout.addWidget(self.agoLabel)
        self.infoLayout.addSpacing(6)
        self.infoLayout.addWidget(self.resultLabel)
        self.infoLayout.addWidget(self.durationLabel)
        self.infoLayout.addStretch()
        infoWidget = QWidget()
        infoWidget.setLayout(self.infoLayout)
        infoWidget.setFixedWidth(128)
        self.infoLayout.setContentsMargins(0, 0, 0, 0)

        self.spellsLayout.setContentsMargins(0, 0, 0, 0)
        self.spellsLayout.setSpacing(2)
        self.spellsLayout.addWidget(self.spell1)
        self.spellsLayout.addWidget(self.spell2)
        self.runeLayout = QVBoxLayout()
        self.runeLayout.setContentsMargins(0, 0, 0, 0)
        self.runeLayout.addWidget(self.rune)
        self.runeLayout.addStretch()

        self.kdaNumbersLayout.setSpacing(4)
        self.kdaNumbersLayout.addWidget(self.kills)
        self.kdaNumbersLayout.addWidget(labelWith("/", "kdaSlash"))
        self.kdaNumbersLayout.addWidget(self.deaths)
        self.kdaNumbersLayout.addWidget(labelWith("/", "kdaSlash"))
        self.kdaNumbersLayout.addWidget(self.assists)
        self.kdaNumbersLayout.addStretch()
        self.kdaLayout.setSpacing(2)
        self.kdaLayout.addStretch()
        self.kdaLayout.addLayout(self.kdaNumbersLayout)
        self.kdaLayout.addWidget(self.kdaRatio)
        self.kdaLayout.addStretch()
        kdaWidget = QWidget()
        kdaWidget.setLayout(self.kdaLayout)
        kdaWidget.setFixedWidth(118)
        self.kdaLayout.setContentsMargins(0, 0, 0, 0)

        self.itemIconsLayout.setSpacing(2)
        for item in self.items:
            self.itemIconsLayout.addWidget(item)
        self.itemIconsLayout.addStretch()
        self.tagsLayout.setSpacing(4)
        for tag in self.tags:
            self.tagsLayout.addWidget(tag)
        self.tagsLayout.addStretch()
        self.itemsLayout.setSpacing(6)
        self.itemsLayout.addStretch()
        self.itemsLayout.addLayout(self.itemIconsLayout)
        self.itemsLayout.addLayout(self.tagsLayout)
        self.itemsLayout.addStretch()

        self.statsLayout.setSpacing(1)
        self.statsLayout.addStretch()
        for w in (self.kpLabel, self.csLabel, self.goldLabel):
            self.statsLayout.addWidget(w)
        self.statsLayout.addStretch()
        statsWidget = QWidget()
        statsWidget.setLayout(self.statsLayout)
        statsWidget.setFixedWidth(118)
        self.statsLayout.setContentsMargins(0, 0, 0, 0)

        self.playersLayout.setContentsMargins(0, 0, 0, 0)
        self.playersLayout.setHorizontalSpacing(8)
        self.playersLayout.setVerticalSpacing(0)
        for widget, row, column in self.players:
            self.playersLayout.addWidget(widget, row, column)

        self.hBoxLayout.setContentsMargins(14, 8, 14, 8)
        self.hBoxLayout.setSpacing(10)
        self.hBoxLayout.addWidget(infoWidget)
        self.hBoxLayout.addWidget(self.championIcon)
        self.hBoxLayout.addLayout(self.spellsLayout)
        self.hBoxLayout.addLayout(self.runeLayout)
        self.hBoxLayout.addWidget(kdaWidget)
        self.hBoxLayout.addLayout(self.itemsLayout, stretch=1)
        self.hBoxLayout.addWidget(statsWidget)
        if self.players:
            self.hBoxLayout.addLayout(self.playersLayout)


class PlayerChip(QWidget):
    """战绩行右侧的玩家: 英雄头像 + 名字, 点击打开其生涯"""

    def __init__(self, player, isSelf=False, parent=None):
        super().__init__(parent)
        self.puuid = player.get('puuid')
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        icon = RoundedLabel(player['championIcon'], 2, 0)
        icon.setFixedSize(16, 16)

        name = player['name'] or "?"
        label = labelWith(name if len(name) <= 11 else name[:10] + "…",
                          "selfPlayerLabel" if isSelf else "playerLabel")
        label.setToolTip(name)

        layout.addWidget(icon)
        layout.addWidget(label)
        layout.addStretch()
        self.setFixedWidth(104)

        if self.puuid and not isSelf:
            self.setCursor(Qt.PointingHandCursor)

    def mouseReleaseEvent(self, e):
        if self.puuid:
            signalBus.toCareerInterface.emit(self.puuid)
            e.accept()
            return
        super().mouseReleaseEvent(e)
