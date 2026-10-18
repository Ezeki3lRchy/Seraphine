"""
生涯页控件 ("赛后记分表" 风格): 段位卡, 近期对局概况 (胜负环 / KDA / 参团率 / 常用英雄 / 分路 /
近期走势条), 以及单局战绩行. 全部自绘, 颜色与字体见 design_tokens.py.
"""
import time

from PySide6.QtCore import Qt, QRectF, QRect, QEvent
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QFontMetrics, QPixmap
from PySide6.QtWidgets import QFrame, QSizePolicy, QToolTip

from app.common.qfluentwidgets import isDarkTheme
from app.common.signals import signalBus
from app.common.style_sheet import colorManager
from app.components.animation_frame import ColorAnimationFrame
from app.components.design_tokens import palette, numberFont, textFont, resultColor, tint
from app.components.match_widgets import rankEmblemPath, gameTags, itemTooltipHtml, TAG_COLORS
from app.lol.connector import connector
from app.lol.tools import summarizeGames


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


def resultType(game):
    if game.get('remake'):
        return 'remake'
    return 'win' if game.get('win') else 'lose'


def kdaColor(kda):
    if 3 <= kda < 4:
        return QColor(0, 163, 80)
    elif 4 <= kda < 5:
        return QColor(0, 147, 255)
    elif 5 <= kda:
        return QColor(240, 111, 0)
    return None


def drawCard(p, rect, pal, radius=10):
    """卡片底: 纸面 + 发丝线描边, 不加阴影"""
    p.setPen(QPen(pal['hairline'], 1))
    p.setBrush(pal['sheet'])
    p.drawRoundedRect(QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)


def drawText(p, rect, string, font, color, align=Qt.AlignLeft | Qt.AlignVCenter):
    p.setFont(font)
    p.setPen(color)
    p.drawText(rect, align, string)


def drawRuns(p, x, y, h, runs):
    """一行里多段不同字体 / 颜色的文字, 依次排开; 返回结束的 x"""
    for string, font, color in runs:
        drawText(p, QRectF(x, y, 1000, h), string, font, color)
        x += QFontMetrics(font).horizontalAdvance(string)
    return x


def drawRoundedPixmap(p, pixmap, rect, radius):
    if pixmap is None or pixmap.isNull():
        return
    p.save()
    clip = QPainterPath()
    clip.addRoundedRect(rect, radius, radius)
    p.setClipPath(clip)
    p.drawPixmap(rect.toRect(), pixmap)
    p.restore()


def drawChampion(p, pixmap, rect, gold, ring=2, crop=0):
    """圆形英雄头像 + 海克斯金描边"""
    if pixmap is not None and not pixmap.isNull():
        src = pixmap.copy(crop, crop, pixmap.width() - 2 * crop,
                          pixmap.height() - 2 * crop) if crop else pixmap
        p.save()
        clip = QPainterPath()
        clip.addEllipse(rect)
        p.setClipPath(clip)
        p.drawPixmap(rect.toRect(), src)
        p.restore()
    p.setPen(QPen(gold, ring))
    p.setBrush(Qt.NoBrush)
    p.drawEllipse(rect.adjusted(ring / 2, ring / 2, -ring / 2, -ring / 2))


