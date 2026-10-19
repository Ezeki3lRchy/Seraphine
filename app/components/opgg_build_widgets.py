"""
OP.GG 英雄详情页 (出装 / 符文 / 技能 / 克制 / 趋势) 的自绘控件.

每张卡片整块自绘并缓存成品图 (主题 / 尺寸 / 缩放比 / 胜负色 / 主题色不变就不重画),
滚动时只贴一张图; 悬浮提示 / 点击靠命中测试. 颜色与字体见 design_tokens.py.
"""
from PySide6.QtCore import Qt, QRectF, QPointF, QEvent, Signal, QCoreApplication
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QFontMetrics, QPixmap, QIcon
from PySide6.QtWidgets import QFrame, QSizePolicy, QToolTip

from app.common.config import qconfig
from app.common.signals import signalBus
from app.common.qfluentwidgets import isDarkTheme, themeColor
from app.components.design_tokens import palette, numberFont, textFont, resultColor, tint
from app.components.match_widgets import itemTooltipHtml

PAD = 16
RADIUS = 8
TITLE_H = 46
ROW_H = 36
ICON = 28

# 表格右侧三列: 选择率 / 胜率 / 对局数
STAT_W = (62, 62, 80)
STAT_GAP = 4

TIER_COLORS = {0: QColor(232, 64, 87), 1: QColor(0, 147, 255), 2: QColor(0, 187, 163),
               3: QColor(255, 185, 0), 4: QColor(154, 164, 175), 5: QColor(168, 138, 103)}

SKILL_COLORS = {'Q': QColor('#1E88E5'), 'W': QColor('#0E9F84'), 'E': QColor('#E07B0C'),
                'R': QColor('#7E57C2')}

POSITION_ICONS = {'TOP': 'top', 'JUNGLE': 'jng', 'MID': 'mid', 'ADC': 'bot', 'SUPPORT': 'sup'}


def trText(context, source, english=None):
    """复用旧控件已有的翻译 (如中文); 没有翻译时显示句首大写的英文"""
    text = QCoreApplication.translate(context, source)
    return text if text != source else (english or source)


def winRateText():
    return trText("ChampionTitleBar", "Win Rate", "Win rate")


def pickRateText():
    return trText("ChampionTitleBar", "Pick Rate", "Pick rate")


def gamesText(n):
    """'1,234 games' (中文为 '1,234 局游戏')"""
    return f"{n:,} " + trText("ItemsWidget", "Games", "games")


def pct(value, digits=1):
    return f"{value * 100:.{digits}f}%" if value is not None else "-"


def rate(win, play):
    return win / play if play else 0


# ---------------- 图片缓存 ----------------

_pixmaps = {}


def loadPixmap(path):
    if not path:
        return QPixmap()

    pixmap = _pixmaps.get(path)
    if pixmap is None:
        pixmap = QPixmap(path)
        _pixmaps[path] = pixmap

    return pixmap


def svgPixmap(path, size, dpr):
    key = (path, size, dpr)
    pixmap = _pixmaps.get(key)
    if pixmap is None:
        pixmap = QIcon(path).pixmap(round(size * dpr), round(size * dpr))
        pixmap.setDevicePixelRatio(dpr)
        _pixmaps[key] = pixmap

    return pixmap


# ---------------- 绘制小工具 ----------------

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


def elide(string, font, width):
    return QFontMetrics(font).elidedText(string, Qt.ElideRight, int(width))


def drawRoundedPixmap(p, pixmap, rect, radius):
    if pixmap is None or pixmap.isNull():
        return
    p.save()
    clip = QPainterPath()
    clip.addRoundedRect(rect, radius, radius)
    p.setClipPath(clip)
    p.drawPixmap(rect, pixmap, QRectF(pixmap.rect()))
    p.restore()


def drawCircle(p, pixmap, rect, ring=None, ringWidth=1.0, crop=0):
    """圆形头像 (可裁掉原图边框) + 可选描边"""
    if pixmap is not None and not pixmap.isNull():
        src = QRectF(pixmap.rect()).adjusted(crop, crop, -crop, -crop)
        p.save()
        clip = QPainterPath()
        clip.addEllipse(rect)
        p.setClipPath(clip)
        p.drawPixmap(rect, pixmap, src)
        p.restore()
    if ring is not None:
        p.setPen(QPen(ring, ringWidth))
        p.setBrush(Qt.NoBrush)
        half = ringWidth / 2
        p.drawEllipse(rect.adjusted(half, half, -half, -half))


