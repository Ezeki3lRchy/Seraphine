import asyncio
from typing import Dict

from PySide6.QtCore import Qt, QRectF, QRect, QPointF, QEvent
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QFrame, QVBoxLayout,
                               QSizePolicy, QStackedWidget, QWidget, QToolTip)
from PySide6.QtGui import QColor, QPainter, QPainterPath, QFont, QFontMetrics, QPixmap, QPen

from ..common.qfluentwidgets import (SmoothScrollArea, TransparentTogglePushButton,
                                     setCustomStyleSheet, isDarkTheme)

from app.common.style_sheet import StyleSheet
from app.common.signals import signalBus
from app.components.profile_level_icon_widget import RoundLevelAvatar
from app.components.animation_frame import ColorAnimationFrame
from app.components.match_widgets import TagPill, gameTags, itemTooltipHtml, rankEmblemPath
from app.components.design_tokens import (deathsColor, palette, numberFont, textFont,
                                         resultColor)
from app.lol.tools import (parseSummonerOrder, summarizeGames, translatePositionKey,
                           needsScores, attachSummonerScores)
from app.common.logger import logger
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

        # 正在后台补评分的玩家 (puuid), 避免逐个显示时重复请求
        self._scoring = set()

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
        self.__fillScoresLater(self.allyView, info['summoners'])

        self.allyButton.setVisible(True)
        self.enemyButton.setVisible(True)
        self.allyButton.setEnabled(True)

    def updateEnemySummoners(self, info):
        if not info or len(info['summoners']) > 5:
            return

        self.enemyView.updateSummoners(info['summoners'])
        self.__fillScoresLater(self.enemyView, info['summoners'])

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

    def __fillScoresLater(self, view, summoners):
        """
        面板先按基础战绩显示; 近期对局的 MVP / SVP / 名次 / 参团率 每局要一个完整对局请求,
        放到后台补齐, 补完重画当前这一队 (期间面板被刷新也没关系: 对局字典是共享的)
        """
        pending = [s for s in summoners
                   if s and needsScores(s) and s.get('puuid') not in self._scoring]
        if not pending:
            return

        puuids = {s.get('puuid') for s in pending}
        self._scoring |= puuids

        async def fill():
            try:
                await attachSummonerScores(pending)
            except Exception as e:
                logger.error(f"fill scores failed: {e!r}", "GameInfoInterface")
                return
            finally:
                self._scoring -= puuids
            view.refreshScores()

        asyncio.ensure_future(fill())

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
        # 加载中会按 "到一个人显示一个" 多次调用: 同一个玩家数据对象的列直接复用, 不重建
        previous = self.items
        self.items = {}

        columns = []
        for summoner in summoners:
            if not summoner:
                continue

            column = previous.pop(summoner["summonerId"], None)
            if column is None or column.info is not summoner:
                if column is not None:
                    column.deleteLater()

                aramInfo = None
                if isAram and summoner["championId"]:
                    aramInfo = AramBuff.getInfoByChampionId(summoner["championId"])

                column = PlayerColumn(summoner, aramInfo, self)

            self.items[summoner["summonerId"]] = column
            columns.append(column)

        for column in previous.values():
            column.deleteLater()

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

    def refreshScores(self):
        """近期对局的评分补齐后重画 (只重画, 不重建列, 选人中的英雄 / 顺序变化都保留)"""
        for column in self.items.values():
            column.refreshScores()

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
                    info['puuid'], self.header.nameWithTag(), str(gid)))
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

    def refreshScores(self):
        self.header.refreshScores()
        for row in self.gameRows:
            row.refreshScores()


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