class RankCard(QFrame):
    """
    段位卡:

        Ranked Solo/Duo
        [徽章]  Diamond II               455W 462L
                55 LP              ▬▬▬▬▬▬▭▭▭ 50%
        Peak  Master      Last season  Diamond I
    """

    HEIGHT = 132

    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setFixedHeight(self.HEIGHT)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.title = title
        self.entry = {}
        self.emblem = QPixmap()

    def updateRank(self, entry: dict):
        """entry: LCU ranked-stats 的 queueMap 某一项 (可为空)"""
        self.entry = entry or {}
        tier = tierText(self.entry.get('tier'), self.entry.get('division'))
        self.emblem = QPixmap(rankEmblemPath(self.entry.get('tier') if tier else ''))
        self.update()

    def paintEvent(self, e):
        pal = palette()
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform
                         | QPainter.TextAntialiasing)
        drawCard(p, self.rect(), pal)

        w, entry = self.width(), self.entry
        drawText(p, QRectF(18, 12, 300, 20), self.title, textFont(13, True), pal['ink'])

        if not self.emblem.isNull():
            p.drawPixmap(QRectF(10, 30, 80, 80).toRect(), self.emblem)

        tier = tierText(entry.get('tier'), entry.get('division'))
        drawText(p, QRectF(98, 40, 300, 32), tier or self.tr("Unranked"),
                 numberFont(26), pal['ink'])
        if tier:
            drawText(p, QRectF(99, 72, 200, 20), f"{entry.get('leaguePoints', 0)} LP",
                     numberFont(14, 400), pal['muted'])

        # 赛季胜负 + 胜率条 (其他玩家 LCU 常把负场给 0, 那时只写胜场, 不画会误导的条)
        wins, losses = entry.get('wins', 0), entry.get('losses', 0)
        right = w - 18
        if losses:
            drawText(p, QRectF(right - 200, 40, 200, 22), f"{wins}W {losses}L",
                     numberFont(15, 400), pal['ink'], Qt.AlignRight | Qt.AlignVCenter)
            rate = wins / (wins + losses)
            rateText = f"{round(rate * 100)}%"
            rateFont = numberFont(15)
            rateW = QFontMetrics(rateFont).horizontalAdvance(rateText)
            drawText(p, QRectF(right - rateW, 64, rateW, 20), rateText, rateFont, pal['ink'],
                     Qt.AlignRight | Qt.AlignVCenter)
            bar = QRectF(right - rateW - 10 - 120, 71, 120, 6)
            p.setPen(Qt.NoPen)
            p.setBrush(pal['hairline'])
            p.drawRoundedRect(bar, 3, 3)
            p.setBrush(resultColor('win'))
            p.drawRoundedRect(QRectF(bar.left(), bar.top(), bar.width() * rate, bar.height()),
                              3, 3)
        elif wins:
            drawText(p, QRectF(right - 200, 40, 200, 22), f"{wins}W", numberFont(15, 400),
                     pal['ink'], Qt.AlignRight | Qt.AlignVCenter)

        # 赛季最高 / 上赛季: 标签 + 数值, 不用点号串起来
        x = 18
        for label, value in ((self.tr("Peak"), tierText(entry.get('highestTier'),
                                                          entry.get('highestDivision'))),
                             (self.tr("Last season"),
                              tierText(entry.get('previousSeasonEndTier'),
                                       entry.get('previousSeasonEndDivision')))):
            if not value:
                continue
            x = drawRuns(p, x, 104, 18, ((label + "  ", textFont(12), pal['muted']),
                                         (value, textFont(13, True), pal['ink'])))
            x += 28

        p.end()


