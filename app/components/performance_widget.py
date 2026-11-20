"""
生涯页 "Performance" 面板 (OP.GG 风格): 近期对局 vs 本赛季, 2 x 2 数据格, 全部自绘.

    Performance  >  Recent 20 games vs season 143 games
    ┌ Win rate (?) ───────────┐ ┌ Avg. KDA (?) ───────────┐
    │ 55%  [+5%]              │ │ 1.62  [+0.04]           │
    │ ▬▬▬▬▬▬▬|▭▭▭▭            │ │                         │
    │ Season 50%              │ │ Season 1.58             │
    └─────────────────────────┘ └─────────────────────────┘
    ┌ Kill participation (?) ─┐ ┌ Avg. game length (?) ───┐
    │ 51%  [+2%]   Season 49% │ │ 26:49 [-0:46]  ...      │

增减胶囊: 近期更好为蓝, 更差为红, 持平为灰; 时长没有好坏, 用中性青色.
数据由 app/lol/season_stats.aggregate 给出; 赛季取不到时只显示近期值, 不画胶囊.
"""
from PySide6.QtCore import Qt, QRectF, QRect, QEvent
from PySide6.QtGui import QColor, QPainter, QPen, QFontMetrics
from PySide6.QtWidgets import QFrame, QSizePolicy

from app.components.rich_tooltip import RichToolTip

from app.common.qfluentwidgets import isDarkTheme
from app.components.design_tokens import numberFont, textFont, resultColor, tint
from app.components.scoreboard import drawText, drawRuns, cachedContent

BETTER = (QColor('#3E6FD8'), QColor('#6C9BFF'))
WORSE = (QColor('#D93A50'), QColor('#F2697C'))
TEAL = (QColor('#0E8F82'), QColor('#2EC4B0'))


def lengthText(secs):
    secs = int(round(abs(secs)))
    return f"{secs // 60}:{secs % 60:02d}"