def drawChevron(p, cx, cy, color, size=4.0):
    p.setPen(QPen(color, 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.setBrush(Qt.NoBrush)
    path = QPainterPath()
    path.moveTo(cx - size / 2, cy - size)
    path.lineTo(cx + size / 2, cy)
    path.lineTo(cx - size / 2, cy + size)
    p.drawPath(path)


def statColumns(right, widths=STAT_W, gap=STAT_GAP):
    """右对齐的三列 (选择率, 胜率, 对局数), 返回各列的 (x, w)"""
    cols = []
    x = right
    for w in reversed(widths):
        x -= w
        cols.append((x, w))
        x -= gap
    return list(reversed(cols))


def drawStatHeaders(p, pal, right, y, h, labels, widths=STAT_W):
    for (x, w), label in zip(statColumns(right, widths), labels):
        drawText(p, QRectF(x, y, w, h), label, textFont(12), pal['muted'],
                 Qt.AlignRight | Qt.AlignVCenter)


def drawStats(p, pal, right, y, h, pickRate, win, play, widths=STAT_W):
    """一行的选择率 (粗) / 胜率 / 对局数"""
    (px, pw), (wx, ww), (gx, gw) = statColumns(right, widths)
    align = Qt.AlignRight | Qt.AlignVCenter
    drawText(p, QRectF(px, y, pw, h), pct(pickRate), numberFont(14), pal['ink'], align)
    drawText(p, QRectF(wx, y, ww, h), pct(rate(win, play)), numberFont(14, 400), pal['ink'], align)
    drawText(p, QRectF(gx, y, gw, h), f"{play:,}", numberFont(13, 400), pal['muted'], align)


def drawItemGroup(card, p, pal, x, cy, icons, chain=False, size=ICON):
    """
    一组物品图标, 返回结束的 x. 相邻的同一物品 (如两瓶药水) 合并成一个图标加数量角标;
    chain=True 时图标之间画箭头 (核心三件套的出装顺序)
    """
    groups = []
    for icon in icons:
        if groups and not chain and groups[-1][0]['itemId'] == icon['itemId'] \
                and icon['itemId']:
            groups[-1][1] += 1
        else:
            groups.append([icon, 1])

    for i, (icon, count) in enumerate(groups):
        rect = QRectF(x, cy - size / 2, size, size)
        drawRoundedPixmap(p, loadPixmap(icon['icon']), rect, 5)

        if count > 1:
            badge = QRectF(rect.right() - 13, rect.bottom() - 13, 16, 16)
            p.setPen(QPen(pal['sheet'], 1.5))
            p.setBrush(QColor(20, 24, 32, 230))
            p.drawRoundedRect(badge, 4, 4)
            drawText(p, badge, str(count), numberFont(12), QColor(255, 255, 255), Qt.AlignCenter)

        if icon.get('itemId'):
            card.addTip(rect, lambda id=icon['itemId']: itemTooltipHtml(id))
        elif icon.get('tip'):
            card.addTip(rect, icon['tip'])

        x += size
        if i != len(groups) - 1:
            if chain:
                drawChevron(p, x + 10, cy, pal['muted'])
                x += 20
            else:
                x += 4

    return x


def drawSkillChip(p, rect, skill, radius=6, font=None):
    color = SKILL_COLORS.get(skill, QColor(120, 120, 120))
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    p.drawRoundedRect(rect, radius, radius)
    drawText(p, rect, skill, font or textFont(13, True), QColor(255, 255, 255), Qt.AlignCenter)


def selectionColors():
    color = themeColor()
    return tint(color, 30), tint(color, 170)


def drawSelection(p, rect):
    fill, border = selectionColors()
    p.setPen(QPen(border, 1))
    p.setBrush(fill)
    p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 6, 6)


# ---------------- 卡片基类 ----------------

class PaintedCard(QFrame):
    """
    自绘卡片: 子类实现 `drawContent(p, pal, dark)`, 绘制时用 `addTip` / `addLink` 登记命中区域.

    卡片底 (纸面 + 发丝线) 与内容一起画进缓存图, 每帧只贴图 + 画悬停高亮
    """

    def __init__(self, parent=None, card=True):
        super().__init__(parent)
        self.card = card
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WA_OpaquePaintEvent, False)

        self._cache = None
        self._cacheKey = None
        self._tips = []
        self._links = []
        self._hover = None

        qconfig.themeChanged.connect(self.refresh)
        # 设置里改了胜负色时也立刻重画 (design_tokens 先清掉颜色缓存)
        signalBus.customColorChanged.connect(self.__onColorChanged)

    def __onColorChanged(self, _):
        self.refresh()

    def refresh(self):
        """数据 / 选中项变化后调用, 丢弃缓存重画"""
        self._cacheKey = None
        self._hover = None
        self.update()

    def addTip(self, rect, tip):
        """tip: 文本, 或返回文本的函数 (悬停时才计算)"""
        self._tips.append((QRectF(rect), tip))

    def addLink(self, rect, callback):
        self._links.append((QRectF(rect), callback))

    def drawContent(self, p: QPainter, pal: dict, dark: bool):
        raise NotImplementedError

    def drawTitle(self, p, pal, title, caption=""):
        drawText(p, QRectF(PAD, 12, self.width() - 2 * PAD, 22), title, textFont(14, True),
                 pal['ink'])
        if caption:
            drawText(p, QRectF(PAD, 12, self.width() - 2 * PAD, 22), caption, textFont(12),
                     pal['muted'], Qt.AlignRight | Qt.AlignVCenter)

    # ---------------- 交互 ----------------

    def __linkAt(self, pos):
        for rect, callback in self._links:
            if rect.contains(pos):
                return rect, callback
        return None, None

    def event(self, e):
        if e.type() == QEvent.ToolTip:
            pos = QPointF(e.pos())
            for rect, tip in self._tips:
                if rect.contains(pos):
                    text = tip() if callable(tip) else tip
                    if text:
                        QToolTip.showText(e.globalPos(), text, self, rect.toRect())
                        return True
            QToolTip.hideText()
            e.ignore()
            return True

        return super().event(e)

    def mouseMoveEvent(self, e):
        rect, _ = self.__linkAt(e.position())
        if rect != self._hover:
            self._hover = rect
            self.setCursor(Qt.PointingHandCursor if rect is not None else Qt.ArrowCursor)
            self.update()
        super().mouseMoveEvent(e)

    def leaveEvent(self, e):
        if self._hover is not None:
            self._hover = None
            self.setCursor(Qt.ArrowCursor)
            self.update()
        super().leaveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            rect, callback = self.__linkAt(e.position())
            if callback is not None:
                callback()
                e.accept()
                return
        super().mouseReleaseEvent(e)

    # ---------------- 绘制 ----------------

    def paintEvent(self, e):
        dark = isDarkTheme()
        pal = palette(dark)

        p = QPainter(self)
        p.drawPixmap(0, 0, self.__content(dark, pal))

        # 悬停高亮: 半透明墨色盖在缓存图上, 不必重画内容
        if self._hover is not None:
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(Qt.NoPen)
            p.setBrush(tint(pal['ink'], 14 if dark else 9))
            p.drawRoundedRect(self._hover, 6, 6)

        p.end()

    def __content(self, dark, pal):
        dpr = self.devicePixelRatioF()
        key = (self.width(), self.height(), dpr, dark, resultColor('win').rgba(),
               resultColor('lose').rgba(), themeColor().rgba())
        if key == self._cacheKey and self._cache is not None:
            return self._cache

        out = QPixmap(round(self.width() * dpr), round(self.height() * dpr))
        out.setDevicePixelRatio(dpr)
        out.fill(Qt.transparent)

        p = QPainter(out)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform
                         | QPainter.TextAntialiasing)
        if self.card:
            p.setPen(QPen(pal['hairline'], 1))
            p.setBrush(pal['sheet'])
            p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5),
                              RADIUS, RADIUS)

        self._tips, self._links = [], []
        self.drawContent(p, pal, dark)
        p.end()

        self._cache, self._cacheKey = out, key
        return out


# ---------------- 英雄概览 ----------------