class RecentSummaryCard(QFrame):
    """
    近期对局概况:

        (胜负环)  Last 20 games       Most played            Roles (ranked)
                  7.1 / 9.1 / 5.4     (头) 56%  5W 4L ...      ▮ ▮ ▮ ▮ ▮
                  1.37:1 KDA
                  KP 48%
        Recent form  ■ ■ □ ■ □ ...   (每格一局, 最新在左; 悬停看本局, 点击打开详情)
    """

    HEIGHT = 178
    TILE = 22
    TILE_GAP = 4
    ROLES = [('TOP', 'top', 'Top'), ('JUNGLE', 'jng', 'Jungle'), ('MIDDLE', 'mid', 'Mid'),
             ('BOTTOM', 'bot', 'Bot'), ('UTILITY', 'sup', 'Support')]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(self.HEIGHT)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.setMouseTracking(True)

        self.games = []
        self.summary = None
        self.championPixmaps = []
        self.roleIcons = {key: QPixmap(f"app/resource/images/icon-position-{icon}.svg")
                          for key, icon, _ in self.ROLES}
        self.tileRects = []
        self.rolesRect = QRect()

    def updateGames(self, games):
        self.games = [g for g in games or [] if g]
        self.summary = summarizeGames(self.games, limit=3)
        self.championPixmaps = [QPixmap(item['icon']) if item.get('icon') else QPixmap()
                                for item in self.summary['topChampions']]
        self.update()

    # ---------------- 交互: 走势条每格 ----------------

    def __tileAt(self, pos):
        for rect, game in self.tileRects:
            if rect.contains(pos):
                return game
        return None

    def event(self, e):
        if e.type() == QEvent.ToolTip:
            game = self.__tileAt(e.pos())
            if game:
                name = connector.manager.champs.get(game['championId'], "") \
                    if getattr(connector, 'manager', None) else ""
                tip = f"{name}  {game['kills']}/{game['deaths']}/{game['assists']}\n" \
                      f"{game['name']}, {timeAgo(game['timeStamp'])}"
                QToolTip.showText(e.globalPos(), tip.strip(), self)
                return True
            if self.rolesRect.contains(e.pos()) and self.summary:
                counts = dict(self.summary['positions'])
                QToolTip.showText(e.globalPos(), "\n".join(
                    f"{label}: {counts.get(key, 0)}" for key, _, label in self.ROLES), self)
                return True
            QToolTip.hideText()
            e.ignore()
            return True
        return super().event(e)

    def mouseMoveEvent(self, e):
        self.setCursor(Qt.PointingHandCursor if self.__tileAt(e.position().toPoint())
                       else Qt.ArrowCursor)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        game = self.__tileAt(e.position().toPoint())
        if game:
            signalBus.careerGameBarClicked.emit(str(game['gameId']))
            return
        super().mouseReleaseEvent(e)

    # ---------------- 绘制 ----------------

    def paintEvent(self, e):
        pal = palette()
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform
                         | QPainter.TextAntialiasing)
        drawCard(p, self.rect(), pal)

        if self.summary is None:
            p.end()
            return

        games = self.games
        counted = [g for g in games if not g.get('remake')]
        wins = sum(1 for g in counted if g['win'])
        losses = len(counted) - wins
        winC, loseC = resultColor('win'), resultColor('lose')

        # 胜负环
        ring = QRectF(22, 20, 88, 88)
        pen = QPen(tint(loseC, 110) if counted else pal['hairline'], 9)
        pen.setCapStyle(Qt.FlatCap)
        p.setPen(pen)
        p.drawArc(ring.adjusted(4.5, 4.5, -4.5, -4.5), 0, 360 * 16)
        if counted:
            pen.setColor(winC)
            p.setPen(pen)
            p.drawArc(ring.adjusted(4.5, 4.5, -4.5, -4.5), 90 * 16,
                      int(360 * 16 * wins / len(counted)))
        drawText(p, ring, f"{round(wins / len(counted) * 100)}%" if counted else "--",
                 numberFont(20), pal['ink'], Qt.AlignCenter)

        # KDA 汇总
        x = 132
        drawText(p, QRectF(x, 16, 220, 18),
                 self.tr("Last %1 games").replace("%1", str(len(games))) + f"   {wins}W {losses}L",
                 textFont(12), pal['muted'])
        n = max(len(counted), 1)
        k = sum(g['kills'] for g in counted)
        d = sum(g['deaths'] for g in counted)
        a = sum(g['assists'] for g in counted)
        slash = numberFont(15, 400)
        drawRuns(p, x, 36, 22, ((f"{k / n:.1f}", numberFont(15), pal['ink']),
                                (" / ", slash, pal['muted']),
                                (f"{d / n:.1f}", numberFont(15), loseC),
                                (" / ", slash, pal['muted']),
                                (f"{a / n:.1f}", numberFont(15), pal['ink'])))
        kda = (k + a) / max(d, 1)
        end = drawRuns(p, x, 58, 34, ((f"{kda:.2f}:1", numberFont(26),
                                       kdaColor(kda) or pal['ink']),))
        drawText(p, QRectF(end + 6, 66, 60, 20), "KDA", textFont(12), pal['muted'])
        kps = [g['kp'] for g in counted if g.get('kp') is not None]
        if kps:
            drawRuns(p, x, 92, 20, (("KP  ", textFont(12), pal['muted']),
                                    (f"{round(sum(kps) / len(kps) * 100)}%",
                                     numberFont(15), pal['ink'])))

        # 常用英雄
        cx = 330
        drawText(p, QRectF(cx, 16, 200, 18), self.tr("Most played"), textFont(12), pal['muted'])
        for i, item in enumerate(self.summary['topChampions']):
            y = 38 + i * 26
            drawChampion(p, self.championPixmaps[i], QRectF(cx, y, 22, 22), pal['gold'], 1.5)
            games_ = item['wins'] + item['losses']
            rate = round(item['wins'] / games_ * 100) if games_ else 0
            drawRuns(p, cx + 30, y + 1, 20, (
                (f"{rate}%", numberFont(14), winC if rate >= 60 else pal['ink']),
                (f"   {item['wins']}W {item['losses']}L", numberFont(13, 400), pal['muted']),
                (f"   {item['kda']:.2f} KDA", numberFont(13, 400), pal['muted'])))

        # 分路 (排位): 最常用的一路用海克斯金
        counts = dict(self.summary['positions'])
        total = max(sum(counts.values()), 1)
        top = max(counts, key=counts.get) if counts else None
        rw = len(self.ROLES) * 28
        rx = self.width() - 22 - rw
        drawText(p, QRectF(rx - 40, 16, rw + 40, 18), self.tr("Roles (ranked)"),
                 textFont(12), pal['muted'], Qt.AlignRight | Qt.AlignVCenter)
        for i, (key, _, _) in enumerate(self.ROLES):
            bx = rx + i * 28 + 8
            barTop, barH = 40, 56
            p.setPen(Qt.NoPen)
            p.setBrush(pal['hairline'])
            p.drawRoundedRect(QRectF(bx, barTop, 10, barH), 3, 3)
            h = barH * counts.get(key, 0) / total
            if h:
                p.setBrush(pal['gold'] if key == top else pal['muted'])
                p.drawRoundedRect(QRectF(bx, barTop + barH - h, 10, h), 3, 3)
            p.drawPixmap(QRectF(bx - 3, barTop + barH + 6, 16, 16).toRect(), self.roleIcons[key])
        self.rolesRect = QRect(int(rx), 36, rw, 84)

        # 近期走势: 每格一局, 最新在左
        y = 132
        label = self.tr("Recent form")
        drawText(p, QRectF(22, y, 110, self.TILE), label, textFont(12), pal['muted'])
        tx = 132
        self.tileRects = []
        for game in games:
            if tx + self.TILE > self.width() - 22:
                break
            t = resultType(game)
            rect = QRectF(tx, y, self.TILE, self.TILE)
            p.setPen(Qt.NoPen)
            p.setBrush(resultColor(t) if t != 'remake' else pal['hairline'])
            p.drawRoundedRect(rect, 4, 4)
            drawText(p, rect, {'win': "W", 'lose': "L", 'remake': "R"}[t], numberFont(11),
                     QColor(255, 255, 255) if t != 'remake' else pal['muted'], Qt.AlignCenter)
            self.tileRects.append((rect.toRect(), game))
            tx += self.TILE + self.TILE_GAP
        if games:
            drawText(p, QRectF(tx + 6, y, 120, self.TILE), self.tr("Newest first"),
                     textFont(11), pal['muted'])

        p.end()


