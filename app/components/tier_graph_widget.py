"""
生涯页的 "段位走势" (Tier graph), 仿 OP.GG 个人页的 Tier graph: 段位 / LP 随时间的折线.
数据来自 app.lol.rank_history 的本地快照 (每次读到段位时记一笔).

- 标题行点击展开 / 收起 (默认收起, 像 OP.GG 藏在按钮后面); 展开后右侧是队列 (单双 / 灵活)
  与 日 / 周 / 月 两组分段切换;
- 每个点标注 "D 3" + LP, 横轴为 MM/DD; 点按序号等距排开, 只画放得下的最近 N 个;
- 纵轴: 大段 400 / 小段 100, 大师以上 = 钻石 I 顶 + LP, 所以折线随段位单调;
- 少于 2 个点时显示空状态说明.
底图缓存成 pixmap (cachedContent), 悬浮点每次画在缓存之上.
"""
import datetime

from PySide6.QtCore import Qt, QRectF, QPointF, QEvent
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QFontMetrics
from PySide6.QtWidgets import QWidget, QSizePolicy

from app.components.rich_tooltip import RichToolTip

from app.common.qfluentwidgets import isDarkTheme
from app.components.design_tokens import numberFont, textFont, tint, palette
from app.components.paint import drawCard, drawText
from app.components.scoreboard import cachedContent
from app.lol import rank_history

PAD = 16
RADIUS = 10
LINE_COLORS = {False: QColor('#2A78D6'), True: QColor('#3987E5')}


def fullTierText(tier, division):
    return f"{tier.title()} {division}".strip()