class ChampionHeaderCard(PaintedCard):
    """
    英雄概览:

        ▌(头像)  Yunara                                   [T2]
        ▌        (分路) Bottom · Tier 2 · Rank 11 of 40
        ▌───────────────────────────────────────────────────
        ▌Win rate   Pick rate   Ban rate   Games     KDA
        ▌49.36%     9.82%       6.90%      214,513   2.08
    """

    HEIGHT = 152

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(self.HEIGHT)
        self.data = None
        self.positionText = ""
        self.stats = []
        self.setVisible(False)

    def setData(self, summary: dict, positionText: str = ""):
        self.data = summary
        self.positionText = positionText

        stats = []
        if summary.get('firstRate') is not None:
            # 斗魂竞技场
            stats.append((trText("ChampionTitleBar", "First Rate", "First place"),
                          pct(summary['firstRate'], 2)))
            stats.append((trText("ChampionTitleBar", "Average Place", "Average place"),
                          f"{summary['averagePlace']:.2f}"))
        elif summary.get('winRate') is not None:
            stats.append((winRateText(), pct(summary['winRate'], 2)))

        if summary.get('pickRate') is not None:
            stats.append((pickRateText(), pct(summary['pickRate'], 2)))

        if summary.get('banRate'):
            stats.append((trText("ChampionTitleBar", "Ban Rate", "Ban rate"),
                          pct(summary['banRate'], 2)))

        if summary.get('play'):
            stats.append((self.tr("Games"), f"{summary['play']:,}"))

        if summary.get('kda'):
            stats.append(("KDA", f"{summary['kda']:.2f}"))

        self.stats = stats
        # 没有任何数据 (如极限闪击没有统计) 时只留头像和名字
        self.setFixedHeight(self.HEIGHT if stats else 88)
        self.refresh()
        self.setVisible(True)

    def drawContent(self, p, pal, dark):
        data = self.data
        if not data:
            return

        w, h = self.width(), self.height()
        tier = data.get('tier')
        accent = TIER_COLORS.get(tier, pal['muted'])

        # 左侧梯队色边条
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), RADIUS, RADIUS)
        p.save()
        p.setClipPath(clip)
        p.fillRect(QRectF(0, 0, 4, h), accent)
        p.restore()

        x = PAD + 4
        drawCircle(p, loadPixmap(data.get('icon')), QRectF(x, 16, 56, 56), pal['gold'], 2,
                   crop=4)
        x += 56 + 14

        # 分路 · 梯队 · 排名
        sub = []
        icon = POSITION_ICONS.get(data.get('position')) if self.positionText else None
        if self.positionText:
            sub.append(self.positionText)
        if tier is not None:
            sub.append("OP" if tier == 0 else self.tr("Tier %1").replace("%1", str(tier)))
        if data.get('rank') and data.get('rankTotal'):
            sub.append(self.tr("Rank %1 of %2").replace("%1", str(data['rank']))
                       .replace("%2", str(data['rankTotal'])))

        # 没有副标题时名字与头像垂直居中
        nameFont = textFont(20, True)
        drawText(p, QRectF(x, 16 if sub else 29, w - x - 70, 30),
                 elide(data.get('name') or "", nameFont, w - x - 70), nameFont, pal['ink'])

        if sub:
            subX = x
            if icon:
                p.drawPixmap(QRectF(x, 50, 16, 16).toRect(),
                             svgPixmap(f"app/resource/images/icon-position-{icon}.svg", 16,
                                       self.devicePixelRatioF()))
                subX += 20
            drawText(p, QRectF(subX, 48, w - subX - PAD, 20), "  ·  ".join(sub), textFont(13),
                     pal['muted'])

        # 梯队徽章
        if tier is not None:
            p.drawPixmap(QRectF(w - PAD - 34, 22, 34, 34).toRect(),
                         svgPixmap(f"app/resource/images/icon-tier-{tier}.svg", 34,
                                   self.devicePixelRatioF()))

        # 数据
        if self.stats:
            p.setPen(QPen(pal['hairline'], 1))
            p.drawLine(QPointF(PAD + 4, 88.5), QPointF(w - PAD, 88.5))
            colW = (w - PAD - 4 - PAD) / len(self.stats)
            for i, (label, value) in enumerate(self.stats):
                cx = PAD + 4 + i * colW
                drawText(p, QRectF(cx, 98, colW, 18), label, textFont(12), pal['muted'])
                drawText(p, QRectF(cx, 116, colW, 26), value, numberFont(20), pal['ink'])


# ---------------- 对线克制 ----------------

class CountersCard(PaintedCard):
    """
    对线克制 (左: 克制, 右: 被克制), 只展示对局数足够的英雄; 点击英雄查看它的出装

        Counters                          Lane opponents with 1,626+ games
        Strong against                 │  Weak against
        (头) Kalista      53.4%  3,477  │  (头) Twitch      46.3%  4,797
    """

    championClicked = Signal(int)

    ROWS = 5
    ROW = 30

    def __init__(self, parent=None):
        super().__init__(parent)
        self.strong = []
        self.weak = []
        self.minGames = 0
        self.setVisible(False)

    def setData(self, counters: dict):
        counters = counters or {}
        minGames = counters.get('minGames') or 0
        self.minGames = minGames
        self.strong = [c for c in counters.get('strongAgainst') or []
                       if c['play'] >= minGames][:self.ROWS]
        self.weak = [c for c in counters.get('weakAgainst') or []
                     if c['play'] >= minGames][:self.ROWS]

        if not self.strong and not self.weak:
            self.setVisible(False)
            return

        rows = max(len(self.strong), len(self.weak))
        self.setFixedHeight(TITLE_H + 24 + rows * self.ROW + 10)
        self.refresh()
        self.setVisible(True)

    def drawContent(self, p, pal, dark):
        w, h = self.width(), self.height()
        self.drawTitle(p, pal, self.tr("Counters"),
                       self.tr("Lane opponents with %1+ games").replace(
                           "%1", f"{self.minGames:,}"))

        gutter = 24
        colW = (w - 2 * PAD - gutter) / 2
        p.setPen(QPen(pal['hairline'], 1))
        midX = PAD + colW + gutter / 2
        p.drawLine(QPointF(midX, TITLE_H), QPointF(midX, h - 12))

        for column, (title, color, entries) in enumerate((
                (self.tr("Strong against"), resultColor('win'), self.strong),
                (self.tr("Weak against"), resultColor('lose'), self.weak))):
            x = PAD + column * (colW + gutter)
            gamesW, rateW = 64, 56
            drawText(p, QRectF(x, TITLE_H - 4, colW, 20), title, textFont(12, True), color)
            if entries:
                drawStatHeaders(p, pal, x + colW, TITLE_H - 4, 20,
                                (self.tr("Win"), self.tr("Games")), (rateW, gamesW))

            if not entries:
                drawText(p, QRectF(x, TITLE_H + 20, colW, self.ROW), self.tr("Not enough games"),
                         textFont(12), pal['muted'])
                continue

            for row, c in enumerate(entries):
                y = TITLE_H + 20 + row * self.ROW
                drawCircle(p, loadPixmap(c['icon']), QRectF(x, y + 3, 24, 24), pal['hairline'],
                           1, crop=3)

                nameW = colW - 32 - rateW - gamesW - STAT_GAP - 8
                font = textFont(13)
                drawText(p, QRectF(x + 32, y, nameW, self.ROW),
                         elide(c.get('name') or "", font, nameW), font, pal['ink'])
                drawText(p, QRectF(x + colW - gamesW - STAT_GAP - rateW, y, rateW, self.ROW),
                         pct(c['winRate']), numberFont(14), color,
                         Qt.AlignRight | Qt.AlignVCenter)
                drawText(p, QRectF(x + colW - gamesW, y, gamesW, self.ROW), f"{c['play']:,}",
                         numberFont(13, 400), pal['muted'], Qt.AlignRight | Qt.AlignVCenter)

                rect = QRectF(x - 6, y + 1, colW + 12, self.ROW - 2)
                self.addLink(rect, lambda id=c['championId']: self.championClicked.emit(id))
                self.addTip(rect, f"{c.get('name') or ''}: {pct(c['winRate'], 2)}, "
                                  f"{gamesText(c['play'])}")