def fitRuns(runs, maxWidth):
    """
    runs: [(text, font, color, group)]; group 为假是必显示的段, 为真 (True 或组号) 的段
    放不下时整组一起丢 (先丢组号最大 / 最靠后的组, 标签和数值不会只剩一半),
    仍放不下则最后一段省略号截断. 返回 (要画的 runs, 是否有内容被省略)
    """
    def width(rs):
        return sum(QFontMetrics(f).horizontalAdvance(t) for t, f, _, _ in rs)

    runs = list(runs)
    dropped = False
    while width(runs) > maxWidth and any(r[3] for r in runs):
        groups = [(int(r[3]), i) for i, r in enumerate(runs) if r[3]]
        group = max(groups)[0]
        last = max(i for g, i in groups if g == group)
        runs = [r for i, r in enumerate(runs)
                if not (r[3] and int(r[3]) == group and (group > 1 or i == last))]
        dropped = True

    if runs and width(runs) > maxWidth:
        text, font, color, optional = runs[-1]
        room = maxWidth - width(runs[:-1])
        runs[-1] = (QFontMetrics(font).elidedText(text, Qt.ElideRight, max(int(room), 0)),
                    font, color, optional)
        dropped = True

    return runs, dropped


def paintRuns(p, x, y, h, runs):
    for text, font, color, _ in runs:
        p.setFont(font)
        p.setPen(color)
        p.drawText(QRectF(x, y, 2000, h), Qt.AlignLeft | Qt.AlignVCenter, text)
        x += QFontMetrics(font).horizontalAdvance(text)
    return x


class ComposedCardMixin:
    """
    给自绘的 ColorAnimationFrame 子类用: 背景 (含悬浮动画色) + 描边 + 内容合成一张缓存图,
    每帧只贴这一张. 背景色只在悬浮动画时变化, 那几帧才重新合成.

    同时不再用样式表画边框: ColorAnimationFrame.setColor 原本每次都 setStyleSheet,
    让 Qt 的样式表引擎在每次重绘时为每一行画一遍边框, 滚动时代价很高.
    子类需实现 _contentPixmap() 返回内容图 (透明底), 并在重画内容时递增 self._contentVersion.
    """

    RADIUS = 6

    def setColor(self, c1, c2, c3, c4):
        self.normalBackgroundColor = c1
        self.hoverBackgroundColor = c2
        self.pressedBackgroundColor = c3
        self._borderColor = c4
        self._composedKey = None
        try:
            self._updateBackgroundColor()
        except Exception:
            return

    def _composed(self):
        content = self._contentPixmap()
        bg = self.backgroundColor
        border = getattr(self, '_borderColor', None) or QColor(0, 0, 0, 0)
        # 内容版本号由子类在重画内容时递增 (QPixmap.cacheKey 在这里不可靠, 实测每次都变)
        key = (bg.rgba(), border.rgba(), getattr(self, '_contentVersion', 0))
        if key == getattr(self, '_composedKey', None):
            return self._composedPixmap

        dpr = self.devicePixelRatioF()
        out = QPixmap(content.size())
        out.setDevicePixelRatio(dpr)
        out.fill(Qt.transparent)

        p = QPainter(out)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(0, 0, self.width(), self.height()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(border, 1))
        p.setBrush(bg)
        p.drawRoundedRect(rect, self.RADIUS, self.RADIUS)
        p.drawPixmap(0, 0, content)
        p.end()

        self._composedPixmap, self._composedKey = out, key
        return out

    def paintEvent(self, e):
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self._composed())
        painter.end()


