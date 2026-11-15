"""
对局详情的 "图表" 视图, 仿 OP.GG 对局的 Etc. 页: 每名英雄每分钟的经济 / 经验 / 补刀 (/ 对英雄伤害)
曲线. 数据为 app.lol.match_build.parseTimelineSeries 的结果.

- 指标切换 (分段) + 两队各 5 个英雄头像开关, 头像开关同时就是图例;
- 未选中的英雄画成淡灰的背景线 (看得出所选英雄在全场的位置); 选中的最多 3 个, 用校验过色盲
  区分度的 3 种颜色 (dataviz validate_palette, 浅色 / 深色底各一套). 颜色跟着英雄走: 选中时占一个
  空闲色位, 取消时释放, 其他人不变色; 已满 3 个时再选, 换掉最早选的 (默认选中的当前玩家最后才换);
- 线尾在右侧留白处直接标注: 英雄头像 + 数值, 挨得太近时上下推开, 用引线连回线尾;
- 悬浮: 竖线吸附到最近的一帧, 标出所选英雄在该帧的点, 读数框列出各英雄的数值 (按大小排).
底图 (坐标轴 / 线 / 标注 / 开关) 缓存成 pixmap, 悬浮层每次画在缓存之上, 鼠标移动不重画底图.
"""
import math

from PySide6.QtCore import Qt, QRectF, QPointF, QEvent
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QFontMetrics, QPixmap
from PySide6.QtWidgets import QWidget, QVBoxLayout, QSizePolicy

from app.components.rich_tooltip import RichToolTip

from app.common.qfluentwidgets import isDarkTheme
from app.components.design_tokens import numberFont, textFont, tint, palette
from app.components.match_build_widgets import MessageCard, drawCard, PAD
from app.components.scoreboard import cachedContent, drawText, drawRoundedPixmap, drawChampion

# 选中英雄的颜色 (色位 0 / 1 / 2). 全部两两组合在两种底色上都过了 CVD / 常规视觉 / 对比度检查
SERIES_COLORS = {
    False: (QColor('#C98500'), QColor('#2A78D6'), QColor('#D55181')),
    True: (QColor('#C98500'), QColor('#3987E5'), QColor('#D55181')),
}
MAX_SELECTED = len(SERIES_COLORS[False])


def compactNumber(value):
    """1234 -> 1.2K, 16052 -> 16.1K, 222 -> 222"""
    if value >= 1000:
        text = f"{value / 1000:.1f}".rstrip('0').rstrip('.')
        return f"{text}K"
    return str(int(value))


def niceTicks(maxValue, target=4):
    """0 起的整齐刻度 (步长 1 / 2 / 5 x 10^k) 与顶端值"""
    if maxValue <= 0:
        return [0, 1], 1
    raw = maxValue / target
    magnitude = 10 ** math.floor(math.log10(raw))
    step = next(m * magnitude for m in (1, 2, 5, 10) if m * magnitude >= raw)
    step = max(step, 1)
    top = math.ceil(maxValue / step) * step
    return [i * step for i in range(int(round(top / step)) + 1)], top


def clockText(minutes):
    secs = int(round(minutes * 60))
    return f"{secs // 60}:{secs % 60:02d}"