# ---------------- 选项表格 (召唤师技能 / 出门装 / 鞋子 / 核心出装) ----------------

class OptionTableCard(PaintedCard):
    """
    一组可选方案, 每行: 图标组 + 选择率 / 胜率 / 对局数

        Core builds                        Pick rate   Win rate     Games
        [物品] › [物品] › [物品]                28.4%      55.0%    42,642
    """

    def __init__(self, title="", parent=None):
        super().__init__(parent)
        self.title = title
        self.rows = []
        self.setVisible(False)

    def setData(self, rows: list, title=None):
        """rows: [{'icons': [{'icon', 'itemId'?, 'tip'?}], 'chain', 'pickRate', 'win', 'play'}]"""
        if title is not None:
            self.title = title

        self.rows = rows or []
        if not self.rows:
            self.setVisible(False)
            return

        self.setFixedHeight(TITLE_H - 6 + len(self.rows) * ROW_H + 8)
        self.refresh()
        self.setVisible(True)

    def drawContent(self, p, pal, dark):
        w = self.width()
        right = w - PAD
        self.drawTitle(p, pal, self.title)
        drawStatHeaders(p, pal, right, 12, 22, (pickRateText(), winRateText(), self.tr("Games")))

        for i, row in enumerate(self.rows):
            y = TITLE_H - 6 + i * ROW_H
            if i:
                p.setPen(QPen(pal['hairline'], 1))
                p.drawLine(QPointF(PAD, y + 0.5), QPointF(right, y + 0.5))

            drawItemGroup(self, p, pal, PAD, y + ROW_H / 2, row['icons'], row.get('chain', False))
            drawStats(p, pal, right, y, ROW_H, row['pickRate'], row['win'], row['play'])


# ---------------- 符文页列表 ----------------

class RunePageTable(PaintedCard):
    """
    符文页候选 (嵌在符文卡片里, 不画卡片底), 点击切换下方展示的符文树:

                                                     Pick rate   Win rate     Games
        (基石) (主系 3 个)  (副系 2 个)  Lethal Tempo     29.8%      47.7%    60,390
    """

    pageSelected = Signal(int)

    HEADER = 20
    GAP = 4

    def __init__(self, parent=None):
        super().__init__(parent, card=False)
        self.pages = []
        self.selected = 0

    def setData(self, pages: list):
        """
        pages: [{'keystoneIcon', 'keystoneTip', 'minorIcons': [{'icon', 'tip'}],
                 'secondaryIcons': [{'icon', 'tip'}], 'title', 'pickRate', 'win', 'play'}]
        """
        self.pages = pages or []
        self.selected = 0
        self.setFixedHeight(self.HEADER + len(self.pages) * (ROW_H + self.GAP))
        self.refresh()

    def select(self, index):
        if index == self.selected:
            return
        self.selected = index
        self.refresh()
        self.pageSelected.emit(index)

    def drawContent(self, p, pal, dark):
        w = self.width()
        right = w - PAD
        drawStatHeaders(p, pal, right, 0, self.HEADER, (pickRateText(), winRateText(),
                                                       self.tr("Games")))
        statsX = statColumns(right)[0][0]

        for i, page in enumerate(self.pages):
            y = self.HEADER + i * (ROW_H + self.GAP)
            rect = QRectF(PAD - 6, y, w - 2 * PAD + 12, ROW_H)
            if i == self.selected and len(self.pages) > 1:
                drawSelection(p, rect)

            x = PAD
            cy = y + ROW_H / 2
            keystone = QRectF(x, cy - 15, 30, 30)
            drawCircle(p, loadPixmap(page.get('keystoneIcon')), keystone)
            if page.get('keystoneTip'):
                self.addTip(keystone, page['keystoneTip'])
            x += 36

            for group in ('minorIcons', 'secondaryIcons'):
                for icon in page.get(group) or []:
                    r = QRectF(x, cy - 10, 20, 20)
                    drawCircle(p, loadPixmap(icon['icon']), r)
                    if icon.get('tip'):
                        self.addTip(r, icon['tip'])
                    x += 22
                x += 8

            nameFont = textFont(13, True)
            title = page.get('title') or ""
            if title and x + 40 < statsX:
                drawText(p, QRectF(x + 2, y, statsX - x - 10, ROW_H),
                         elide(title, nameFont, statsX - x - 10), nameFont, pal['ink'])

            drawStats(p, pal, right, y, ROW_H, page['pickRate'], page['win'], page['play'])

            if len(self.pages) > 1:
                self.addLink(rect, lambda index=i: self.select(index))


# ---------------- 符文树 ----------------

def runeTooltipHtml(rune):
    name = rune.get('name') or ""
    desc = rune.get('desc') or ""
    body = f"<br>{desc}" if desc else ""
    return (f"<table width=300><tr><td style='font-size:12px'>"
            f"<b style='font-size:14px'>{name}</b>{body}</td></tr></table>")