class PlayerHeader(ComposedCardMixin, ColorAnimationFrame):
    """
    一列顶部的玩家卡片, 整卡自绘, 固定高度 (每行一个固定槽位, 没数据就空着),
    这样同一队 5 列的卡片一样高, 下面的近期对局逐行对齐.

        [头像]   名字
        [熟练度] #tag
        [徽章]  Diamond II  55 LP
                455W ▬▬▬▬▬▭▭ 463L   50%
                Peak Master   Last season Diamond I
        Flex  Diamond I 75 LP  55%
        Recent  KDA 2.27  40%  4W 6L
        3L streak  1 remake  2 MVP  2 SVP   BOT 6 SUP 3
        Pick Ahri  M45 210k  2G 50% 3.4 KDA
    """

    HEIGHT = 236
    PAD = 10
    AVATAR = 56

    def __init__(self, info: dict, aramInfo=None, parent=None):
        super().__init__(type='default', parent=parent)
        self._pressedBackgroundColor = self._hoverBackgroundColor
        self.setFixedHeight(self.HEIGHT)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.setMouseTracking(True)

        self.info = info
        self.gamesInfo = [g for g in info.get('gamesInfo') or [] if g]
        self.mastery = info.get('mastery') or {}
        self.championId = info.get('championId') or 0
        self.summary = summarizeGames(self.gamesInfo, self.championId)

        self.avatar = RoundLevelAvatar(info['icon'],
                                       info['xpSinceLastLevel'],
                                       info['xpUntilNextLevel'],
                                       self.AVATAR, info["level"], aramInfo=aramInfo)
        self.avatar.setParent(self)
        self.avatar.move(self.PAD, self.PAD)

        # 当前英雄熟练度, 盖在头像左下角
        self.masteryPill = TagPill("", "#3b4a6b", 10, self)
        self.masteryPill.setVisible(False)

        solo, flex = info['rankInfo']['solo'], info['rankInfo']['flex']
        self.solo, self.flex = solo, flex
        self.soloEmblem = QPixmap(rankEmblemPath(solo.get('tierKey'))) \
            if tierText(solo.get('tierKey'), solo.get('division')) else QPixmap()
        self.flexEmblem = QPixmap(rankEmblemPath(flex.get('tierKey'))) \
            if tierText(flex.get('tierKey'), flex.get('division')) else QPixmap()

        self.nameRect = QRect()
        self.tips = []          # [(QRect, text)]
        self._cache = None
        self._cacheKey = None

        self.__updateMasteryPill()

    # ---------------- 对外 ----------------

    def nameWithTag(self):
        tag = self.info.get('tagLine')
        return f"{self.info['name']}#{tag}" if tag else self.info['name']

    def updateChampion(self, championId, icon, aramInfo=None):
        self.avatar.updateIcon(icon)
        if aramInfo:
            self.avatar.updateAramInfo(aramInfo)

        self.championId = championId
        self.summary = summarizeGames(self.gamesInfo, championId)
        self._cacheKey = None
        self.__updateMasteryPill()
        self.update()

    def updateTeamColor(self, team):
        if team in [0, 1]:
            self.setType(f"team{team+1}")

    def refreshScores(self):
        """MVP / SVP 次数等来自后补的评分"""
        self.summary = summarizeGames(self.gamesInfo, self.championId)
        self._cacheKey = None
        self.update()

    # ---------------- 交互 ----------------

    def event(self, e):
        if e.type() == QEvent.ToolTip:
            for rect, text in self.tips:
                if rect.contains(e.pos()) and text:
                    QToolTip.showText(e.globalPos(), text, self, rect)
                    return True
            QToolTip.hideText()
            e.ignore()
            return True
        return super().event(e)

    def mouseMoveEvent(self, e):
        self.setCursor(Qt.PointingHandCursor if self.nameRect.contains(e.position().toPoint())
                       else Qt.ArrowCursor)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self.nameRect.contains(e.position().toPoint()):
            signalBus.toCareerInterface.emit(self.info['puuid'])
            e.accept()
            return
        super().mouseReleaseEvent(e)

    # ---------------- 绘制 ----------------

    def __updateMasteryPill(self):
        mastery = self.mastery.get(self.championId) if self.championId else None
        if not mastery:
            self.masteryPill.setVisible(False)
            return

        self.masteryPill.setText(f"M{mastery['level']}")
        self.masteryPill.setToolTip(
            f"{self.tr('Mastery')} {mastery['level']}, {mastery['points']:,} pts")
        self.masteryPill.adjustSize()
        self.masteryPill.move(self.PAD - 2,
                              self.PAD + self.AVATAR - self.masteryPill.height() + 2)
        self.masteryPill.setVisible(True)
        self.masteryPill.raise_()

    def _contentPixmap(self):
        return self.__content()

    def __content(self):
        dark = isDarkTheme()
        pal = palette(dark)
        dpr = self.devicePixelRatioF()
        winC, loseC = resultColor('win'), resultColor('lose')
        key = (self.width(), dpr, dark, winC.rgba(), loseC.rgba(), self.championId)
        if key == self._cacheKey:
            return self._cache

        ink, muted = pal['ink'], pal['muted']
        out = QPixmap(round(self.width() * dpr), round(self.height() * dpr))
        out.setDevicePixelRatio(dpr)
        out.fill(Qt.transparent)

        p = QPainter(out)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform
                         | QPainter.TextAntialiasing)

        w, pad = self.width(), self.PAD
        tips = []
        info, solo, flex, summary = self.info, self.solo, self.flex, self.summary

        def line(x, y, h, runs, tip=None):
            fitted, dropped = fitRuns(runs, w - pad - x)
            end = paintRuns(p, x, y, h, fitted)
            full = "".join(r[0] for r in runs)
            if tip or dropped:
                tips.append((QRect(int(x), int(y), int(w - pad - x), int(h)),
                             tip or full.strip()))
            return end

        # 名字 (预组队上一局的队友 / 对手着色) + tag
        fate = info.get("fateFlag")
        nameColor = QColor("#bf242a") if fate == "enemy" else \
            QColor("#057748") if fate == "ally" else ink
        nx = pad + self.AVATAR + 10
        name = info['name'] + ("" if info.get("isPublic", True) else " 🔒")
        nameFont = textFont(15, True)
        name = QFontMetrics(nameFont).elidedText(name, Qt.ElideRight, int(w - pad - nx))
        p.setFont(nameFont)
        p.setPen(nameColor)
        p.drawText(QRectF(nx, 14, w - pad - nx, 22), Qt.AlignLeft | Qt.AlignVCenter, name)
        self.nameRect = QRect(int(nx), 14, QFontMetrics(nameFont).horizontalAdvance(name) + 2, 22)
        nameTip = self.nameWithTag()
        if info.get("recentlyChampionName"):
            nameTip += "\n" + str(info["recentlyChampionName"])
        tips.append((self.nameRect, nameTip))
        if info.get('tagLine'):
            line(nx, 36, 18, [(f"#{info['tagLine']}", textFont(12), muted, False)])

        # 单双排: 徽章 + 段位 + 胜点
        y = 74
        if not self.soloEmblem.isNull():
            p.drawPixmap(QRectF(pad - 4, y - 4, 44, 44).toRect(), self.soloEmblem)
        tx = pad + 44
        tier = tierText(solo.get('tierKey'), solo.get('division'))
        if tier:
            lp = solo.get('lp')
            line(tx, y, 22, [(tier, numberFont(16), ink, False),
                             (f"  {lp} LP" if lp not in ("", None) else "", numberFont(13, 400),
                              muted, True)], self.tr("Ranked Solo / Duo"))
        else:
            line(tx, y, 22, [(self.tr("Unranked"), textFont(13, True), muted, False)],
                 self.tr("Ranked Solo / Duo"))

        # 赛季胜负条 (其他玩家 LCU 常把负场给 0, 那时只写胜场)
        y = 98
        wins, losses = solo.get('wins', 0), solo.get('losses', 0)
        if losses:
            rate = wins / (wins + losses)
            rateText = f"{round(rate * 100)}%"
            rateFont = numberFont(14)
            rateW = QFontMetrics(rateFont).horizontalAdvance(rateText)
            bar = QRectF(tx, y + 2, max(w - pad - tx - rateW - 8, 40), 16)
            path = QPainterPath()
            path.addRoundedRect(bar, 4, 4)
            p.save()
            p.setClipPath(path)
            p.fillRect(bar, loseC)
            p.fillRect(QRectF(bar.left(), bar.top(), bar.width() * rate, bar.height()), winC)
            p.restore()
            small = numberFont(11)
            p.setFont(small)
            p.setPen(QColor(255, 255, 255))
            p.drawText(bar.adjusted(5, 0, -5, 0), Qt.AlignLeft | Qt.AlignVCenter, f"{wins}W")
            p.drawText(bar.adjusted(5, 0, -5, 0), Qt.AlignRight | Qt.AlignVCenter, f"{losses}L")
            p.setFont(rateFont)
            p.setPen(winRateColor(round(rate * 100)) or ink)
            p.drawText(QRectF(w - pad - rateW, y, rateW, 20), Qt.AlignRight | Qt.AlignVCenter,
                       rateText)
        elif wins:
            line(tx, y, 20, [(f"{wins}W", numberFont(13), ink, False),
                             ("  " + self.tr("this season"), textFont(12), muted, True)])

        # 赛季最高 / 上赛季
        y = 119
        runs = []
        for label, value in ((self.tr("Peak"), tierText(solo.get('highestTier'),
                                                          solo.get('highestDivision'))),
                             (self.tr("Last season"), tierText(solo.get('previousTier'),
                                                               solo.get('previousDivision')))):
            if value:
                # 第二对 (上赛季) 放不下时标签 + 数值一起省略
                group = 2 if runs else 0
                runs += [(("   " if runs else "") + label + " ", textFont(12), muted, group),
                         (value, textFont(12, True), ink, group)]
        if runs:
            line(tx, y, 18, runs)

        # 灵活组排
        y = 144
        fx = pad
        if not self.flexEmblem.isNull():
            p.drawPixmap(QRectF(pad, y + 1, 18, 18).toRect(), self.flexEmblem)
            fx += 22
        flexTier = tierText(flex.get('tierKey'), flex.get('division'))
        runs = [(self.tr("Flex") + "  ", textFont(12), muted, False)]
        if flexTier:
            lp = flex.get('lp')
            runs.append((flexTier + (f" {lp} LP" if lp not in ("", None) else ""),
                         textFont(12, True), ink, False))
            fw, fl = flex.get('wins', 0), flex.get('losses', 0)
            if fl:
                fr = round(fw / (fw + fl) * 100)
                runs.append((f"  {fr}%", numberFont(13), winRateColor(fr) or ink, True))
            elif fw:
                runs.append((f"  {fw}W", numberFont(13, 400), muted, True))
        else:
            runs.append((self.tr("Unranked"), textFont(12), muted, False))
        line(fx, y, 20, runs, self.tr("Ranked Flex"))

        # 分隔线
        p.setPen(QPen(pal['hairline'], 1))
        p.drawLine(QPointF(pad, 169.5), QPointF(w - pad, 169.5))

        # 近期: KDA + 胜率
        y = 175
        k, d, a = info['kda']
        kda = (k + a) / max(d, 1)
        rw, rl = info.get('wins', 0), info.get('losses', 0)
        runs = [(self.tr("Recent") + "  ", textFont(12), muted, False),
                (f"{kda:.2f}", numberFont(14), kdaColor(kda) or ink, False),
                (" KDA", textFont(12), muted, False)]
        if rw + rl:
            rr = round(rw / (rw + rl) * 100)
            runs += [(f"   {rr}%", numberFont(14), winRateColor(rr) or ink, False),
                     (f"  {rw}W {rl}L", numberFont(13, 400), muted, True)]
        line(pad, y, 18, runs, self.tr("Recent games (remakes excluded)"))

        # 标记: 连胜 / 连败, 重开, MVP / SVP 次数, 常用分路
        y = 195
        runs = []
        streak = summary['streak']
        if streak and streak[1] >= 3:
            label = (self.tr("%1W streak") if streak[0] == 'win'
                     else self.tr("%1L streak")).replace("%1", str(streak[1]))
            runs.append((label + "   ", textFont(12, True),
                         winC if streak[0] == 'win' else loseC, False))
        if info.get('remakes', 0):
            runs.append((self.tr("%1 remake").replace("%1", str(info['remakes'])) + "   ",
                         textFont(12), loseC, True))
        if summary['mvp']:
            runs.append((f"{summary['mvp']} MVP   ", textFont(12, True), pal['gold'], False))
        if summary['svp']:
            runs.append((f"{summary['svp']} SVP   ", textFont(12, True), QColor('#8e6bd8'),
                         False))
        for position, count in summary['positions'][:2]:
            runs.append((f"{translatePositionKey(position)} {count}  ", textFont(12), muted, True))
        if runs:
            line(pad, y, 18, runs)

        # 当前所选英雄: 熟练度 + 近期战绩
        y = 215
        if self.championId:
            champ = connector.manager.champs.get(self.championId, "") \
                if getattr(connector, 'manager', None) else ""
            runs = [(self.tr("Pick") + (f" {champ}" if champ else "") + "  ", textFont(12),
                     muted, False)]
            mastery = self.mastery.get(self.championId)
            if mastery:
                runs.append((f"M{mastery['level']} {shortNumber(mastery['points'])}   ",
                             numberFont(13), pal['gold'], True))
            current = summary['currentChampion']
            if current:
                games = current['wins'] + current['losses']
                cr = round(current['wins'] / games * 100) if games else 0
                runs += [(f"{current['games']}G ", numberFont(13, 400), muted, False),
                         (f"{cr}%", numberFont(13), winRateColor(cr) or ink, False),
                         (f"  {current['kda']:.1f} KDA", numberFont(13, 400), muted, True)]
            else:
                runs.append((self.tr("no recent games"), textFont(12), muted, False))
            line(pad, y, 18, runs)

        p.end()

        self.tips = tips
        self._cache, self._cacheKey = out, key
        self._contentVersion = getattr(self, '_contentVersion', 0) + 1
        return out


