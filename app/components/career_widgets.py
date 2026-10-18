"""
生涯页 OP.GG 风格控件: 段位卡, 近期对局概况 (胜负环形图 / KDA / 参团率 / 常用英雄 / 分路),
以及单局战绩行 (标签 / 参团率 / 物品详情 / 本局 10 名玩家).
"""
import time

from PySide6.QtCore import Qt, QRectF, QRect, QEvent, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QFont, QFontMetrics, QPixmap
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QVBoxLayout, QLabel, QWidget,
                               QSizePolicy, QToolTip)

from app.common.qfluentwidgets import isDarkTheme
from app.common.signals import signalBus
from app.common.style_sheet import colorManager
from app.components.animation_frame import ColorAnimationFrame
from app.components.champion_icon_widget import RoundIcon
from app.components.match_widgets import RankEmblem, gameTags, itemTooltipHtml
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
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
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
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
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
    生涯页一局 (OP.GG 战绩行), 整行自绘:

        Ranked Solo/Duo  [英雄+等级][技能][符文]  14 / 4 / 8    P/Kill 63%   玩家1  玩家6
        3 minutes ago                             5.50:1 KDA   CS 232 (9.0)  ...    ...
        Victory          [物品 x 7]                            Gold 12.1k
        25m 53s          [Double kill] [MVP]

    20 行 x 约 70 个子控件时滚动每帧要重绘两千个控件 (实测约 23ms / 帧);
    现在每行一个控件, 内容画进缓存图. 物品详情 / 数据说明 / 点玩家名打开生涯靠命中测试.
    """

    HEIGHT = 104
    MARGIN = 14
    INFO_W = 128
    ICON = 58
    SMALL = 24
    KDA_W = 118
    ITEM = 26
    STATS_W = 118
    CHIP_W = 104

    def __init__(self, game: dict, currentPuuid=None, parent=None):
        if game['remake']:
            type = 'remake'
        elif game['win']:
            type = 'win'
        else:
            type = 'lose'

        super().__init__(type=type, parent=parent)
        self.setProperty('pressed', False)
        self.setFixedHeight(self.HEIGHT)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.setMouseTracking(True)

        self.game = game
        self.gameId = game['gameId']
        self.resultType = type
        self.currentPuuid = currentPuuid

        self.championPixmap = QPixmap(game["championIcon"])
        self.spells = [QPixmap(game["spell1Icon"]), QPixmap(game["spell2Icon"])]
        self.rune = QPixmap(game["runeIcon"]) if game.get("runeIcon") else QPixmap()
        itemIds = game.get('itemIds') or [0] * len(game['itemIcons'])
        self.items = [(QPixmap(icon), itemId)
                      for icon, itemId in zip(game['itemIcons'], itemIds)]
        self.tags = gameTags(game)

        teams = {}
        for player in game.get('participants') or []:
            teams.setdefault(player['teamId'], []).append(player)
        self.teams = [team[:5] for team in list(teams.values())[:2]]
        self.playerIcons = {id(p): QPixmap(p['championIcon'])
                            for team in self.teams for p in team}

        kp = game.get('kp')
        self.kpText = f"P/Kill {round(kp * 100)}%" if kp is not None else ""
        secs = game.get('durationSecs') or 0
        cs = game.get('cs', 0)
        self.csText = f"CS {cs}" + (f" ({cs / (secs / 60):.1f})" if secs >= 60 else "")
        self.goldText = f"{self.tr('Gold')} {game.get('gold', 0):,}"
        self.resultText = {'remake': self.tr("Remake"), 'win': self.tr("Victory"),
                           'lose': self.tr("Defeat")}[type]
        self.agoText = timeAgo(game['timeStamp'])
        self.durationText = durationText(secs)

        tips = [f"{self.tr('Damage')}: {game.get('damage', 0):,}",
                f"{self.tr('Vision')}: {game.get('visionScore', 0)}"]
        if game.get('scoreRank'):
            tips.append(f"{self.tr('Seraphine score')}: "
                        f"#{game['scoreRank']} / {game['scorePlayers']}")
            tips.append(self.tr(
                "MVP: best score on the winning team, SVP: best on the losing team"))
        self.statsTip = "\n".join(tips)

        # 命中区域, 绘制时按当前宽度算出
        self.itemRects = []
        self.playerRects = []
        self.statsRect = QRect()
        self.infoRect = QRect()

        self._cache = None
        self._cacheKey = None

        self.clicked.connect(
            lambda: signalBus.careerGameBarClicked.emit(str(self.gameId)))

    # ---------------- 命中测试 ----------------

    def __playerAt(self, pos):
        for rect, player in self.playerRects:
            if rect.contains(pos):
                return player
        return None

    def event(self, e):
        if e.type() == QEvent.ToolTip:
            pos = e.pos()
            for rect, itemId in self.itemRects:
                if rect.contains(pos):
                    html = itemTooltipHtml(itemId)
                    if html:
                        QToolTip.showText(e.globalPos(), html, self, rect)
                        return True
            for rect, player in self.playerRects:
                if rect.contains(pos):
                    QToolTip.showText(e.globalPos(), player['name'] or "?", self, rect)
                    return True
            if self.statsRect.contains(pos):
                QToolTip.showText(e.globalPos(), self.statsTip, self, self.statsRect)
                return True
            if self.infoRect.contains(pos):
                QToolTip.showText(e.globalPos(), self.game.get('time', ''), self, self.infoRect)
                return True
            QToolTip.hideText()
            e.ignore()
            return True

        return super().event(e)

    def mouseMoveEvent(self, e):
        player = self.__playerAt(e.position().toPoint())
        clickable = player and player.get('puuid') and player.get('puuid') != self.currentPuuid
        self.setCursor(Qt.PointingHandCursor if clickable else Qt.ArrowCursor)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        # 点玩家名打开其生涯, 不触发整行的点击 (打开对局详情)
        player = self.__playerAt(e.position().toPoint())
        if player and player.get('puuid'):
            self.setProperty('pressed', False)
            signalBus.toCareerInterface.emit(player['puuid'])
            e.accept()
            return

        super().mouseReleaseEvent(e)

    # ---------------- 绘制 ----------------

    def paintEvent(self, e):
        super().paintEvent(e)  # 背景 (胜 / 负 / 重开, 悬浮动画)

        painter = QPainter(self)
        painter.drawPixmap(0, 0, self.__content())
        painter.end()

    @staticmethod
    def __font(size, bold=False):
        font = QFont(['Segoe UI', 'Microsoft YaHei'])
        font.setPixelSize(size)
        font.setBold(bold)
        return font

    @staticmethod
    def __roundedPixmap(p, pixmap, rect, radius):
        if pixmap.isNull():
            return
        p.save()
        clip = QPainterPath()
        clip.addRoundedRect(rect, radius, radius)
        p.setClipPath(clip)
        p.drawPixmap(rect.toRect(), pixmap)
        p.restore()

    def __content(self):
        dark = isDarkTheme()
        deathsColor = colorManager.getColor('deaths')[0]
        dpr = self.devicePixelRatioF()
        key = (self.width(), self.height(), dpr, dark, deathsColor.rgba())
        if key == self._cacheKey:
            return self._cache

        fg = QColor(255, 255, 255) if dark else QColor(0, 0, 0)
        sub = QColor(255, 255, 255, 153) if dark else QColor(0, 0, 0, 153)
        accent = LOSS_COLOR if self.resultType == 'lose' else WIN_COLOR

        out = QPixmap(round(self.width() * dpr), round(self.height() * dpr))
        out.setDevicePixelRatio(dpr)
        out.fill(Qt.transparent)

        p = QPainter(out)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform
                         | QPainter.TextAntialiasing)

        def text(rect, string, font, color, align=Qt.AlignLeft | Qt.AlignVCenter):
            p.setFont(font)
            p.setPen(color)
            p.drawText(rect, align, string)

        m, w, h = self.MARGIN, self.width(), self.height()

        # 左: 模式 / 多久前 / 结果 / 时长
        x = m
        text(QRectF(x, 10, self.INFO_W, 20), self.game['name'], self.__font(14, True), accent)
        text(QRectF(x, 30, self.INFO_W, 18), self.agoText, self.__font(13), sub)
        text(QRectF(x, 54, self.INFO_W, 20), self.resultText, self.__font(14, True), fg)
        text(QRectF(x, 74, self.INFO_W, 18), self.durationText, self.__font(13), sub)
        self.infoRect = QRect(x, 10, self.INFO_W, 82)
        x += self.INFO_W + 10

        # 英雄头像 (圆形 + 金边) 与等级角标 (同 RoundIconWithLevel)
        iconRect = QRectF(x, (h - self.ICON) / 2, self.ICON, self.ICON)
        if not self.championPixmap.isNull():
            src = self.championPixmap.copy(6, 6, self.championPixmap.width() - 12,
                                           self.championPixmap.height() - 12)
            p.save()
            clip = QPainterPath()
            clip.addEllipse(iconRect)
            p.setClipPath(clip)
            p.drawPixmap(iconRect.toRect(), src)
            p.restore()
            p.setPen(QPen(QColor(120, 90, 40), 4))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(iconRect.adjusted(2, 2, -2, -2))

        levelRect = QRectF(iconRect.left() + 42, iconRect.top() + 36, 22, 22)
        p.setPen(QPen(QColor(120, 90, 40), 1))
        p.setBrush(QColor(1, 10, 19) if dark else QColor(249, 249, 249))
        p.drawEllipse(levelRect)
        text(levelRect, str(self.game['champLevel']), self.__font(11, True),
             QColor(153, 148, 135) if dark else QColor(1, 10, 19), Qt.AlignCenter)
        x += self.ICON + 6 + 10

        # 召唤师技能 (竖排) + 基石符文
        top = (h - 2 * self.SMALL - 2) / 2
        for i, spell in enumerate(self.spells):
            self.__roundedPixmap(p, spell, QRectF(x, top + i * (self.SMALL + 2),
                                                  self.SMALL, self.SMALL), 3)
        x += self.SMALL + 4
        if not self.rune.isNull():
            p.drawPixmap(QRectF(x, top, self.SMALL, self.SMALL).toRect(), self.rune)
        x += self.SMALL + 10

        # K / D / A
        kdaTop = h / 2 - 20
        bold18, regular18 = self.__font(18, True), self.__font(18)
        cx = x
        for string, font, color in ((str(self.game['kills']), bold18, fg),
                                    (" / ", regular18, sub),
                                    (str(self.game['deaths']), bold18, deathsColor),
                                    (" / ", regular18, sub),
                                    (str(self.game['assists']), bold18, fg)):
            text(QRectF(cx, kdaTop, 200, 24), string, font, color)
            cx += QFontMetrics(font).horizontalAdvance(string)
        kda = (self.game['kills'] + self.game['assists']) / max(self.game['deaths'], 1)
        text(QRectF(x, kdaTop + 24, self.KDA_W, 18), f"{kda:.2f}:1 KDA", self.__font(13), sub)
        x += self.KDA_W + 10
        itemsX = x

        # 右: 本局 10 名玩家 (两队各一列)
        right = w - m
        self.playerRects = []
        if self.teams:
            playersX = right - 2 * self.CHIP_W - 8
            rowH = 17
            playersTop = (h - 5 * rowH) / 2
            nameFont = self.__font(12)
            selfFont = self.__font(12, True)
            for column, team in enumerate(self.teams):
                for row, player in enumerate(team):
                    px = playersX + column * (self.CHIP_W + 8)
                    py = playersTop + row * rowH
                    self.__roundedPixmap(p, self.playerIcons[id(player)],
                                         QRectF(px, py + 0.5, 16, 16), 2)
                    isSelf = player.get('puuid') == self.currentPuuid
                    name = player['name'] or "?"
                    name = name if len(name) <= 11 else name[:10] + "…"
                    text(QRectF(px + 20, py, self.CHIP_W - 20, rowH), name,
                         selfFont if isSelf else nameFont, fg if isSelf else sub)
                    self.playerRects.append(
                        (QRect(int(px), int(py), self.CHIP_W, rowH), player))
            right = playersX - 10

        # 参团率 / 补刀 / 金币
        statsX = right - self.STATS_W
        statsTop = h / 2 - 28
        text(QRectF(statsX, statsTop, self.STATS_W, 20), self.kpText,
             self.__font(14), QColor(232, 64, 87))
        text(QRectF(statsX, statsTop + 20, self.STATS_W, 18), self.csText, self.__font(13), sub)
        text(QRectF(statsX, statsTop + 38, self.STATS_W, 18), self.goldText, self.__font(13), sub)
        self.statsRect = QRect(int(statsX), int(statsTop), self.STATS_W, 56)

        # 物品 + 标签
        self.itemRects = []
        itemsTop = h / 2 - self.ITEM - 2
        for i, (pixmap, itemId) in enumerate(self.items):
            rect = QRectF(itemsX + i * (self.ITEM + 2), itemsTop, self.ITEM, self.ITEM)
            self.__roundedPixmap(p, pixmap, rect, 3)
            self.itemRects.append((rect.toRect(), itemId))

        tagX, tagY = itemsX, itemsTop + self.ITEM + 6
        pillFont = self.__font(12, True)
        metrics = QFontMetrics(pillFont)
        p.setFont(pillFont)
        for string, color in self.tags:
            rect = QRectF(tagX, tagY, metrics.horizontalAdvance(string) + 16, 20)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(color))
            p.drawRoundedRect(rect, 9, 9)
            p.setPen(QColor(255, 255, 255))
            p.drawText(rect, Qt.AlignCenter, string)
            tagX += rect.width() + 4

        p.end()

        self._cache, self._cacheKey = out, key
        return out