class RuneTreeView(PaintedCard):
    """
    选中符文页的完整符文树 (嵌在符文卡片里): 主系 (基石 + 3 行) / 副系 3 行 / 属性碎片 3 行,
    选中的符文实色, 其余淡化; 悬停显示符文说明

          (主系) Precision      (副系) Inspiration       Shards
          ◯  ◉  ◯  ◯
          ◯  ◉  ◯               ◯  ◉  ◯                  ◯ ◉ ◯
          ...
    """

    HEADER = 26
    KEYSTONE = 38
    RUNE = 28
    SHARD = 22
    ROW = 34
    FADED = 0.22

    def __init__(self, parent=None):
        super().__init__(parent, card=False)
        self.primary = None
        self.secondary = None
        self.selected = set()
        self.shards = []
        self.setFixedHeight(self.HEADER + 8 + self.KEYSTONE + 6 + 3 * self.ROW)

    def setData(self, primary: dict, secondary: dict, perks: list):
        """primary / secondary: connector.manager.getPerkStyles() 中的系别; perks: 9 个符文 id"""
        self.primary = primary
        self.secondary = secondary
        self.selected = set(perks[:-3])
        self.shards = list(perks[-3:])
        self.refresh()

    def __drawRune(self, p, rune, rect, selected, background=None):
        gold = palette()['gold']
        p.setOpacity(1 if selected else self.FADED)
        p.setPen(Qt.NoPen)
        if background is not None:
            p.setBrush(background)
            p.drawEllipse(rect)
        elif selected and rect.width() >= self.KEYSTONE:
            # 基石图标是浅色的, 垫一层金色浅底才和未选中的区分得开
            p.setBrush(tint(gold, 50))
            p.drawEllipse(rect)
        drawCircle(p, loadPixmap(rune.get('icon')), rect)
        p.setOpacity(1)
        if selected:
            p.setPen(QPen(gold, 1.5))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(rect.adjusted(0.75, 0.75, -0.75, -0.75))
        self.addTip(rect, runeTooltipHtml(rune))

    def __drawRow(self, p, runes, cx, cy, size, gap, selected, background=None):
        total = len(runes) * size + (len(runes) - 1) * gap
        x = cx - total / 2
        for rune in runes:
            self.__drawRune(p, rune, QRectF(x, cy - size / 2, size, size), selected(rune),
                            background)
            x += size + gap

    def __drawStyleHeader(self, p, pal, style, cx, title=None):
        font = textFont(12, True)
        text = title if title is not None else style.get('name') or ""
        textW = QFontMetrics(font).horizontalAdvance(text)
        iconW = 0 if style is None else 22
        x = cx - (iconW + (6 if iconW else 0) + textW) / 2
        if style is not None:
            icon = QRectF(x, (self.HEADER - 22) / 2, 22, 22)
            drawCircle(p, loadPixmap(style.get('icon')), icon)
            x += 28
        drawText(p, QRectF(x, 0, textW + 2, self.HEADER), text, font, pal['muted'])

    def drawContent(self, p, pal, dark):
        if not self.primary or not self.secondary:
            return

        w = self.width()
        primaryW, secondaryW, shardW, gap = 4 * self.KEYSTONE + 3 * 6, 3 * self.RUNE + 2 * 12, \
            3 * self.SHARD + 2 * 8, 44
        left = (w - (primaryW + secondaryW + shardW + 2 * gap)) / 2
        cxPrimary = left + primaryW / 2
        cxSecondary = left + primaryW + gap + secondaryW / 2
        cxShards = left + primaryW + secondaryW + 2 * gap + shardW / 2

        self.__drawStyleHeader(p, pal, self.primary, cxPrimary)
        self.__drawStyleHeader(p, pal, self.secondary, cxSecondary)
        self.__drawStyleHeader(p, pal, None, cxShards, self.tr("Shards"))

        def isSelected(rune):
            return rune.get('runeId') in self.selected

        top = self.HEADER + 8
        slots = self.primary.get('slots') or []
        if slots:
            self.__drawRow(p, slots[0], cxPrimary, top + self.KEYSTONE / 2, self.KEYSTONE, 6,
                           isSelected)

        rowsTop = top + self.KEYSTONE + 6
        for i, slot in enumerate(slots[1:4]):
            self.__drawRow(p, slot, cxPrimary, rowsTop + i * self.ROW + self.ROW / 2, self.RUNE,
                           12, isSelected)

        for i, slot in enumerate((self.secondary.get('slots') or [])[1:4]):
            self.__drawRow(p, slot, cxSecondary, rowsTop + i * self.ROW + self.ROW / 2,
                           self.RUNE, 12, isSelected)

        shardBg = QColor(30, 35, 40)
        for i, slot in enumerate(slots[4:7]):
            chosen = self.shards[i] if i < len(self.shards) else None
            self.__drawRow(p, slot, cxShards, rowsTop + i * self.ROW + self.ROW / 2, self.SHARD, 8,
                           lambda rune, c=chosen: rune.get('runeId') == c, shardBg)


# ---------------- 技能加点 ----------------