class GraphCard(QWidget):
    """指标切换 + 英雄开关 + 折线图, 一张自绘卡片"""

    SEG_Y, SEG_H = 14, 30
    LABEL_Y = 60
    TILE_Y, TILE, TILE_GAP = 82, 34, 10
    TEAM_GAP = 48
    PLOT_TOP, PLOT_H = 146, 270
    AXIS_W = 46            # 纵轴刻度文字
    GUTTER = 104           # 线尾标注
    X_LABEL_H = 22
    END_ICON = 22
    LABEL_SEP = 26         # 线尾标注之间的最小间距

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.setFixedHeight(self.PLOT_TOP + self.PLOT_H + self.X_LABEL_H + PAD)
        self.setMouseTracking(True)

        self.metricLabels = {'gold': self.tr("Gold earned"), 'xp': self.tr("Experience"),
                             'cs': self.tr("CS"), 'damage': self.tr("Damage to champions")}
        self.metricKey = 'gold'
        self.series = None
        self.teams = ([], [])         # [(participantId, QPixmap, name)] x 2
        self.teamLabels = ("", "")
        self.defaultPid = None
        self.slots = {}               # participantId -> 色位
        self.order = []               # 选中的先后
        self.hover = None             # ('metric', key) | ('player', pid) | None
        self.hoverIndex = None        # 悬浮所在的帧
        self._cache = self._cacheKey = None

    # ---------------- 数据 ----------------

    def setPlayers(self, team1, team2, labels, currentPid):
        def players(summoners):
            return [(s.get('participantId'), QPixmap(s.get('championIcon') or ''),
                     s.get('summonerName') or '') for s in summoners]

        self.teams = (players(team1), players(team2))
        self.teamLabels = labels
        self.defaultPid = currentPid
        self.slots, self.order = {}, []
        if currentPid is not None:
            self.slots[currentPid] = 0
            self.order.append(currentPid)
        self.hoverIndex = None
        self.refresh()

    def setSeries(self, series):
        self.series = series
        if series and self.metricKey not in series['metrics']:
            self.metricKey = 'gold'
        self.hoverIndex = None
        self.refresh()

    def refresh(self):
        self._cacheKey = None
        self.update()

    def availableMetrics(self):
        return list(self.series['metrics']) if self.series else ['gold', 'xp', 'cs']

    def values(self, pid):
        if not self.series:
            return None
        return (self.series['series'].get(pid) or {}).get(self.metricKey)

    def players(self):
        return [player for team in self.teams for player in team]

    def color(self, pid):
        return SERIES_COLORS[isDarkTheme()][self.slots[pid]]

    def toggle(self, pid):
        if pid in self.slots:
            del self.slots[pid]
            self.order.remove(pid)
        else:
            if len(self.slots) >= MAX_SELECTED:
                # 换掉最早选的; 当前玩家 (默认选中) 留到最后
                others = [p for p in self.order if p != self.defaultPid]
                drop = others[0] if others else self.order[0]
                del self.slots[drop]
                self.order.remove(drop)
            used = set(self.slots.values())
            self.slots[pid] = next(s for s in range(MAX_SELECTED) if s not in used)
            self.order.append(pid)
        self.refresh()

    # ---------------- 几何 ----------------

    def metricRects(self):
        metrics = QFontMetrics(textFont(13, True))
        x, y = PAD + 3, self.SEG_Y + 3
        rects = []
        for key in self.availableMetrics():
            w = metrics.horizontalAdvance(self.metricLabels[key]) + 28
            rects.append((key, QRectF(x, y, w, self.SEG_H - 6)))
            x += w
        return rects

    def teamX(self):
        groupW = 5 * self.TILE + 4 * self.TILE_GAP
        return PAD, PAD + max(groupW + self.TEAM_GAP, (self.width() - 2 * PAD) / 2)

    def tileRects(self):
        rects = []
        for x, team in zip(self.teamX(), self.teams):
            for i, (pid, _, _) in enumerate(team):
                rects.append((pid, QRectF(x + i * (self.TILE + self.TILE_GAP), self.TILE_Y,
                                          self.TILE, self.TILE)))
        return rects

    def plotRect(self):
        left = PAD + self.AXIS_W
        right = self.width() - PAD - self.GUTTER
        return QRectF(left, self.PLOT_TOP, max(right - left, 10), self.PLOT_H)

    def scale(self):
        """(纵轴刻度, 顶端值, x(minute), y(value)); 纵轴按全部 10 人取最大, 换选中不跳"""
        plot = self.plotRect()
        minutes = self.series['minutes']
        span = max(minutes[-1], 1e-6)
        peak = max((max(self.values(pid) or [0]) for pid, _, _ in self.players()), default=0)
        ticks, top = niceTicks(peak)

        def x(minute):
            return plot.left() + minute / span * plot.width()

        def y(value):
            return plot.bottom() - value / top * plot.height()

        return ticks, top, x, y

    def hitTest(self, pos):
        for key, rect in self.metricRects():
            if rect.contains(pos):
                return ('metric', key)
        for pid, rect in self.tileRects():
            if rect.adjusted(-3, -3, 3, 3).contains(pos):
                return ('player', pid)
        return None

    def frameIndexAt(self, pos):
        if not self.series:
            return None
        plot = self.plotRect()
        if not plot.adjusted(-8, -8, 8, 8).contains(pos):
            return None
        _, _, x, _ = self.scale()
        minutes = self.series['minutes']
        return min(range(len(minutes)), key=lambda i: abs(x(minutes[i]) - pos.x()))

    # ---------------- 交互 ----------------

    def event(self, e):
        if e.type() == QEvent.ToolTip:
            hit = self.hitTest(QPointF(e.pos()))
            if hit and hit[0] == 'player':
                for pid, _, name in self.players():
                    if pid == hit[1] and name:
                        RichToolTip.showText(e.globalPos(), name, self)
                        return True
            RichToolTip.hideText()
            e.ignore()
            return True
        return super().event(e)

    def mouseMoveEvent(self, e):
        pos = e.position()
        hit = self.hitTest(pos)
        self.setCursor(Qt.PointingHandCursor if hit and hit != ('metric', self.metricKey)
                       else Qt.ArrowCursor)
        index = self.frameIndexAt(pos)
        if hit != self.hover:
            self.hover = hit
            self.update()
        if index != self.hoverIndex:
            self.hoverIndex = index
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
        if hit and hit[0] == 'metric' and hit[1] != self.metricKey:
            self.metricKey = hit[1]
            self.refresh()
        elif hit and hit[0] == 'player':
            self.toggle(hit[1])
        e.accept()

    # ---------------- 绘制 ----------------

    def paintEvent(self, e):
        p = QPainter(self)
        key = (self.metricKey, tuple(sorted(self.slots.items())), self.hover, id(self.series))
        p.drawPixmap(0, 0, cachedContent(self, self.__draw, key))
        if self.series and self.hoverIndex is not None:
            p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing
                             | QPainter.SmoothPixmapTransform)
            self.drawHover(p)
        p.end()

    def __draw(self, p, pal):
        drawCard(p, self.rect(), pal)
        self.drawMetricBar(p, pal)
        self.drawTiles(p, pal)
        if self.series:
            self.drawPlot(p, pal)

    def drawMetricBar(self, p, pal):
        dark = isDarkTheme()
        rects = self.metricRects()
        if not rects:
            return
        track = QRectF(PAD, self.SEG_Y, rects[-1][1].right() + 3 - PAD, self.SEG_H)
        p.setPen(Qt.NoPen)
        p.setBrush(pal['hairline'])
        p.drawRoundedRect(track, 7, 7)
        for key, rect in rects:
            selected = key == self.metricKey
            if selected:
                p.setPen(Qt.NoPen if dark else QPen(tint(pal['ink'], 22), 1))
                p.setBrush(QColor('#474E5B') if dark else pal['sheet'])
                p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 5, 5)
            color = pal['ink'] if selected or self.hover == ('metric', key) else pal['muted']
            drawText(p, rect, self.metricLabels[key], textFont(13, selected), color,
                     Qt.AlignCenter)

    def drawTiles(self, p, pal):
        for x, label in zip(self.teamX(), self.teamLabels):
            drawText(p, QRectF(x, self.LABEL_Y, 260, 16), label, textFont(12, True),
                     pal['muted'])

        icons = {pid: pix for pid, pix, _ in self.players()}
        for pid, rect in self.tileRects():
            selected = pid in self.slots
            if selected:
                p.setPen(QPen(self.color(pid), 2.5))
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(rect.adjusted(-3.5, -3.5, 3.5, 3.5), 9, 9)
            p.setOpacity(1 if selected else (0.8 if self.hover == ('player', pid) else 0.42))
            drawRoundedPixmap(p, icons.get(pid), rect, 6)
            p.setOpacity(1)

    def drawPlot(self, p, pal):
        plot = self.plotRect()
        ticks, top, x, y = self.scale()
        minutes = self.series['minutes']

        # 纵轴: 发丝线网格 + 左侧刻度
        for value in ticks:
            yy = round(y(value)) + 0.5
            p.setPen(QPen(pal['hairline'], 1))
            p.drawLine(QPointF(plot.left(), yy), QPointF(plot.right(), yy))
            drawText(p, QRectF(PAD, yy - 9, self.AXIS_W - 10, 18), compactNumber(value),
                     numberFont(11, 500), pal['muted'], Qt.AlignRight | Qt.AlignVCenter)

        # 横轴: 整分钟刻度, 间距不小于 48px
        pxPerMin = plot.width() / max(minutes[-1], 1e-6)
        step = next((s for s in (1, 2, 5, 10) if s * pxPerMin >= 48), 10)
        for minute in range(0, int(minutes[-1]) + 1, step):
            xx = x(minute)
            drawText(p, QRectF(xx - 24, plot.bottom() + 4, 48, 16), f"{minute}m",
                     numberFont(11, 500), pal['muted'], Qt.AlignCenter)

        # 背景线 (未选中的英雄), 再画选中的 (色位 0 在最上)
        context = tint(pal['muted'], 80 if isDarkTheme() else 70)
        for pid, _, _ in self.players():
            values = self.values(pid)
            if values and pid not in self.slots:
                self.drawLine(p, values, x, y, QPen(context, 1.2, Qt.SolidLine, Qt.RoundCap,
                                                    Qt.RoundJoin))
        for pid in sorted(self.slots, key=lambda pid: -self.slots[pid]):
            values = self.values(pid)
            if values:
                self.drawLine(p, values, x, y, QPen(self.color(pid), 2, Qt.SolidLine,
                                                    Qt.RoundCap, Qt.RoundJoin))

        if not self.slots:
            drawText(p, QRectF(plot.left() + 12, plot.top() + 6, plot.width() - 24, 18),
                     self.tr("Pick champions above to compare them."), textFont(12),
                     pal['muted'])
            return

        self.drawEndLabels(p, pal, plot, x, y)

    def drawLine(self, p, values, x, y, pen):
        minutes = self.series['minutes']
        path = QPainterPath(QPointF(x(minutes[0]), y(values[0])))
        for minute, value in zip(minutes[1:], values[1:]):
            path.lineTo(QPointF(x(minute), y(value)))
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)

    def endLabelCenters(self, plot, ends):
        """[(pid, 线尾 y)] -> {pid: 标注中心 y}: 按 y 排开, 间距不小于 LABEL_SEP, 不出图"""
        ends = sorted(ends, key=lambda item: item[1])
        lo, hi = plot.top() + self.END_ICON / 2, plot.bottom() - self.END_ICON / 2
        centers = []
        for _, yy in ends:
            c = min(max(yy, lo), hi)
            if centers:
                c = max(c, centers[-1] + self.LABEL_SEP)
            centers.append(c)
        for i in range(len(centers) - 1, -1, -1):
            limit = hi if i == len(centers) - 1 else centers[i + 1] - self.LABEL_SEP
            centers[i] = min(centers[i], limit)
        return {pid: c for (pid, _), c in zip(ends, centers)}

    def drawEndLabels(self, p, pal, plot, x, y):
        minutes = self.series['minutes']
        icons = {pid: pix for pid, pix, _ in self.players()}
        ends = [(pid, y(self.values(pid)[-1])) for pid in self.slots if self.values(pid)]
        centers = self.endLabelCenters(plot, ends)
        endX = x(minutes[-1])
        labelX = plot.right() + 16

        for pid, yy in ends:
            color, c = self.color(pid), centers[pid]
            # 引线: 线尾 -> 标注
            p.setPen(QPen(color, 1))
            p.drawLine(QPointF(endX + 5, yy), QPointF(labelX - 3, c))
            icon = QRectF(labelX, c - self.END_ICON / 2, self.END_ICON, self.END_ICON)
            drawChampion(p, icons.get(pid), icon, color, 2, crop=3)
            drawText(p, QRectF(icon.right() + 6, c - 9, self.GUTTER - self.END_ICON - 22, 18),
                     compactNumber(self.values(pid)[-1]), numberFont(12, 600), pal['ink'])
            self.drawDot(p, QPointF(endX, yy), color, pal)

    def drawDot(self, p, center, color, pal):
        """r=4 的点 + 2px 纸面色描边 (压在线上也看得清)"""
        p.setPen(QPen(pal['sheet'], 2))
        p.setBrush(color)
        p.drawEllipse(center, 5, 5)

    def drawHover(self, p):
        pal = palette()
        plot = self.plotRect()
        _, _, x, y = self.scale()
        i = self.hoverIndex
        minute = self.series['minutes'][i]
        cx = x(minute)

        p.setPen(QPen(tint(pal['muted'], 150), 1))
        p.drawLine(QPointF(cx, plot.top()), QPointF(cx, plot.bottom()))

        rows = []
        for pid in self.slots:
            values = self.values(pid)
            if values:
                self.drawDot(p, QPointF(cx, y(values[i])), self.color(pid), pal)
                rows.append((values[i], pid))
        if not rows:
            return
        rows.sort(key=lambda row: -row[0])

        # 读数框: 时间 + 每名英雄一行 (颜色短线 + 数值 + 名字); 数值在前, 名字次要
        names = {pid: name for pid, _, name in self.players()}
        valueFont, nameFont, headFont = numberFont(13, 600), textFont(12), textFont(12, True)
        vm, nm = QFontMetrics(valueFont), QFontMetrics(nameFont)
        valueW = max(vm.horizontalAdvance(f"{value:,}") for value, _ in rows)
        nameTexts = {pid: nm.elidedText(names.get(pid, ''), Qt.ElideRight, 130)
                     for _, pid in rows}
        nameW = max(nm.horizontalAdvance(text) for text in nameTexts.values())
        rowH, headH = 20, 24
        w = 12 + 14 + 8 + valueW + 10 + nameW + 12
        h = headH + len(rows) * rowH + 8
        bx = cx + 14 if cx + 14 + w <= self.width() - PAD else cx - 14 - w
        box = QRectF(bx, plot.top() + 4, w, h)

        p.setPen(QPen(pal['hairline'], 1))
        p.setBrush(QColor('#2B3039') if isDarkTheme() else pal['sheet'])
        p.drawRoundedRect(box, 6, 6)
        drawText(p, QRectF(box.left() + 12, box.top() + 4, w - 24, 20), clockText(minute),
                 headFont, pal['muted'])
        for n, (value, pid) in enumerate(rows):
            ry = box.top() + headH + n * rowH
            p.setPen(QPen(self.color(pid), 2.5, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(box.left() + 13, ry + rowH / 2),
                       QPointF(box.left() + 25, ry + rowH / 2))
            vx = box.left() + 12 + 14 + 8
            drawText(p, QRectF(vx, ry, valueW, rowH), f"{value:,}", valueFont, pal['ink'],
                     Qt.AlignRight | Qt.AlignVCenter)
            drawText(p, QRectF(vx + valueW + 10, ry, nameW + 4, rowH), nameTexts[pid], nameFont,
                     pal['muted'])


class MatchGraphView(QWidget):
    """图表视图: 提示卡 (加载中 / 没有数据) 或图表卡. 数据为 loadGameBuildData 的返回值"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.vBoxLayout = QVBoxLayout(self)
        self.vBoxLayout.setContentsMargins(0, 0, 0, 0)
        self.vBoxLayout.setSpacing(12)
        self.messageCard = MessageCard()
        self.graphCard = GraphCard()
        for card in (self.messageCard, self.graphCard):
            self.vBoxLayout.addWidget(card)
            card.setVisible(False)

    def setPlayers(self, team1, team2, labels, currentPid):
        self.graphCard.setPlayers(team1, team2, labels, currentPid)

    def setLoading(self):
        self.graphCard.setVisible(False)
        self.messageCard.setMessage(self.tr("Loading graphs..."), quiet=True)
        self.messageCard.setVisible(True)

    def setData(self, data):
        series = (data or {}).get('series')
        self.graphCard.setSeries(series)
        if series:
            self.messageCard.setVisible(False)
        else:
            self.messageCard.setMessage(self.tr("Graphs aren't available for this game."),
                                        self.tr("The match timeline has no per-minute data."))
            self.messageCard.setVisible(True)
        self.graphCard.setVisible(bool(series))