class GameRow(ComposedCardMixin, ColorAnimationFrame):
    """
    近期一局 (OP.GG 风格), 整行自绘:

        [英雄]  K / D / A              KP 55%
                2.83 KDA        305 CS (8.1)
        [物品 x 7]                     Solo
        [Double kill] [MVP]      10/19 · 27m

    对局信息页一次有 5 x 11 行; 用子控件拼时整页约 1300 个控件, 滚动每帧都要逐个重绘
    (实测约 50ms / 帧, 明显卡顿). 现在每行只有一个控件, 内容画进缓存图, 滚动时只贴图;
    物品详情靠鼠标位置命中物品格子后弹出.
    """

    HEIGHT = 92
    MARGIN = 8
    ICON = 34
    ITEM = 20

    def __init__(self, game, parent=None):
        if game['remake']:
            type = 'remake'
        elif game['win']:
            type = 'win'
        else:
            type = 'lose'

        super().__init__(type=type, parent=parent)
        self._pressedBackgroundColor = self._hoverBackgroundColor
        self.setFixedHeight(self.HEIGHT)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.setMinimumWidth(196)

        self.game = game
        self.gameId = game['gameId']
        self.remake = game['remake']

        self.championPixmap = QPixmap(game['championIcon']) \
            if game.get('championIcon') else QPixmap()
        itemIds = game.get('itemIds') or [0] * len(game.get('itemIcons', []))
        self.items = [(QPixmap(icon) if icon else QPixmap(), itemId)
                      for icon, itemId in zip(game.get('itemIcons', []), itemIds)]
        self.kda = (game['kills'] + game['assists']) / max(game['deaths'], 1)

        secs = game.get('durationSecs') or 0
        minutes = secs / 60
        cs = game.get('cs', 0)
        self.csText = f"{cs} CS" + (f" ({cs / minutes:.1f})" if minutes >= 1 else "")
        self.modeText = shortModeName(game)
        self.dateText = game['shortTime'] + (f" · {int(minutes)}m" if secs else "")

        self.itemRects = []

        self._cache = None
        self._cacheKey = None
        self.__scoreFields()

    def __scoreFields(self):
        """标签 / 参团率 / 评分说明: 依赖后补的评分 (见 GameInfoInterface.__fillScoresLater)"""
        game = self.game
        self.tags = gameTags(game)
        kp = game.get('kp')
        self.kpText = f"KP {round(kp * 100)}%" if kp is not None else ""
        self.rowTip = self.__rowTip(game)

    def refreshScores(self):
        self.__scoreFields()
        self._cacheKey = None
        self.update()

    def __rowTip(self, game):
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
        return "\n".join(t for t in tips if t)

    def event(self, e):
        # 悬浮提示: 指在物品格子上显示物品详情, 其余位置显示本局概要
        if e.type() == QEvent.ToolTip:
            pos = e.pos()
            for rect, itemId in self.itemRects:
                if rect.contains(pos):
                    html = itemTooltipHtml(itemId)
                    if html:
                        QToolTip.showText(e.globalPos(), html, self, rect)
                        return True
            QToolTip.showText(e.globalPos(), self.rowTip, self, self.rect())
            return True

        return super().event(e)

    def _contentPixmap(self):
        return self.__content()

    @staticmethod
    def __font(size, bold=False):
        font = QFont(['Segoe UI', 'Microsoft YaHei'])
        font.setPixelSize(size)
        font.setBold(bold)
        return font

    def __content(self):
        dark = isDarkTheme()
        deaths = deathsColor()
        dpr = self.devicePixelRatioF()
        key = (self.width(), self.height(), dpr, dark, deaths.rgba())
        if key == self._cacheKey:
            return self._cache

        fg = QColor(255, 255, 255) if dark else QColor(0, 0, 0)
        sub = QColor(255, 255, 255, 153) if dark else QColor(0, 0, 0, 153)

        out = QPixmap(round(self.width() * dpr), round(self.height() * dpr))
        out.setDevicePixelRatio(dpr)
        out.fill(Qt.transparent)

        p = QPainter(out)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform
                         | QPainter.TextAntialiasing)

        m, w = self.MARGIN, self.width()

        # 英雄头像: 圆形, 金色描边 (同 RoundIcon(..., 34, 2, 2))
        iconRect = QRectF(m, 6, self.ICON, self.ICON)
        if not self.championPixmap.isNull():
            src = self.championPixmap.copy(2, 2, self.championPixmap.width() - 4,
                                           self.championPixmap.height() - 4)
            p.save()
            clip = QPainterPath()
            clip.addEllipse(iconRect)
            p.setClipPath(clip)
            p.drawPixmap(iconRect.toRect(), src)
            p.restore()
            p.setPen(QPen(QColor(120, 90, 40), 2))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(iconRect)

        # K / D / A + KDA
        x = m + self.ICON + 8
        bold15 = self.__font(15, True)
        regular15 = self.__font(15)
        for text, font, color in ((str(self.game['kills']), bold15, fg),
                                  (" / ", regular15, sub),
                                  (str(self.game['deaths']), bold15, deaths),
                                  (" / ", regular15, sub),
                                  (str(self.game['assists']), bold15, fg)):
            p.setFont(font)
            p.setPen(color)
            p.drawText(QRectF(x, 6, 200, 20), Qt.AlignLeft | Qt.AlignVCenter, text)
            x += QFontMetrics(font).horizontalAdvance(text)

        ratioColor = (None if self.remake else kdaColor(self.kda)) or sub
        p.setFont(self.__font(12))
        p.setPen(ratioColor)
        p.drawText(QRectF(m + self.ICON + 8, 26, 120, 16), Qt.AlignLeft | Qt.AlignVCenter,
                   f"{self.kda:.2f} KDA")

        # 右上: 参团率 / 补刀
        right = QRectF(w - m - 120, 6, 120, 20)
        p.setFont(self.__font(13))
        p.setPen(fg)
        p.drawText(right, Qt.AlignRight | Qt.AlignVCenter, self.kpText)
        p.setFont(self.__font(12))
        p.setPen(sub)
        p.drawText(QRectF(w - m - 120, 26, 120, 16), Qt.AlignRight | Qt.AlignVCenter,
                   self.csText)

        # 物品
        self.itemRects = []
        y = 6 + self.ICON + 4
        for i, (pixmap, itemId) in enumerate(self.items):
            rect = QRectF(m + i * (self.ITEM + 1), y, self.ITEM, self.ITEM)
            if not pixmap.isNull():
                p.save()
                clip = QPainterPath()
                clip.addRoundedRect(rect, 3, 3)
                p.setClipPath(clip)
                p.drawPixmap(rect.toRect(), pixmap)
                p.restore()
            self.itemRects.append((rect.toRect(), itemId))

        p.setFont(self.__font(12))
        p.setPen(sub)
        p.drawText(QRectF(w - m - 80, y, 80, self.ITEM), Qt.AlignRight | Qt.AlignVCenter,
                   self.modeText)

        # 标签胶囊 + 日期
        y += self.ITEM + 4
        x = m
        pillFont = self.__font(11, True)
        p.setFont(pillFont)
        metrics = QFontMetrics(pillFont)
        for text, color in self.tags:
            rect = QRectF(x, y, metrics.horizontalAdvance(text) + 14, 18)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(color))
            p.drawRoundedRect(rect, 8, 8)
            p.setPen(QColor(255, 255, 255))
            p.drawText(rect, Qt.AlignCenter, text)
            x += rect.width() + 4

        p.setFont(self.__font(12))
        p.setPen(sub)
        p.drawText(QRectF(w - m - 100, y, 100, 18), Qt.AlignRight | Qt.AlignVCenter,
                   self.dateText)

        p.end()

        self._cache, self._cacheKey = out, key
        self._contentVersion = getattr(self, '_contentVersion', 0) + 1
        return out