def tickLabel(value):
    """纵轴刻度: 小段起点 'D 4'; 大师以上写 LP"""
    if value >= rank_history.APEX_BASE:
        lp = value - rank_history.APEX_BASE
        return f"{lp} LP" if lp else "M"
    tier = rank_history.TIERS[int(value // 400)]
    division = ('IV', 'III', 'II', 'I')[int(value % 400 // 100)]
    return rank_history.shortLabel(tier, division)


class TierGraphCard(QWidget):
    """段位走势卡片: 标题行 (可展开) + 分段切换 + 折线图"""

    HEADER_H = 46
    SEG_H = 28
    PLOT_TOP = 54          # 展开后折线区域的顶 (含标注留白)
    LABEL_ROOM = 38        # 点上方两行标注
    PLOT_H = 190
    X_LABEL_H = 24
    AXIS_W = 52
    POINT_GAP = 66         # 相邻点的最小间距 (放得下 "D 3 / 100 LP")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.setMouseTracking(True)

        self.queueLabels = {'RANKED_SOLO_5x5': self.tr("Solo/Duo"),
                            'RANKED_FLEX_SR': self.tr("Flex")}
        self.periodLabels = {'day': self.tr("Daily"), 'week': self.tr("Weekly"),
                             'month': self.tr("Monthly")}
        self.queue = 'RANKED_SOLO_5x5'
        self.period = 'day'
        self.expanded = False
        self.puuid = None
        self.histories = {}
        self.hover = None          # ('header',) | ('queue', key) | ('period', key) | None
        self.hoverIndex = None
        self._cache = self._cacheKey = None
        self._version = 0
        self.setFixedHeight(self.expandedHeight() if self.expanded else self.HEADER_H)

    # ---------------- 数据 ----------------

    def expandedHeight(self):
        return self.PLOT_TOP + self.LABEL_ROOM + self.PLOT_H + self.X_LABEL_H + PAD

    def setPuuid(self, puuid):
        """重新读该召唤师的快照 (切换召唤师 / 刚记了一笔之后调用)"""
        self.puuid = puuid
        self.histories = {queue: rank_history.history(puuid, queue)
                          for queue in rank_history.QUEUES} if puuid else {}
        self.hoverIndex = None
        self.refresh()

    def setHistories(self, histories):
        """直接给数据 {queue: [(timestamp, tier, division, lp)]} (测试 / 预览用)"""
        self.histories = histories
        self.hoverIndex = None
        self.refresh()

    def setExpanded(self, expanded):
        self.expanded = expanded
        self.hoverIndex = None
        self.setFixedHeight(self.expandedHeight() if expanded else self.HEADER_H)
        self.refresh()

    def refresh(self):
        self._version += 1
        self._cacheKey = None
        self.update()

    def points(self):
        """当前队列 / 时间段的聚合点, 只取放得下的最近几个"""
        points = rank_history.aggregate(self.histories.get(self.queue) or [], self.period)
        n = max(int(self.plotRect().width() // self.POINT_GAP) + 1, 2)
        return points[-n:]

    # ---------------- 几何 ----------------

    def segmentRects(self):
        """[(kind, key, rect)], 右对齐在标题行里, 两组之间留 10px"""
        metrics = QFontMetrics(textFont(12, True))
        y = (self.HEADER_H - self.SEG_H) / 2 + 3
        groups = (('queue', list(self.queueLabels.items())),
                  ('period', list(self.periodLabels.items())))
        widths = [[metrics.horizontalAdvance(label) + 22 for _, label in items]
                  for _, items in groups]
        x = self.width() - PAD - 22 - sum(sum(w) + 6 for w in widths) - 10
        rects = []
        for (kind, items), ws in zip(groups, widths):
            x += 3
            for (key, _), w in zip(items, ws):
                rects.append((kind, key, QRectF(x, y, w, self.SEG_H - 6)))
                x += w
            x += 3 + 10
        return rects

    def plotRect(self):
        left = PAD + self.AXIS_W
        right = self.width() - PAD - 24
        return QRectF(left, self.PLOT_TOP + self.LABEL_ROOM, max(right - left, 10),
                      self.PLOT_H)

    def scale(self, points):
        """(纵轴刻度, x(i), y(value))"""
        plot = self.plotRect()
        values = [pt['value'] for pt in points]
        lo, hi = min(values), max(values)
        if hi - lo < 200:
            mid = (hi + lo) / 2
            lo, hi = mid - 100, mid + 100
        span = hi - lo
        lo, hi = max(lo - span * 0.08, 0), hi + span * 0.08

        step = next(s for s in (100, 200, 400, 800, 1600, 3200, 6400)
                    if (hi - lo) / s <= 4 or s == 6400)
        first = int(lo // step + 1) * step
        ticks = list(range(first, int(hi) + 1, step))

        n = len(points)
        inset = min(28, plot.width() / 4)

        def x(i):
            if n == 1:
                return plot.center().x()
            return plot.left() + inset + i / (n - 1) * (plot.width() - 2 * inset)

        def y(value):
            return plot.bottom() - (value - lo) / (hi - lo) * plot.height()

        return ticks, x, y

    def hitTest(self, pos):
        if self.expanded:
            for kind, key, rect in self.segmentRects():
                if rect.contains(pos):
                    return (kind, key)
        if pos.y() < self.HEADER_H:
            return ('header',)
        return None

    def pointIndexAt(self, pos, points):
        if not self.expanded or len(points) < 2:
            return None
        plot = self.plotRect()
        if not plot.adjusted(-20, -self.LABEL_ROOM, 20, 8).contains(pos):
            return None
        _, x, _ = self.scale(points)
        return min(range(len(points)), key=lambda i: abs(x(i) - pos.x()))

    # ---------------- 交互 ----------------

    def event(self, e):
        if e.type() == QEvent.ToolTip:
            points = self.points()
            i = self.pointIndexAt(QPointF(e.pos()), points)
            if i is not None:
                pt = points[i]
                date = datetime.datetime.fromtimestamp(pt['timestamp']).strftime('%Y/%m/%d')
                RichToolTip.showText(e.globalPos(), f"{fullTierText(pt['tier'], pt['division'])}"
                                  f"  {pt['lp']} LP\n{date}", self)
                return True
            RichToolTip.hideText()
            e.ignore()
            return True
        return super().event(e)

    def mouseMoveEvent(self, e):
        pos = e.position()
        hit = self.hitTest(pos)
        clickable = hit is not None and hit not in (('queue', self.queue),
                                                    ('period', self.period))
        self.setCursor(Qt.PointingHandCursor if clickable else Qt.ArrowCursor)
        index = self.pointIndexAt(pos, self.points())
        if hit != self.hover or index != self.hoverIndex:
            self.hover, self.hoverIndex = hit, index
            self.update()
        super().mouseMoveEvent(e)

    def leaveEvent(self, e):
        if self.hover is not None or self.hoverIndex is not None:
            self.hover = self.hoverIndex = None
            self.update()
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        e.accept()

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.LeftButton:
            return super().mouseReleaseEvent(e)

        hit = self.hitTest(e.position())
        if hit == ('header',):
            self.setExpanded(not self.expanded)
        elif hit and hit[0] == 'queue' and hit[1] != self.queue:
            self.queue = hit[1]
            self.hoverIndex = None
            self.refresh()
        elif hit and hit[0] == 'period' and hit[1] != self.period:
            self.period = hit[1]
            self.hoverIndex = None
            self.refresh()
        e.accept()

    # ---------------- 绘制 ----------------

    def paintEvent(self, e):
        p = QPainter(self)
        key = (self._version, self.queue, self.period, self.expanded, self.hover)
        p.drawPixmap(0, 0, cachedContent(self, self.__draw, key))
        if self.expanded and self.hoverIndex is not None:
            points = self.points()
            if len(points) >= 2 and self.hoverIndex < len(points):
                p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
                _, x, y = self.scale(points)
                pt = points[self.hoverIndex]
                pal = palette()
                color = LINE_COLORS[isDarkTheme()]
                center = QPointF(x(self.hoverIndex), y(pt['value']))
                p.setPen(QPen(tint(color, 90), 6))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(center, 7, 7)
                self.drawDot(p, center, color, pal)
        p.end()

    def __draw(self, p, pal):
        drawCard(p, self.rect(), pal)
        self.drawHeader(p, pal)
        if not self.expanded:
            return
        points = self.points()
        if len(points) < 2:
            self.drawEmpty(p, pal)
        else:
            self.drawPlot(p, pal, points)

    def drawHeader(self, p, pal):
        titleColor = pal['ink']
        drawText(p, QRectF(18, 0, 300, self.HEADER_H), self.tr("Tier graph"),
                 textFont(13, True), titleColor)

        # 展开 / 收起的箭头
        cx, cy = self.width() - PAD - 8, self.HEADER_H / 2
        color = pal['ink'] if self.hover == ('header',) else pal['muted']
        p.setPen(QPen(color, 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        s = 4.5
        path = QPainterPath()
        if self.expanded:
            path.moveTo(cx - s, cy + s / 2)
            path.lineTo(cx, cy - s / 2)
            path.lineTo(cx + s, cy + s / 2)
        else:
            path.moveTo(cx - s, cy - s / 2)
            path.lineTo(cx, cy + s / 2)
            path.lineTo(cx + s, cy - s / 2)
        p.drawPath(path)

        if not self.expanded:
            return

        dark = isDarkTheme()
        rects = self.segmentRects()
        for kind in ('queue', 'period'):
            group = [r for r in rects if r[0] == kind]
            track = QRectF(group[0][2].left() - 3, group[0][2].top() - 3,
                           group[-1][2].right() - group[0][2].left() + 6, self.SEG_H)
            p.setPen(Qt.NoPen)
            p.setBrush(pal['hairline'])
            p.drawRoundedRect(track, 7, 7)
            current = self.queue if kind == 'queue' else self.period
            labels = self.queueLabels if kind == 'queue' else self.periodLabels
            for _, key, rect in group:
                selected = key == current
                if selected:
                    p.setPen(Qt.NoPen if dark else QPen(tint(pal['ink'], 22), 1))
                    p.setBrush(QColor('#474E5B') if dark else pal['sheet'])
                    p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 5, 5)
                color = pal['ink'] if selected or self.hover == (kind, key) else pal['muted']
                drawText(p, rect, labels[key], textFont(12, selected), color, Qt.AlignCenter)

        p.setPen(QPen(pal['hairline'], 1))
        p.drawLine(QPointF(PAD, self.HEADER_H + 0.5),
                   QPointF(self.width() - PAD, self.HEADER_H + 0.5))

    def drawEmpty(self, p, pal):
        area = QRectF(PAD, self.HEADER_H, self.width() - 2 * PAD,
                      self.height() - self.HEADER_H)
        drawText(p, QRectF(area.left(), area.center().y() - 24, area.width(), 22),
                 self.tr("Not enough history yet"), textFont(14, True), pal['ink'],
                 Qt.AlignCenter)
        drawText(p, QRectF(area.left(), area.center().y() + 2, area.width(), 20),
                 self.tr("Seraphine records your rank each time you view it."),
                 textFont(12), pal['muted'], Qt.AlignCenter)

    def drawPlot(self, p, pal, points):
        plot = self.plotRect()
        ticks, x, y = self.scale(points)

        # 纵轴: 发丝线网格 + 左侧段位刻度
        for value in ticks:
            yy = round(y(value)) + 0.5
            if yy < plot.top() - self.LABEL_ROOM:
                continue
            p.setPen(QPen(pal['hairline'], 1, Qt.DashLine))
            p.drawLine(QPointF(plot.left(), yy), QPointF(plot.right(), yy))
            drawText(p, QRectF(PAD, yy - 9, self.AXIS_W - 10, 18), tickLabel(value),
                     numberFont(11, 500), pal['muted'], Qt.AlignRight | Qt.AlignVCenter)

        color = LINE_COLORS[isDarkTheme()]
        centers = [QPointF(x(i), y(pt['value'])) for i, pt in enumerate(points)]

        # 线下浅色填充 + 折线
        area = QPainterPath(QPointF(centers[0].x(), plot.bottom()))
        for c in centers:
            area.lineTo(c)
        area.lineTo(QPointF(centers[-1].x(), plot.bottom()))
        area.closeSubpath()
        p.setPen(Qt.NoPen)
        p.setBrush(tint(color, 34 if isDarkTheme() else 26))
        p.drawPath(area)

        line = QPainterPath(centers[0])
        for c in centers[1:]:
            line.lineTo(c)
        p.setPen(QPen(color, 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        p.drawPath(line)

        # 点 + 标注 ("D 3" / "45 LP") + 日期
        last = len(points) - 1
        for i, (pt, c) in enumerate(zip(points, centers)):
            self.drawDot(p, c, color, pal)
            box = QRectF(c.x() - self.POINT_GAP / 2, c.y() - 40, self.POINT_GAP, 18)
            drawText(p, box, pt['label'], numberFont(13, 700 if i == last else 600),
                     pal['ink'], Qt.AlignCenter)
            drawText(p, box.translated(0, 16), f"{pt['lp']} LP", numberFont(11, 500),
                     pal['muted'], Qt.AlignCenter)
            date = datetime.datetime.fromtimestamp(pt['timestamp']).strftime('%m/%d')
            drawText(p, QRectF(c.x() - 30, plot.bottom() + 6, 60, 16), date,
                     numberFont(11, 500), pal['ink'] if i == last else pal['muted'],
                     Qt.AlignCenter)

    def drawDot(self, p, center, color, pal):
        """r=4 的点 + 2px 纸面色描边"""
        p.setPen(QPen(pal['sheet'], 2))
        p.setBrush(color)
        p.drawEllipse(center, 5, 5)