class PerformancePanel(QFrame):
    HEIGHT = 268
    MARGIN = 18
    HEADER_H = 44
    TILE_H = 100
    GAP = 10

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(self.HEIGHT)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        self.recent = None
        self.season = None
        # 'loading' / 'ready' / 'unavailable'
        self.state = 'unavailable'
        self.seasonSource = ""
        self.helpRects = []
        self._hover = -1
        self._cache = None
        self._cacheKey = None
        self.setMouseTracking(True)

    # ---------------- 数据 ----------------

    def setRecent(self, recent, loadingSeason=True):
        """recent: aggregate() 结果 (可为 None); 赛季数据清空, 等 setSeason"""
        self.recent = recent
        self.season = None
        self.state = 'loading' if loadingSeason and recent else 'unavailable'
        self.seasonSource = ""
        self.newestOnly = False
        self._dirty()

    def setSeason(self, season, source="", newestOnly=False):
        """
        season: aggregate() 结果或 rankedSeasonFallback() 结果; None 表示取不到.
        newestOnly: the sample is only the newest season games (cap reached / a page failed)
        """
        self.season = season
        self.state = 'ready' if season else 'unavailable'
        self.seasonSource = source
        self.newestOnly = newestOnly
        self._dirty()

    def _dirty(self):
        self._cacheKey = None
        self.update()

    # ---------------- 文字 ----------------

    def headerText(self):
        recentN = self.recent['games'] if self.recent else 0
        text = self.tr("Recent %1 games").replace("%1", str(recentN))
        if self.state == 'loading':
            return text + "  ·  " + self.tr("loading season...")
        if self.state == 'ready':
            if self.season.get('rankedOnly'):
                return text + " " + self.tr("vs season %1 ranked games").replace(
                    "%1", str(self.season['games']))
            if getattr(self, 'newestOnly', False):
                return text + " " + self.tr("vs the newest %1 season games").replace(
                    "%1", str(self.season['games']))
            return text + " " + self.tr("vs season %1 games").replace(
                "%1", str(self.season['games']))
        return text

    def tileSpecs(self):
        """[(标题, 说明, 近期值字符串, 胶囊 (字符串, 颜色) 或 None, 赛季值字符串 或 None, 进度)]"""
        r, s = self.recent or {}, self.season or {}
        dark = isDarkTheme()
        neutral = None

        def pct(v):
            return f"{round(v * 100)}%" if v is not None else "--"

        def chip(diff, text, lengthStyle=False):
            if lengthStyle:
                return text, TEAL[dark]
            if diff > 0:
                return text, BETTER[dark]
            if diff < 0:
                return text, WORSE[dark]
            return text, neutral

        def pctChip(rv, sv):
            if rv is None or sv is None:
                return None
            d = round(rv * 100) - round(sv * 100)
            return chip(d, f"{d:+d}%" if d else "0%")

        kdaChip = None
        if r.get('kda') is not None and s.get('kda') is not None:
            d = round(r['kda'], 2) - round(s['kda'], 2)
            d = round(d, 2)
            kdaChip = chip(d, f"{d:+.2f}" if d else "0.00")

        lenChip = None
        if r.get('duration') is not None and s.get('duration') is not None:
            d = round(r['duration']) - round(s['duration'])
            lenChip = chip(d, ("-" if d < 0 else "+") + lengthText(d), True)

        seasonLabel = self.tr("Season")
        kpTip = self.tr("Kill participation: (kills + assists) / your team's kills, "
                        "averaged over games.")
        if s.get('kp') is not None:
            kpTip += "\n" + self.tr("Season value from %1 games with full team data.") \
                .replace("%1", str(s['kpGames']))
        winTip = self.tr("Share of games won. Remakes are not counted.")
        if s.get('rankedOnly'):
            winTip += "\n" + self.tr("Season value: Ranked Solo/Duo + Flex record.")

        def season(text, value):
            return f"{seasonLabel} {text}" if value is not None else None

        return [
            (self.tr("Win rate"), winTip, pct(r.get('winRate')),
             pctChip(r.get('winRate'), s.get('winRate')),
             season(pct(s.get('winRate')), s.get('winRate')),
             (r.get('winRate'), s.get('winRate'))),
            (self.tr("Avg. KDA"),
             self.tr("(Kills + Assists) / Deaths, summed over the games."),
             f"{r['kda']:.2f}" if r.get('kda') is not None else "--", kdaChip,
             season(f"{s.get('kda', 0) or 0:.2f}", s.get('kda')), None),
            (self.tr("Kill participation"), kpTip, pct(r.get('kp')),
             pctChip(r.get('kp'), s.get('kp')), season(pct(s.get('kp')), s.get('kp')), None),
            (self.tr("Avg. game length"),
             self.tr("Average game duration, remakes excluded. Neither longer nor "
                     "shorter is better, so the change is shown in a neutral color."),
             lengthText(r['duration']) if r.get('duration') is not None else "--", lenChip,
             season(lengthText(s.get('duration') or 0), s.get('duration')), None),
        ]

    # ---------------- 交互 ----------------

    def mouseMoveEvent(self, e):
        pos = e.position().toPoint()
        hover = next((i for i, (rect, _) in enumerate(self.helpRects) if rect.contains(pos)), -1)
        if hover != self._hover:
            self._setHover(hover)
            if hover >= 0:
                rect, tip = self.helpRects[hover]
                RichToolTip.showText(e.globalPosition().toPoint(), tip, self, rect)
        super().mouseMoveEvent(e)

    def leaveEvent(self, e):
        self._setHover(-1)
        super().leaveEvent(e)

    def _setHover(self, index):
        """悬停的 (?) 序号, -1 为没有; 变化时立刻显示/收起说明并重绘高亮"""
        if index < 0:
            self.unsetCursor()
            tip = RichToolTip._instance
            if self._hover >= 0 and tip is not None and tip._owner is self:
                RichToolTip.hideText()
        else:
            self.setCursor(Qt.PointingHandCursor)
        self._hover = index
        self.update()

    def event(self, e):
        if e.type() == QEvent.ToolTip:
            for rect, tip in self.helpRects:
                if rect.contains(e.pos()):
                    RichToolTip.showText(e.globalPos(), tip, self, rect)
                    return True
            RichToolTip.hideText()
            e.ignore()
            return True
        return super().event(e)

    # ---------------- 绘制 ----------------

    def paintEvent(self, e):
        p = QPainter(self)
        p.drawPixmap(0, 0, cachedContent(self, self.__draw, (self._hover,)))
        p.end()

    def __draw(self, p, pal):
        # 卡片底 (同 career_widgets.drawCard)
        p.setPen(QPen(pal['hairline'], 1))
        p.setBrush(pal['sheet'])
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 10, 10)

        m, w = self.MARGIN, self.width()
        x = drawRuns(p, m, 14, 20, ((self.tr("Performance"), textFont(13, True), pal['ink']),
                                    ("  ›  ", textFont(13), pal['muted'])))
        drawText(p, QRectF(x, 14, w - x - m, 20), self.headerText(), textFont(12),
                 pal['muted'])

        tileW = (w - 2 * m - self.GAP) / 2
        self.helpRects = []
        for i, spec in enumerate(self.tileSpecs()):
            row, col = divmod(i, 2)
            rect = QRectF(m + col * (tileW + self.GAP),
                          self.HEADER_H + row * (self.TILE_H + self.GAP), tileW, self.TILE_H)
            self.__drawTile(p, pal, rect, *spec)

    def __drawTile(self, p, pal, rect, title, tip, value, chip, seasonText, progress):
        ink, muted = pal['ink'], pal['muted']
        p.setPen(QPen(pal['hairline'], 1))
        p.setBrush(tint(pal['hairline'], 70))
        p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 8, 8)

        x, y = rect.left() + 14, rect.top()

        # 标题 + (?)
        titleFont = textFont(12)
        titleW = QFontMetrics(titleFont).horizontalAdvance(title)
        drawText(p, QRectF(x, y + 10, titleW + 2, 18), title, titleFont, muted)
        helpColor = ink if self._hover == len(self.helpRects) else muted
        help_ = QRectF(x + titleW + 6, y + 11, 16, 16)
        p.setPen(QPen(helpColor, 1.1))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(help_.adjusted(0.5, 0.5, -0.5, -0.5))
        drawText(p, help_, "?", textFont(11, True), helpColor, Qt.AlignCenter)
        self.helpRects.append((QRect(int(x), int(y + 8), int(titleW + 26), 22), tip))

        # 近期值 + 增减胶囊
        valueFont = numberFont(24)
        end = drawRuns(p, x, y + 30, 32, ((value, valueFont, ink),))
        if chip:
            text, color = chip
            chipFont = numberFont(12)
            cw = QFontMetrics(chipFont).horizontalAdvance(text) + 14
            chipRect = QRectF(end + 8, y + 37, cw, 20)
            p.setPen(Qt.NoPen)
            p.setBrush(tint(color, 40) if color is not None else pal['hairline'])
            p.drawRoundedRect(chipRect, 10, 10)
            drawText(p, chipRect, text, chipFont, color if color is not None else muted,
                     Qt.AlignCenter)

        # 胜率条 + 赛季刻度
        if progress is not None and progress[0] is not None:
            bar = QRectF(x, y + 70, rect.width() - 28, 6)
            p.setPen(Qt.NoPen)
            p.setBrush(pal['hairline'])
            p.drawRoundedRect(bar, 3, 3)
            p.setBrush(resultColor('win'))
            p.drawRoundedRect(QRectF(bar.left(), bar.top(), bar.width() * progress[0],
                                     bar.height()), 3, 3)
            if progress[1] is not None:
                tx = bar.left() + bar.width() * progress[1]
                p.setPen(QPen(ink, 2))
                p.drawLine(QRectF(tx, bar.top() - 3, 0, bar.height() + 6).topLeft(),
                           QRectF(tx, bar.top() - 3, 0, bar.height() + 6).bottomLeft())
            seasonY = y + 80
        else:
            seasonY = y + 72

        if seasonText:
            drawText(p, QRectF(x, seasonY, rect.width() - 28, 18), seasonText,
                     numberFont(12, 400), muted)