class SkillsCard(PaintedCard):
    """
    技能: 主升顺序 (可点选) + 对应的最常见 15 级加点表

        Skill order                         Pick rate   Win rate     Games
        [Q] › [E] › [W]                         51.8%      58.9%    52,297
        [Q] › [W] › [E]                         48.0%      61.6%    48,463
              1  2  3  4  5  6  7  8  9 10 11 12 13 14 15
        [Q]  ■        ■  ■     ■     ■
        [W]     ■                                   ■  ■
        ...
        Most common sequence: 24,284 games, 59.7% win rate
    """

    GRID_ROW = 24
    LEVELS = 15

    def __init__(self, parent=None):
        super().__init__(parent)
        self.options = []
        self.selected = 0
        self.setVisible(False)

    def setData(self, skills: dict):
        if not skills:
            self.setVisible(False)
            return

        self.options = skills.get('options') or [skills]
        self.selected = 0
        self.setFixedHeight(self.__gridTop() + 18 + 4 * self.GRID_ROW + 34)
        self.refresh()
        self.setVisible(True)

    def __gridTop(self):
        return TITLE_H - 6 + len(self.options) * ROW_H + 14

    def select(self, index):
        if index != self.selected:
            self.selected = index
            self.refresh()

    def drawContent(self, p, pal, dark):
        if not self.options:
            return

        w = self.width()
        right = w - PAD
        self.drawTitle(p, pal, self.tr("Skill order"))
        drawStatHeaders(p, pal, right, 12, 22, (pickRateText(), winRateText(), self.tr("Games")))

        selectable = len(self.options) > 1
        for i, option in enumerate(self.options):
            y = TITLE_H - 6 + i * ROW_H
            rect = QRectF(PAD - 6, y + 2, w - 2 * PAD + 12, ROW_H - 4)
            if selectable and i == self.selected:
                drawSelection(p, rect)

            x = PAD
            masteries = option.get('masteries') or []
            for j, skill in enumerate(masteries):
                drawSkillChip(p, QRectF(x, y + 5, 26, 26), skill)
                x += 26
                if j != len(masteries) - 1:
                    drawChevron(p, x + 9, y + ROW_H / 2, pal['muted'])
                    x += 18

            drawStats(p, pal, right, y, ROW_H, option['pickRate'], option['win'], option['play'])

            if selectable:
                self.addLink(rect, lambda index=i: self.select(index))

        # 加点表: 每列一级, 每行一个技能
        option = self.options[self.selected]
        order = (option.get('order') or [])[:self.LEVELS]
        top = self.__gridTop()
        labelW = 34
        colW = min(34.0, (w - 2 * PAD - labelW) / self.LEVELS)
        gridX = PAD + labelW

        for level in range(self.LEVELS):
            drawText(p, QRectF(gridX + level * colW, top, colW, 16), str(level + 1),
                     numberFont(12, 400), pal['muted'], Qt.AlignCenter)

        cellW, cellH = colW - 4, self.GRID_ROW - 4
        for row, skill in enumerate("QWER"):
            y = top + 18 + row * self.GRID_ROW
            drawSkillChip(p, QRectF(PAD, y + 1, 22, 22), skill, 5, textFont(12, True))

            for level in range(self.LEVELS):
                cell = QRectF(gridX + level * colW + 2, y + 2, cellW, cellH)
                if level < len(order) and order[level] == skill:
                    p.setPen(Qt.NoPen)
                    p.setBrush(SKILL_COLORS[skill])
                    p.drawRoundedRect(cell, 4, 4)
                    drawText(p, cell, skill, textFont(12, True), QColor(255, 255, 255),
                             Qt.AlignCenter)
                else:
                    p.setPen(Qt.NoPen)
                    p.setBrush(tint(pal['hairline'], 140 if dark else 130))
                    p.drawRoundedRect(cell, 4, 4)

        if option.get('orderPlay'):
            y = top + 18 + 4 * self.GRID_ROW + 6
            caption = self.tr("Most common sequence: %1, %2 win rate") \
                .replace("%1", gamesText(option['orderPlay'])) \
                .replace("%2", pct(rate(option['orderWin'], option['orderPlay'])))
            drawText(p, QRectF(PAD, y, w - 2 * PAD, 20), caption, textFont(12), pal['muted'])


# ---------------- 后期装备 ----------------