class CareerGameRow(ColorAnimationFrame):
    """
    生涯页一局 (记分表一行), 整行自绘:

        ▌Ranked Solo/Duo  [英雄][技能][符文]  4 / 10 / 10   [物品 x 7]      KP 50%   玩家1 玩家6
        ▌34 minutes ago                       1.40:1 KDA    [4th]           CS 346   ...
        ▌Defeat  32m 59s                                                    Gold 18,199

    胜负由左侧实色边条 + 浅色底表达 (颜色取自设置里的卡片颜色), 数字用 Bahnschrift.
    物品详情 / 数据说明 / 点玩家名打开生涯靠命中测试.
    """

    HEIGHT = 104
    MARGIN = 18
    INFO_W = 132
    ICON = 56
    SMALL = 24
    KDA_W = 128
    ITEM = 26
    STATS_W = 130
    CHIP_W = 108

    def __init__(self, game: dict, currentPuuid=None, parent=None):
        type = resultType(game)

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
        self.items = [(QPixmap(icon) if itemId else QPixmap(), itemId)
                      for icon, itemId in zip(game['itemIcons'], itemIds)]
        self.tags = gameTags(game)

        teams = {}
        for player in game.get('participants') or []:
            teams.setdefault(player['teamId'], []).append(player)
        self.teams = [team[:5] for team in list(teams.values())[:2]]
        self.playerIcons = {id(p): QPixmap(p['championIcon'])
                            for team in self.teams for p in team}

        kp = game.get('kp')
        self.kpValue = f"{round(kp * 100)}%" if kp is not None else ""
        secs = game.get('durationSecs') or 0
        cs = game.get('cs', 0)
        self.csValue = f"{cs}" + (f" ({cs / (secs / 60):.1f})" if secs >= 60 else "")
        self.goldValue = f"{game.get('gold', 0):,}"
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
                if rect.contains(pos) and itemId:
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
        pal = palette()
        color = resultColor(self.resultType)
        hover = getattr(self, 'isHover', False)

        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)

        # 底: 纸面 + 结果色浅底 (悬停时稍深) + 左侧实色边条
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, 8, 8)
        p.setPen(QPen(pal['hairline'], 1))
        p.setBrush(pal['sheet'])
        p.drawPath(path)
        p.setPen(Qt.NoPen)
        p.setBrush(tint(color, 30 if hover else 15))
        p.drawPath(path)
        p.save()
        p.setClipPath(path)
        p.fillRect(QRectF(0, 0, 5, self.height()), color if self.resultType != 'remake'
                   else pal['muted'])
        p.restore()

        p.drawPixmap(0, 0, self.__content())
        p.end()

    def __content(self):
        dark = isDarkTheme()
        pal = palette(dark)
        deathsColor = colorManager.getColor('deaths')[0]
        color = resultColor(self.resultType)
        dpr = self.devicePixelRatioF()
        key = (self.width(), self.height(), dpr, dark, deathsColor.rgba(), color.rgba())
        if key == self._cacheKey:
            return self._cache

        ink, muted = pal['ink'], pal['muted']

        out = QPixmap(round(self.width() * dpr), round(self.height() * dpr))
        out.setDevicePixelRatio(dpr)
        out.fill(Qt.transparent)

        p = QPainter(out)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform
                         | QPainter.TextAntialiasing)

        m, w, h = self.MARGIN, self.width(), self.height()

        # 左: 模式 / 多久前 / 结果 + 时长
        x = m
        drawText(p, QRectF(x, 14, self.INFO_W, 20), self.game['name'], textFont(13, True), ink)
        drawText(p, QRectF(x, 34, self.INFO_W, 18), self.agoText, textFont(12), muted)
        end = drawRuns(p, x, 62, 22, ((self.resultText, textFont(14, True),
                                       color if self.resultType != 'remake' else muted),))
        drawText(p, QRectF(end + 8, 63, 100, 22), self.durationText, numberFont(13, 400), muted)
        self.infoRect = QRect(x, 14, self.INFO_W, 72)
        x += self.INFO_W + 8

        # 英雄头像 (金边) + 等级角标
        iconRect = QRectF(x, (h - self.ICON) / 2, self.ICON, self.ICON)
        drawChampion(p, self.championPixmap, iconRect, pal['gold'], 2, crop=6)
        level = QRectF(iconRect.right() - 18, iconRect.bottom() - 18, 22, 22)
        p.setPen(QPen(pal['gold'], 1.5))
        p.setBrush(pal['sheet'])
        p.drawEllipse(level)
        drawText(p, level, str(self.game['champLevel']), numberFont(11), ink, Qt.AlignCenter)
        x += self.ICON + 14

        # 召唤师技能 (竖排) + 基石符文
        top = (h - 2 * self.SMALL - 3) / 2
        for i, spell in enumerate(self.spells):
            drawRoundedPixmap(p, spell, QRectF(x, top + i * (self.SMALL + 3),
                                               self.SMALL, self.SMALL), 4)
        x += self.SMALL + 4
        if not self.rune.isNull():
            p.drawPixmap(QRectF(x, top, self.SMALL, self.SMALL).toRect(), self.rune)
        x += self.SMALL + 16

        # K / D / A (记分牌数字) + KDA
        kdaTop = h / 2 - 24
        bold, thin = numberFont(21), numberFont(21, 300)
        drawRuns(p, x, kdaTop, 28, ((str(self.game['kills']), bold, ink),
                                    (" / ", thin, muted),
                                    (str(self.game['deaths']), bold, deathsColor),
                                    (" / ", thin, muted),
                                    (str(self.game['assists']), bold, ink)))
        kda = (self.game['kills'] + self.game['assists']) / max(self.game['deaths'], 1)
        drawRuns(p, x, kdaTop + 30, 18, ((f"{kda:.2f}:1", numberFont(13),
                                          kdaColor(kda) or muted),
                                         ("  KDA", textFont(12), muted)))
        x += self.KDA_W + 8
        itemsX = x
        # 物品列定宽, 数据紧跟其后; 多出来的宽度留在玩家列表之前
        statsX = itemsX + 7 * (self.ITEM + 3) + 24

        # 右: 本局 10 名玩家 (两队各一列)
        right = w - m
        self.playerRects = []
        if self.teams:
            playersX = right - 2 * self.CHIP_W - 8
            rowH = 17
            playersTop = (h - 5 * rowH) / 2
            for column, team in enumerate(self.teams):
                for row, player in enumerate(team):
                    px = playersX + column * (self.CHIP_W + 8)
                    py = playersTop + row * rowH
                    drawRoundedPixmap(p, self.playerIcons[id(player)],
                                      QRectF(px, py + 0.5, 16, 16), 3)
                    isSelf = player.get('puuid') == self.currentPuuid
                    nameFont = textFont(12, isSelf)
                    name = QFontMetrics(nameFont).elidedText(
                        player['name'] or "?", Qt.ElideRight, self.CHIP_W - 22)
                    drawText(p, QRectF(px + 21, py, self.CHIP_W - 21, rowH), name,
                             nameFont, ink if isSelf else muted)
                    self.playerRects.append(
                        (QRect(int(px), int(py), self.CHIP_W, rowH), player))
            right = playersX - 18

        # 参团率 / 补刀 / 金币: 灰色标签 + 墨色数字
        statsX = min(statsX, right - self.STATS_W)
        statsTop = h / 2 - 30
        for i, (label, value, size) in enumerate((("KP", self.kpValue, 16),
                                                  ("CS", self.csValue, 14),
                                                  (self.tr("Gold"), self.goldValue, 14))):
            if not value:
                continue
            y = statsTop + i * 21
            drawText(p, QRectF(statsX, y, 40, 20), label, textFont(12), muted)
            drawText(p, QRectF(statsX + 40, y, self.STATS_W - 40, 20), value,
                     numberFont(size, 600 if i == 0 else 400), ink)
        self.statsRect = QRect(int(statsX), int(statsTop), self.STATS_W, 62)

        # 物品 (空格画发丝线框) + 标签
        self.itemRects = []
        itemsTop = h / 2 - self.ITEM - 4
        for i, (pixmap, itemId) in enumerate(self.items):
            rect = QRectF(itemsX + i * (self.ITEM + 3), itemsTop, self.ITEM, self.ITEM)
            if itemId:
                drawRoundedPixmap(p, pixmap, rect, 5)
            else:
                p.setPen(QPen(pal['hairline'], 1))
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 5, 5)
            self.itemRects.append((rect.toRect(), itemId))

        tagX, tagY = itemsX, itemsTop + self.ITEM + 8
        pillFont = textFont(12, True)
        metrics = QFontMetrics(pillFont)
        for string, tagColor in self.tags:
            rect = QRectF(tagX, tagY, metrics.horizontalAdvance(string) + 18, 22)
            quiet = tagColor == TAG_COLORS['rank']
            p.setPen(Qt.NoPen)
            # 名次标签低调 (发丝线底 + 墨色字), MVP 用海克斯金
            fill = pal['hairline'] if quiet else (
                pal['gold'] if string == "MVP" else QColor(tagColor))
            p.setBrush(fill)
            p.drawRoundedRect(rect, 11, 11)
            drawText(p, rect, string, pillFont, ink if quiet else QColor(255, 255, 255),
                     Qt.AlignCenter)
            tagX += rect.width() + 6

        p.end()

        self._cache, self._cacheKey = out, key
        return out