class ChampionStatRow(QWidget):
    """近期英雄一行 (自绘): 头像 | KDA | 场次 | 胜率 | 熟练度"""

    HEIGHT = 30

    def __init__(self, item, mastery=None, parent=None):
        super().__init__(parent)
        self.setFixedHeight(self.HEIGHT)
        self.item = item
        self.mastery = mastery
        self.icon = QPixmap(item['icon']) if item.get('icon') else QPixmap()

        name = connector.manager.champs.get(item['championId'], "") \
            if getattr(connector, 'manager', None) else ""
        tip = f"{name}  {item['wins']}W {item['losses']}L, {item['kda']:.2f} KDA".strip()
        if mastery:
            tip += f"\n{self.tr('Mastery')} {mastery['level']}, {mastery['points']:,} pts"
        self.setToolTip(tip)

    def paintEvent(self, e):
        # 内容缓存成图, 滚动时只贴图 (按宽度 / 缩放比 / 主题重画)
        dpr = self.devicePixelRatioF()
        key = (self.width(), dpr, isDarkTheme())
        if key != getattr(self, '_cacheKey', None):
            self._cache, self._cacheKey = self.__render(dpr), key

        painter = QPainter(self)
        painter.drawPixmap(0, 0, self._cache)
        painter.end()

    def __render(self, dpr):
        pal = palette()
        item = self.item
        out = QPixmap(round(self.width() * dpr), round(self.HEIGHT * dpr))
        out.setDevicePixelRatio(dpr)
        out.fill(Qt.transparent)

        p = QPainter(out)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform
                         | QPainter.TextAntialiasing)
        h, w = self.HEIGHT, self.width()

        icon = QRectF(4, (h - 24) / 2, 24, 24)
        if not self.icon.isNull():
            p.save()
            clip = QPainterPath()
            clip.addEllipse(icon)
            p.setClipPath(clip)
            p.drawPixmap(icon.toRect(), self.icon)
            p.restore()
        p.setPen(QPen(pal['gold'], 1.5))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(icon.adjusted(0.75, 0.75, -0.75, -0.75))

        p.setFont(numberFont(13))
        p.setPen(kdaColor(item['kda']) or pal['ink'])
        p.drawText(QRectF(36, 0, 80, h), Qt.AlignLeft | Qt.AlignVCenter, f"{item['kda']:.2f}")
        p.setFont(textFont(12))
        p.setPen(pal['muted'])
        p.drawText(QRectF(36 + QFontMetrics(numberFont(13)).horizontalAdvance(
            f"{item['kda']:.2f}") + 3, 0, 40, h), Qt.AlignLeft | Qt.AlignVCenter, "KDA")

        games = item['wins'] + item['losses']
        rate = round(item['wins'] / games * 100) if games else 0
        right = w - 4
        if self.mastery:
            p.setFont(numberFont(12, 400))
            p.setPen(pal['gold'])
            p.drawText(QRectF(right - 40, 0, 40, h), Qt.AlignRight | Qt.AlignVCenter,
                       f"M{self.mastery['level']}")
        right -= 46
        p.setFont(numberFont(13))
        p.setPen(winRateColor(rate) or pal['ink'])
        p.drawText(QRectF(right - 44, 0, 44, h), Qt.AlignRight | Qt.AlignVCenter, f"{rate}%")
        right -= 50
        p.setFont(numberFont(12, 400))
        p.setPen(pal['muted'])
        p.drawText(QRectF(right - 36, 0, 36, h), Qt.AlignRight | Qt.AlignVCenter,
                   f"{item['games']}G")
        p.end()
        return out