class LateItemsCard(PaintedCard):
    """
    核心三件套之外的终局装备 (按选择率, 两列, 先列后行):

        Late-game items                      Other items in final builds
        [物品]   42.5%   50.9%   83,825   │  [物品]   10.6%   53.8%  20,907
    """

    ROWS = 5
    ROW = 36
    WIDTHS = (52, 52, 64)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.items = []
        self.setVisible(False)

    def setData(self, items: list):
        self.items = items or []
        if not self.items:
            self.setVisible(False)
            return

        rows = min(self.ROWS, (len(self.items) + 1) // 2)
        self.setFixedHeight(TITLE_H + 18 + rows * self.ROW + 8)
        self.refresh()
        self.setVisible(True)

    def drawContent(self, p, pal, dark):
        w, h = self.width(), self.height()
        self.drawTitle(p, pal, self.tr("Late-game items"),
                       self.tr("Other items in final builds"))

        gutter = 24
        colW = (w - 2 * PAD - gutter) / 2
        rows = min(self.ROWS, (len(self.items) + 1) // 2)
        midX = PAD + colW + gutter / 2
        p.setPen(QPen(pal['hairline'], 1))
        p.drawLine(QPointF(midX, TITLE_H - 4), QPointF(midX, h - 12))

        for column in range(2):
            x = PAD + column * (colW + gutter)
            right = x + colW
            drawStatHeaders(p, pal, right, TITLE_H - 6, 18,
                            (self.tr("Pick"), self.tr("Win"), self.tr("Games")), self.WIDTHS)

            for row in range(rows):
                index = column * rows + row
                if index >= len(self.items):
                    break

                item = self.items[index]
                y = TITLE_H + 14 + row * self.ROW
                rect = QRectF(x, y + (self.ROW - ICON) / 2, ICON, ICON)
                drawRoundedPixmap(p, loadPixmap(item['icon']), rect, 5)
                self.addTip(rect, lambda id=item['id']: itemTooltipHtml(id))
                drawStats(p, pal, right, y, self.ROW, item['pickRate'], item['win'], item['play'],
                          self.WIDTHS)


# ---------------- 趋势 ----------------

class TrendsCard(PaintedCard):
    """
    左: 不同游戏时长的胜率 (以 50% 为基线的上下柱); 右: 近期版本的胜率 / 选择率 / 禁用率走势

        Trends
        Win rate by game length (min)      │  By patch                16.11 – 16.20
          ▇     ▇                          │  Win rate   49.4%  −1.0  ╱╲__╱
        ──────────────── 50%               │  Pick rate   9.8%  −6.2  ...
             ▆     ▆                       │  Ban rate    6.9%  −2.9  ...
         <25 25-30 30-35 35-40 40+         │
    """

    HEIGHT = 196
    BUCKETS = {0: "<25", 25: "25–30", 30: "30–35", 35: "35–40", 40: "40+"}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.gameLengths = []
        self.series = []
        self.setFixedHeight(self.HEIGHT)
        self.setVisible(False)

    def setData(self, trends: dict):
        trends = trends or {}
        self.gameLengths = trends.get('gameLengths') or []

        series = []
        for key, label in (('win', winRateText()), ('pick', pickRateText()),
                           ('ban', trText("ChampionTitleBar", "Ban Rate", "Ban rate"))):
            points = trends.get(key) or []
            if any(t['rate'] for t in points):
                series.append((key, label, points))
        self.series = series

        if not self.gameLengths and not self.series:
            self.setVisible(False)
            return

        self.refresh()
        self.setVisible(True)

    def drawContent(self, p, pal, dark):
        w, h = self.width(), self.height()
        self.drawTitle(p, pal, self.tr("Trends"))

        gutter = 24
        halves = [x for x in (self.gameLengths and 'length', self.series and 'patch') if x]
        if not halves:
            return
        colW = (w - 2 * PAD - gutter * (len(halves) - 1)) / len(halves)

        for i, kind in enumerate(halves):
            x = PAD + i * (colW + gutter)
            if i:
                p.setPen(QPen(pal['hairline'], 1))
                p.drawLine(QPointF(x - gutter / 2, TITLE_H - 4), QPointF(x - gutter / 2, h - 12))

            if kind == 'length':
                self.__drawGameLengths(p, pal, x, colW)
            else:
                self.__drawPatches(p, pal, x, colW)

    def __drawGameLengths(self, p, pal, x, width):
        top = TITLE_H - 6
        drawText(p, QRectF(x, top, width, 18), self.tr("Win rate by game length (min)"),
                 textFont(12), pal['muted'])

        winC, loseC = resultColor('win'), resultColor('lose')
        n = len(self.gameLengths)
        slot = width / n
        barW = min(24.0, slot - 18)
        half = 30
        mid = top + 22 + 16 + half
        maxDev = max(0.04, *(abs(g['winRate'] - g['average']) for g in self.gameLengths))

        # 50% 基线
        p.setPen(QPen(pal['muted'], 1, Qt.DashLine))
        p.drawLine(QPointF(x, mid + 0.5), QPointF(x + width, mid + 0.5))

        for i, g in enumerate(self.gameLengths):
            cx = x + slot * i + slot / 2
            dev = g['winRate'] - g['average']
            length = max(2.0, abs(dev) / maxDev * half)
            color = winC if dev >= 0 else loseC
            bar = QRectF(cx - barW / 2, mid - length if dev >= 0 else mid, barW, length)
            p.setPen(Qt.NoPen)
            p.setBrush(color)
            p.drawRoundedRect(bar, 3, 3)

            labelRect = QRectF(cx - slot / 2, bar.top() - 18, slot, 16) if dev >= 0 \
                else QRectF(cx - slot / 2, bar.bottom() + 2, slot, 16)
            drawText(p, labelRect, pct(g['winRate']), numberFont(12), pal['ink'],
                     Qt.AlignCenter)

            bucket = self.BUCKETS.get(g['minute'], str(g['minute']))
            drawText(p, QRectF(cx - slot / 2, mid + half + 20, slot, 18), bucket,
                     numberFont(12, 400), pal['muted'], Qt.AlignCenter)

            tip = self.tr("%1 min: %2 win rate").replace("%1", bucket) \
                .replace("%2", pct(g['winRate'], 2))
            if g.get('rank'):
                tip += "\n" + self.tr("Rank %1 at this game length").replace("%1", str(g['rank']))
            self.addTip(QRectF(cx - slot / 2, top + 18, slot, 2 * half + 56), tip)

    def __drawPatches(self, p, pal, x, width):
        top = TITLE_H - 6
        drawText(p, QRectF(x, top, width, 18), self.tr("By patch"), textFont(12), pal['muted'])

        versions = self.series[0][2]
        if versions:
            drawText(p, QRectF(x, top, width, 18),
                     f"{versions[0]['version']} – {versions[-1]['version']}",
                     numberFont(12, 400), pal['muted'], Qt.AlignRight | Qt.AlignVCenter)

        labelW, valueW, deltaW = 70, 50, 44
        sparkX = x + labelW + valueW + deltaW + 10
        sparkW = x + width - sparkX
        winC, loseC = resultColor('win'), resultColor('lose')

        for row, (key, label, points) in enumerate(self.series):
            y = top + 26 + row * 38
            rowH = 30
            latest = points[-1]['rate']
            drawText(p, QRectF(x, y, labelW, rowH), label, textFont(12), pal['muted'])
            drawText(p, QRectF(x + labelW, y, valueW, rowH), pct(latest), numberFont(14),
                     pal['ink'], Qt.AlignRight | Qt.AlignVCenter)

            if len(points) > 1:
                delta = round((latest - points[-2]['rate']) * 100, 1)
                sign = "+" if delta > 0 else ("−" if delta < 0 else "±")
                if key == 'win' and delta:
                    color = winC if delta > 0 else loseC
                else:
                    color = pal['muted']
                drawText(p, QRectF(x + labelW + valueW, y, deltaW, rowH),
                         f"{sign}{abs(delta):.1f}", numberFont(12, 400), color,
                         Qt.AlignRight | Qt.AlignVCenter)

            if sparkW > 30 and len(points) > 1:
                self.__drawSparkline(p, pal, QRectF(sparkX, y + 4, sparkW, rowH - 8), points,
                                     reference=0.5 if key == 'win' else None)

            tip = "\n".join(f"{t['version']}: {pct(t['rate'], 2)}"
                            + (f"  (#{t['rank']})" if t.get('rank') else "")
                            for t in reversed(points))
            self.addTip(QRectF(x, y, width, rowH), f"{label}\n{tip}")

    @staticmethod
    def __drawSparkline(p, pal, rect, points, reference=None):
        values = [t['rate'] for t in points]
        lo, hi = min(values), max(values)
        if reference is not None:
            lo, hi = min(lo, reference), max(hi, reference)
        span = (hi - lo) or 1e-6
        step = rect.width() / (len(values) - 1)

        def pt(i, v):
            return QPointF(rect.left() + i * step, rect.bottom() - (v - lo) / span * rect.height())

        if reference is not None:
            p.setPen(QPen(pal['hairline'], 1, Qt.DashLine))
            p.drawLine(pt(0, reference), pt(len(values) - 1, reference))

        path = QPainterPath(pt(0, values[0]))
        for i, v in enumerate(values[1:], 1):
            path.lineTo(pt(i, v))
        p.setPen(QPen(pal['muted'], 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)

        last = pt(len(values) - 1, values[-1])
        p.setPen(Qt.NoPen)
        p.setBrush(pal['ink'])
        p.drawEllipse(last, 3, 3)


# ---------------- 斗魂竞技场: 强化符文 / 双人搭档 ----------------

AUGMENT_COLORS = (QColor(153, 176, 180), QColor(194, 172, 141), QColor(184, 123, 255))


class AugmentsCard(PaintedCard):
    """
    斗魂竞技场强化符文, 按稀有度分三列 (白银 / 黄金 / 棱彩), 每列按选择率取前 10:

        Augments                                    First place rate · games
        Silver               │ Gold                 │ Prismatic
        [图] Zealot          │ [图] Quest: Three... │ [图] Dual Wield
             18.9%  9,263    │      20.9%  23,217   │      23.1%  17,592
    """

    ROWS = 10
    ROW = 42

    def __init__(self, parent=None):
        super().__init__(parent)
        self.groups = []
        self.setVisible(False)

    def setData(self, groups: list):
        self.groups = [g[:self.ROWS] for g in (groups or [])[:3]]
        if not any(self.groups):
            self.setVisible(False)
            return

        rows = max(len(g) for g in self.groups)
        self.setFixedHeight(TITLE_H + 22 + rows * self.ROW + 8)
        self.refresh()
        self.setVisible(True)

    def drawContent(self, p, pal, dark):
        w, h = self.width(), self.height()
        self.drawTitle(p, pal, self.tr("Augments"), self.tr("First place rate, games"))

        gutter = 16
        colW = (w - 2 * PAD - 2 * gutter) / 3
        titles = (self.tr("Silver"), self.tr("Gold"), self.tr("Prismatic"))

        for column, group in enumerate(self.groups):
            x = PAD + column * (colW + gutter)
            color = AUGMENT_COLORS[column]
            if column:
                p.setPen(QPen(pal['hairline'], 1))
                p.drawLine(QPointF(x - gutter / 2, TITLE_H - 4), QPointF(x - gutter / 2, h - 12))
            drawText(p, QRectF(x, TITLE_H - 4, colW, 20), titles[column], textFont(12, True),
                     color.darker(130) if not dark else color)

            for row, aug in enumerate(group):
                y = TITLE_H + 22 + row * self.ROW
                icon = QRectF(x, y + 4, 30, 30)
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(30, 35, 40))
                p.drawRoundedRect(icon, 6, 6)
                drawRoundedPixmap(p, loadPixmap(aug.get('icon')), icon.adjusted(2, 2, -2, -2), 4)
                p.setPen(QPen(color, 1.5))
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(icon.adjusted(0.75, 0.75, -0.75, -0.75), 6, 6)

                tx, tw = x + 38, colW - 38
                font = textFont(12, True)
                drawText(p, QRectF(tx, y + 2, tw, 18), elide(aug.get('name') or "", font, tw),
                         font, pal['ink'])
                play = aug.get('play') or 0
                drawRuns(p, tx, y + 20, 18, (
                    (pct(rate(aug.get('firstPlace') or 0, play)), numberFont(12), pal['ink']),
                    (f"   {play:,}", numberFont(12, 400), pal['muted'])))

                tip = f"<b>{aug.get('name') or ''}</b><br>" + \
                    self.tr("First place: %1").replace(
                        "%1", pct(rate(aug.get('firstPlace') or 0, play), 2)) + "<br>" + \
                    self.tr("Average place: %1").replace(
                        "%1", f"{(aug.get('totalPlace') or 0) / play:.2f}" if play else "-") + \
                    "<br>" + gamesText(play)
                self.addTip(QRectF(x, y, colW, self.ROW), tip)


class DuoPartnersCard(PaintedCard):
    """
    斗魂竞技场的双人搭档 (按选择率取前 10), 点击查看该英雄:

        Common duo partners          Avg place   First place   Win rate    Games
        (头) Sett                         3.16        22.6%      58.3%     1,842
    """

    championClicked = Signal(int)

    ROWS = 10
    ROW = 32
    WIDTHS = (64, 72, 64, 64)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.partners = []
        self.setVisible(False)

    def setData(self, partners: list):
        self.partners = [x for x in partners or [] if x.get('play')][:self.ROWS]
        if not self.partners:
            self.setVisible(False)
            return

        self.setFixedHeight(TITLE_H - 4 + len(self.partners) * self.ROW + 10)
        self.refresh()
        self.setVisible(True)

    def drawContent(self, p, pal, dark):
        w = self.width()
        right = w - PAD
        self.drawTitle(p, pal, self.tr("Common duo partners"))
        labels = (trText("ChampionTitleBar", "Average Place", "Avg. place"),
                  trText("ChampionTitleBar", "First Rate", "First place"), winRateText(),
                  self.tr("Games"))
        drawStatHeaders(p, pal, right, 12, 22, labels, self.WIDTHS)
        cols = statColumns(right, self.WIDTHS)
        align = Qt.AlignRight | Qt.AlignVCenter

        for i, c in enumerate(self.partners):
            y = TITLE_H - 4 + i * self.ROW
            play = c['play']
            drawCircle(p, loadPixmap(c.get('icon')), QRectF(PAD, y + 4, 24, 24),
                       pal['hairline'], 1, crop=3)
            nameW = cols[0][0] - PAD - 40
            font = textFont(13)
            drawText(p, QRectF(PAD + 32, y, nameW, self.ROW), elide(c.get('name') or "", font,
                                                                    nameW), font, pal['ink'])
            values = (f"{c['totalPlace'] / play:.2f}", pct(rate(c['firstPlace'], play)),
                      pct(rate(c['win'], play)), f"{play:,}")
            fonts = (numberFont(14), numberFont(14, 400), numberFont(14, 400),
                     numberFont(13, 400))
            colors = (pal['ink'], pal['ink'], pal['ink'], pal['muted'])
            for (x, cw), value, font_, color in zip(cols, values, fonts, colors):
                drawText(p, QRectF(x, y, cw, self.ROW), value, font_, color, align)

            rect = QRectF(PAD - 6, y + 1, w - 2 * PAD + 12, self.ROW - 2)
            self.addLink(rect, lambda id=c['championId']: self.championClicked.emit(id))


# ---------------- 无数据提示 ----------------

class NoticeCard(PaintedCard):
    """该模式下 OP.GG 没有出装数据时的提示"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.text = ""
        self.setFixedHeight(64)
        self.setVisible(False)

    def setText(self, text):
        self.text = text
        self.refresh()

    def drawContent(self, p, pal, dark):
        drawText(p, QRectF(PAD, 0, self.width() - 2 * PAD, self.height()), self.text,
                 textFont(13), pal['muted'], Qt.AlignCenter)
