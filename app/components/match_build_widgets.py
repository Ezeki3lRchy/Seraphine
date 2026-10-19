"""
对局详情 (搜索页右侧) 的 "出装" 视图, 仿 OP.GG 对局的 Build 页:

- DetailModeBar: 标题栏下的 "Overview / Build" 分段切换 + (Build 时) 10 名玩家的英雄头像条, 点头像
  切换查看的玩家;
- MatchBuildView: 物品时间线 (按分钟分组, 组间箭头, 自动换行, 叠加计数 / 卖出标记) + 技能加点
  (主升顺序 + 1..18 级序列) + 符文页 (主系 / 副系, 选中全彩, 未选灰暗).

全部自绘并缓存成 pixmap (同记分表), 悬浮提示靠命中测试. 数据由 app.lol.match_build 解析,
颜色与字体见 design_tokens.py.
"""
import html

from PySide6.QtCore import Qt, QRectF, QPointF, QEvent, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QFontMetrics, QPixmap, QImage
from PySide6.QtWidgets import QWidget, QVBoxLayout, QSizePolicy, QToolTip

from app.common.qfluentwidgets import isDarkTheme
from app.components.design_tokens import numberFont, textFont, deathsColor, tint
from app.components.match_widgets import itemTooltipHtml, TOOLTIP_WIDTH
from app.components.scoreboard import (cachedContent, drawText, drawRoundedPixmap,
                                       drawChampion)

PAD = 16          # 卡片内边距
TITLE_H = 46      # 卡片标题行 (标题在 y=14..34)
RADIUS = 8

# 各系符文的主题色 (选中符文的描边)
STYLE_COLORS = {8000: QColor('#C8AA6E'), 8100: QColor('#D44242'), 8200: QColor('#9FAAFC'),
                8300: QColor('#49AAB9'), 8400: QColor('#A1D586')}

_pixmaps = {}
_grays = {}


def pixmap(path):
    """按路径缓存的 QPixmap (物品 / 符文图标在多名玩家间复用)"""
    if not path:
        return QPixmap()
    if path not in _pixmaps:
        _pixmaps[path] = QPixmap(path)
    return _pixmaps[path]


def grayPixmap(path, darken=False):
    """
    去色 (保留透明度) 的图标, 用于未选中的符文; darken 时再压暗一些, 让浅色的基石图在白底上
    也看得出轮廓
    """
    if not path:
        return QPixmap()
    key = (path, darken)
    if key not in _grays:
        image = QImage(path)
        if image.isNull():
            _grays[key] = QPixmap()
        else:
            image = image.convertToFormat(QImage.Format_ARGB32)
            gray = image.convertToFormat(QImage.Format_Grayscale8)                 .convertToFormat(QImage.Format_ARGB32_Premultiplied)
            gray.setAlphaChannel(image.convertToFormat(QImage.Format_Alpha8))
            if darken:
                p = QPainter(gray)
                p.setCompositionMode(QPainter.CompositionMode_SourceAtop)
                p.fillRect(gray.rect(), QColor(40, 46, 60, 120))
                p.end()
            _grays[key] = QPixmap.fromImage(gray)
    return _grays[key]


def tooltipHtml(title, body=""):
    body = f"<br>{body}" if body else ""
    return (f"<table width={TOOLTIP_WIDTH}><tr><td style='font-size:12px'>"
            f"<b style='font-size:14px'>{html.escape(title)}</b>{body}</td></tr></table>")


def drawCard(p, rect, pal):
    """出装视图的卡片底: 纸面 + 发丝线 (不带胜负色)"""
    path = QPainterPath()
    path.addRoundedRect(QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5), RADIUS, RADIUS)
    p.setPen(QPen(pal['hairline'], 1))
    p.setBrush(pal['sheet'])
    p.drawPath(path)


def drawChevron(p, center, color, size=4.5):
    p.setPen(QPen(color, 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.setBrush(Qt.NoBrush)
    path = QPainterPath(QPointF(center.x() - size / 2, center.y() - size))
    path.lineTo(QPointF(center.x() + size / 2, center.y()))
    path.lineTo(QPointF(center.x() - size / 2, center.y() + size))
    p.drawPath(path)


# ---------------------------------------------------------------- 切换条 ----

class DetailModeBar(QWidget):
    """
    标题栏下的一行:  [ Overview | Build ]                 名字  (蓝队 5 头像) | (红队 5 头像)
    头像条只在 Build 时显示; 选中的头像金边全亮, 其余半透明
    """

    modeChanged = Signal(int)
    playerSelected = Signal(int)

    HEIGHT = 40
    SEG_H = 30
    ICON = 30
    ICON_GAP = 6
    TEAM_GAP = 17

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(self.HEIGHT)
        self.setMouseTracking(True)
        self.labels = [self.tr("Overview"), self.tr("Build")]
        self.mode = 0
        self.teams = ([], [])     # [(participantId, QPixmap, name)] x 2
        self.selected = None
        self.hover = None         # ('mode', i) | ('player', participantId)
        self._cache = self._cacheKey = None

    def setMode(self, mode):
        self.mode = mode
        self._cacheKey = None
        self.update()

    def setPlayers(self, team1, team2):
        """两队的 summoners (parseGameDetailData 的格式); 默认选中当前玩家"""
        def players(summoners):
            return [(s.get('participantId'), QPixmap(s.get('championIcon') or ''),
                     s.get('summonerName') or '') for s in summoners]

        self.teams = (players(team1), players(team2))
        current = [s.get('participantId') for s in list(team1) + list(team2) if s.get('isCurrent')]
        everyone = [pid for team in self.teams for pid, _, _ in team]
        self.selected = current[0] if current else (everyone[0] if everyone else None)
        self._cacheKey = None
        self.update()

    def selectedName(self):
        for team in self.teams:
            for pid, _, name in team:
                if pid == self.selected:
                    return name
        return ""

    # ---------------- 几何 ----------------

    def modeRects(self):
        font = QFontMetrics(textFont(13, True))
        x, y = 3, (self.HEIGHT - self.SEG_H) / 2 + 3
        rects = []
        for label in self.labels:
            w = font.horizontalAdvance(label) + 30
            rects.append(QRectF(x, y, w, self.SEG_H - 6))
            x += w
        return rects

    def playerRects(self):
        """[(participantId, QRectF)], 右对齐; 非 Build 模式为空"""
        if self.mode != 1:
            return []
        n1, n2 = len(self.teams[0]), len(self.teams[1])
        total = (n1 + n2) * self.ICON + max(n1 + n2 - 2, 0) * self.ICON_GAP + \
            (self.TEAM_GAP if n1 and n2 else 0)
        x = self.width() - 2 - total
        y = (self.HEIGHT - self.ICON) / 2
        rects = []
        for t, team in enumerate(self.teams):
            for i, (pid, _, _) in enumerate(team):
                rects.append((pid, QRectF(x, y, self.ICON, self.ICON)))
                x += self.ICON + (self.ICON_GAP if i < len(team) - 1 else 0)
            if t == 0 and n1 and n2:
                x += self.TEAM_GAP
        return rects

    def hitTest(self, pos):
        for i, rect in enumerate(self.modeRects()):
            if rect.contains(pos):
                return ('mode', i)
        for pid, rect in self.playerRects():
            if rect.adjusted(-2, -2, 2, 2).contains(pos):
                return ('player', pid)
        return None

    # ---------------- 交互 ----------------

    def event(self, e):
        if e.type() == QEvent.ToolTip:
            hit = self.hitTest(QPointF(e.pos()))
            if hit and hit[0] == 'player':
                for team in self.teams:
                    for pid, _, name in team:
                        if pid == hit[1] and name:
                            QToolTip.showText(e.globalPos(), name, self)
                            return True
            QToolTip.hideText()
            e.ignore()
            return True
        return super().event(e)

    def mouseMoveEvent(self, e):
        hit = self.hitTest(e.position())
        clickable = hit is not None and hit not in (('mode', self.mode),
                                                    ('player', self.selected))
        self.setCursor(Qt.PointingHandCursor if clickable else Qt.ArrowCursor)
        if hit != self.hover:
            self.hover = hit
            self.update()
        super().mouseMoveEvent(e)

    def leaveEvent(self, e):
        if self.hover is not None:
            self.hover = None
            self.update()
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        e.accept()

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.LeftButton:
            return super().mouseReleaseEvent(e)

        hit = self.hitTest(e.position())
        if hit and hit[0] == 'mode' and hit[1] != self.mode:
            self.setMode(hit[1])
            self.modeChanged.emit(hit[1])
        elif hit and hit[0] == 'player' and hit[1] != self.selected:
            self.selected = hit[1]
            self._cacheKey = None
            self.update()
            self.playerSelected.emit(hit[1])
        e.accept()

    # ---------------- 绘制 ----------------

    def paintEvent(self, e):
        p = QPainter(self)
        p.drawPixmap(0, 0, cachedContent(self, self.__draw, (self.mode, self.selected,
                                                             self.hover)))
        p.end()

    def __draw(self, p, pal):
        dark = isDarkTheme()
        ink, muted = pal['ink'], pal['muted']

        # 分段切换: 发丝线色的底槽 + 选中段浮起
        rects = self.modeRects()
        track = QRectF(0, (self.HEIGHT - self.SEG_H) / 2,
                       rects[-1].right() + 3, self.SEG_H)
        p.setPen(Qt.NoPen)
        p.setBrush(pal['hairline'])
        p.drawRoundedRect(track, 7, 7)
        for i, (rect, label) in enumerate(zip(rects, self.labels)):
            selected = i == self.mode
            if selected:
                p.setPen(Qt.NoPen if dark else QPen(tint(ink, 22), 1))
                p.setBrush(QColor('#474E5B') if dark else pal['sheet'])
                p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 5, 5)
            color = ink if selected or self.hover == ('mode', i) else muted
            drawText(p, rect, label, textFont(13, selected), color, Qt.AlignCenter)

        players = self.playerRects()
        if not players:
            return

        # 选中玩家的名字 (头像条左边)
        left, right = track.right() + 20, players[0][1].left() - 12
        name = self.selectedName()
        if name and right - left > 40:
            font = textFont(13, True)
            name = QFontMetrics(font).elidedText(name, Qt.ElideRight, int(right - left))
            drawText(p, QRectF(left, 0, right - left, self.HEIGHT), name, font, ink,
                     Qt.AlignRight | Qt.AlignVCenter)

        icons = {pid: pix for team in self.teams for pid, pix, _ in team}
        n1 = len(self.teams[0])
        for i, (pid, rect) in enumerate(players):
            selected = pid == self.selected
            p.setOpacity(1 if selected else (0.85 if self.hover == ('player', pid) else 0.5))
            drawChampion(p, icons.get(pid), rect, pal['gold'] if selected else pal['hairline'],
                         2 if selected else 1, crop=3)
            p.setOpacity(1)
            if i == n1 - 1 and len(players) > n1:
                x = rect.right() + self.TEAM_GAP / 2
                p.setPen(QPen(pal['hairline'], 1))
                p.drawLine(QPointF(x, rect.top() + 4), QPointF(x, rect.bottom() - 4))


# ---------------------------------------------------------------- 卡片 ----

class BuildCard(QWidget):
    """
    出装视图的一张卡片: 纸面底 + 标题 + 自绘内容. 高度随宽度变化 (contentHeight), 宽度变了
    就重新定高; 命中区域 (rect, tooltip html) 在绘制缓存时生成
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.title = ""
        self.hits = []
        self._cache = self._cacheKey = None

    def contentHeight(self, width):
        return TITLE_H + PAD

    def refresh(self):
        self._cacheKey = None
        self.setFixedHeight(self.contentHeight(self.width()))
        self.update()

    def resizeEvent(self, e):
        h = self.contentHeight(self.width())
        if h != self.height():
            self.setFixedHeight(h)
        super().resizeEvent(e)

    def event(self, e):
        if e.type() == QEvent.ToolTip:
            for rect, tip in self.hits:
                if rect.contains(e.pos()):
                    if tip:
                        QToolTip.showText(e.globalPos(), tip, self, rect)
                        return True
                    break
            QToolTip.hideText()
            e.ignore()
            return True
        return super().event(e)

    def paintEvent(self, e):
        p = QPainter(self)
        p.drawPixmap(0, 0, cachedContent(self, self.__draw))
        p.end()

    def __draw(self, p, pal):
        drawCard(p, self.rect(), pal)
        if self.title:
            drawText(p, QRectF(PAD, 14, self.width() - 2 * PAD, 20), self.title,
                     textFont(14, True), pal['ink'])
        self.hits = []
        self.drawContent(p, pal)

    def drawContent(self, p, pal):
        pass


class MessageCard(BuildCard):
    """没有标题的提示卡: 一行主文字 + 可选的一行次要说明 (加载中 / 无出装数据)"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.text, self.detail, self.quiet = "", "", False
        self.refresh()

    def setMessage(self, text, detail="", quiet=False):
        self.text, self.detail, self.quiet = text, detail, quiet
        self.refresh()

    def contentHeight(self, width):
        return 78 if self.detail else 56

    def drawContent(self, p, pal):
        w, h = self.width(), self.height()
        color = pal['muted'] if self.quiet else pal['ink']
        if self.detail:
            drawText(p, QRectF(PAD, h / 2 - 21, w - 2 * PAD, 20), self.text,
                     textFont(13, not self.quiet), color, Qt.AlignCenter)
            drawText(p, QRectF(PAD, h / 2 + 1, w - 2 * PAD, 20), self.detail, textFont(12),
                     pal['muted'], Qt.AlignCenter)
        else:
            drawText(p, QRectF(PAD, 0, w - 2 * PAD, h), self.text,
                     textFont(13, not self.quiet), color, Qt.AlignCenter)


class ItemBuildCard(BuildCard):
    """
    物品时间线: 每组 (同一分钟的购买) 一排图标 + 下方 "N min", 组间箭头, 放不下就换行.
    同组重复购买右下角计数, 后来卖掉的半透明 + 右上角红色叉
    """

    ICON, GAP = 30, 4
    ARROW_W = 26
    LABEL_H = 18
    ROW_H = ICON + 4 + LABEL_H
    ROW_GAP = 12

    def __init__(self, parent=None):
        super().__init__(parent)
        self.title = self.tr("Item build")
        self.groups = []
        self.icons = {}
        self.refresh()

    def setGroups(self, groups, iconPaths):
        self.groups = groups or []
        self.icons = {itemId: pixmap(path) for itemId, path in (iconPaths or {}).items()}
        self.refresh()

    def minuteText(self, minute):
        return self.tr("%1 min").replace("%1", str(minute))

    def layoutGroups(self, width):
        """[(group, x, y, slotW)], [(arrowX, y)], 内容底部 y"""
        metrics = QFontMetrics(textFont(12))
        right = width - PAD
        x, y = PAD, TITLE_H
        placed, arrows = [], []
        for group in self.groups:
            n = len(group['items'])
            itemsW = n * self.ICON + (n - 1) * self.GAP
            slotW = max(itemsW, metrics.horizontalAdvance(self.minuteText(group['minute'])))
            if x > PAD and x + self.ARROW_W + slotW > right:
                x, y = PAD, y + self.ROW_H + self.ROW_GAP
            if x > PAD:
                arrows.append((x, y))
                x += self.ARROW_W
            placed.append((group, x, y, slotW))
            x += slotW
        return placed, arrows, (y + self.ROW_H if placed else TITLE_H + 20)

    def contentHeight(self, width):
        return self.layoutGroups(width)[2] + PAD

    def drawContent(self, p, pal):
        if not self.groups:
            drawText(p, QRectF(PAD, TITLE_H, self.width() - 2 * PAD, 20),
                     self.tr("No purchases recorded."), textFont(12), pal['muted'])
            return

        placed, arrows, _ = self.layoutGroups(self.width())
        for x, y in arrows:
            drawChevron(p, QPointF(x + self.ARROW_W / 2, y + self.ICON / 2), pal['muted'])

        for group, x, y, slotW in placed:
            for i, item in enumerate(group['items']):
                rect = QRectF(x + i * (self.ICON + self.GAP), y, self.ICON, self.ICON)
                self.drawItem(p, pal, rect, item)
                self.hits.append((rect.toRect(), itemTooltipHtml(item['itemId'])))
            drawText(p, QRectF(x, y + self.ICON + 4, slotW + 8, self.LABEL_H),
                     self.minuteText(group['minute']), textFont(12), pal['muted'])

    def drawItem(self, p, pal, rect, item):
        icon = self.icons.get(item['itemId'])
        if icon is not None and not icon.isNull():
            p.setOpacity(0.45 if item['sold'] else 1)
            drawRoundedPixmap(p, icon, rect, 4)
            p.setOpacity(1)
        else:
            p.setPen(QPen(pal['hairline'], 1))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 4, 4)

        if item['count'] > 1:
            text = str(item['count'])
            font = numberFont(11)
            bw = max(QFontMetrics(font).horizontalAdvance(text) + 6, 14)
            badge = QRectF(rect.right() - bw + 2, rect.bottom() - 12, bw, 14)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(17, 20, 28, 225))
            p.drawRoundedRect(badge, 4, 4)
            drawText(p, badge, text, font, QColor(255, 255, 255), Qt.AlignCenter)

        if item['sold']:
            mark = QRectF(rect.right() - 10, rect.top() - 4, 14, 14)
            p.setPen(QPen(pal['sheet'], 1.5))
            p.setBrush(deathsColor())
            p.drawEllipse(mark)
            c, d = mark.center(), 2.6
            p.setPen(QPen(QColor(255, 255, 255), 1.6, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(c.x() - d, c.y() - d), QPointF(c.x() + d, c.y() + d))
            p.drawLine(QPointF(c.x() - d, c.y() + d), QPointF(c.x() + d, c.y() - d))


def skillColors(key, pal):
    """技能字母徽章的 (底色, 字色): Q / W / E 中性, R 海克斯金"""
    if key == 'R':
        return pal['gold'], (pal['sheet'] if isDarkTheme() else QColor(255, 255, 255))
    return tint(pal['ink'], 24), pal['ink']


class SkillOrderCard(BuildCard):
    """技能: 主升顺序 (Q > E > W) + 1..18 级加点序列 (每级一个字母徽章, R 高亮)"""

    BADGE = 30
    CELL_MAX, CELL_GAP = 28, 4
    LEVELS = 18

    def __init__(self, parent=None):
        super().__init__(parent)
        self.title = self.tr("Skill order")
        self.skills = {'sequence': [], 'order': []}
        self.refresh()

    def setSkills(self, skills):
        self.skills = skills or {'sequence': [], 'order': []}
        self.refresh()

    ORDER_GAP = 22        # 主升顺序徽章之间 (箭头)
    INLINE_GAP = 40       # 并排时主升顺序与序列之间

    def orderWidth(self):
        n = len(self.skills['order'])
        return n * self.BADGE + max(n - 1, 0) * self.ORDER_GAP

    def sequenceWidth(self, cell):
        return self.LEVELS * cell + (self.LEVELS - 1) * self.CELL_GAP

    def geometry(self, width):
        """(inline, cell, 序列左 x, 序列等级号的 y); 够宽时主升顺序与序列并排"""
        inline = width - 2 * PAD >= self.orderWidth() + self.INLINE_GAP +             self.sequenceWidth(self.CELL_MAX)
        if inline:
            return True, self.CELL_MAX, PAD + self.orderWidth() + self.INLINE_GAP, TITLE_H
        free = width - 2 * PAD - (self.LEVELS - 1) * self.CELL_GAP
        cell = int(max(min(self.CELL_MAX, free / self.LEVELS), 16))
        return False, cell, PAD, TITLE_H + self.BADGE + 16

    def contentHeight(self, width):
        if not self.skills['sequence']:
            return TITLE_H + 20 + PAD
        _, cell, _, top = self.geometry(width)
        return top + 20 + cell + PAD

    def drawBadge(self, p, pal, rect, key, size):
        fill, color = skillColors(key, pal)
        p.setPen(Qt.NoPen)
        p.setBrush(fill)
        p.drawRoundedRect(rect, 5, 5)
        drawText(p, rect, key, numberFont(size, 700), color, Qt.AlignCenter)

    def drawContent(self, p, pal):
        sequence, order = self.skills['sequence'], self.skills['order']
        if not sequence:
            drawText(p, QRectF(PAD, TITLE_H, self.width() - 2 * PAD, 20),
                     self.tr("No skill level-ups recorded."), textFont(12), pal['muted'])
            return

        inline, cell, sx, top = self.geometry(self.width())

        # 主升顺序 (并排时与序列的字母行对齐)
        x = PAD
        y = top + 20 + (cell - self.BADGE) / 2 if inline else TITLE_H
        for i, key in enumerate(order):
            if i:
                drawChevron(p, QPointF(x + self.ORDER_GAP / 2, y + self.BADGE / 2),
                            pal['muted'])
                x += self.ORDER_GAP
            self.drawBadge(p, pal, QRectF(x, y, self.BADGE, self.BADGE), key, 15)
            x += self.BADGE

        # 逐级序列: 上面等级号, 下面字母; 没升到的级数画空格
        for level in range(self.LEVELS):
            cx = sx + level * (cell + self.CELL_GAP)
            drawText(p, QRectF(cx - 4, top, cell + 8, 16), str(level + 1), numberFont(12, 400),
                     pal['muted'], Qt.AlignCenter)
            rect = QRectF(cx, top + 20, cell, cell)
            if level < len(sequence):
                self.drawBadge(p, pal, rect, sequence[level], 13)
            else:
                p.setPen(QPen(pal['hairline'], 1))
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 5, 5)


class RunesCard(BuildCard):
    """符文页: 主系 (基石 + 3 行) | 副系 (3 行) | 属性碎片 (有数据时); 选中全彩, 未选去色变淡"""

    HEADER = 26
    KEY, RUNE, SHARD = 42, 28, 22
    ROW_GAP = 12
    COL_GAP = 48

    def __init__(self, parent=None):
        super().__init__(parent)
        self.title = self.tr("Runes")
        self.page = None
        self.refresh()

    def setPage(self, page):
        self.page = page
        self.refresh()

    def rowHeights(self):
        return [self.KEY, self.RUNE, self.RUNE, self.RUNE]

    def contentHeight(self, width):
        top = TITLE_H + self.HEADER + 12
        return int(top + sum(self.rowHeights()) + 3 * self.ROW_GAP + PAD + 4)

    @staticmethod
    def rowWidth(n, size, gap):
        return n * size + max(n - 1, 0) * gap

    def columnWidth(self, tree, sizes):
        metrics = QFontMetrics(textFont(13, True))
        header = 24 + 8 + metrics.horizontalAdvance(tree.get('name', ''))
        rows = [self.rowWidth(len(row), size, gap) for row, (size, gap) in zip(tree['rows'], sizes)]
        return max([header] + rows)

    def drawTree(self, p, pal, x, tree, sizes, rowTops, colW, headerTop=None):
        # 系别图标 + 名字 (默认在顶部; 副系放在主系基石那一行的位置, 同 OP.GG, 不留空行)
        icon = pixmap(tree.get('icon'))
        top = TITLE_H if headerTop is None else headerTop
        if not icon.isNull():
            rect = QRectF(x, top + 1, 24, 24)
            p.drawPixmap(rect.toRect(), icon)
            self.hits.append((rect.toRect(), tooltipHtml(tree.get('name', ''))))
        drawText(p, QRectF(x + 32, top, colW, self.HEADER), tree.get('name', ''),
                 textFont(13, True), pal['ink'])

        ring = STYLE_COLORS.get(tree.get('id'), pal['gold'])
        for row, (size, gap), y in zip(tree['rows'], sizes, rowTops):
            rowW = self.rowWidth(len(row), size, gap)
            rx = x + (colW - rowW) / 2
            for i, rune in enumerate(row):
                rect = QRectF(rx + i * (size + gap), y, size, size)
                self.drawRune(p, pal, rect, rune, ring, size >= self.KEY)
                self.hits.append((rect.toRect(), tooltipHtml(rune['name'], rune.get('desc', ''))))

    def drawRune(self, p, pal, rect, rune, ring, keystone):
        if rune['selected']:
            if not keystone:
                # 普通符文: 系别色圆底 + 描边, 图标略缩
                p.setPen(QPen(ring, 1.5))
                p.setBrush(tint(ring, 40))
                p.drawEllipse(rect.adjusted(0.75, 0.75, -0.75, -0.75))
                rect = rect.adjusted(3, 3, -3, -3)
            p.drawPixmap(rect.toRect(), pixmap(rune['icon']))
        else:
            p.setOpacity(0.36 if isDarkTheme() else 0.5)
            p.drawPixmap(rect.adjusted(2, 2, -2, -2).toRect() if not keystone else rect.toRect(),
                         grayPixmap(rune['icon'], not isDarkTheme()))
            p.setOpacity(1)

    def drawContent(self, p, pal):
        page = self.page
        if not page:
            return

        heights = self.rowHeights()
        top = TITLE_H + self.HEADER + 12
        rowTops = [top + sum(heights[:i]) + i * self.ROW_GAP for i in range(4)]

        primarySizes = [(self.KEY, 10), (self.RUNE, 16), (self.RUNE, 16), (self.RUNE, 16)]
        x = PAD
        columns = [(page['primary'], primarySizes, rowTops, None)]
        if page.get('secondary'):
            # 副系 3 行与主系的 3 行普通符文对齐; 系别图标 + 名字放在基石行的位置
            keystoneMiddle = rowTops[0] + (self.KEY - self.HEADER) / 2
            columns.append((page['secondary'], primarySizes[1:], rowTops[1:], keystoneMiddle))
        if page.get('shards'):
            shardTree = {'id': None, 'name': self.tr("Shards"), 'icon': None,
                         'rows': page['shards']}
            shardTops = [rowTops[1] + i * (self.SHARD + 12) - 2 for i in range(3)]
            columns.append((shardTree, [(self.SHARD, 10)] * 3, shardTops, None))

        for i, (tree, sizes, tops, headerTop) in enumerate(columns):
            colW = self.columnWidth(tree, sizes)
            if i:
                p.setPen(QPen(pal['hairline'], 1))
                lineX = x - self.COL_GAP / 2
                p.drawLine(QPointF(lineX, TITLE_H + 2), QPointF(lineX, self.height() - PAD - 2))
            self.drawTree(p, pal, x, tree, sizes, tops, colW, headerTop)
            x += colW + self.COL_GAP


# ---------------------------------------------------------------- 视图 ----

class MatchBuildView(QWidget):
    """
    出装视图: 提示卡 (加载中 / 无出装数据) + 物品时间线 + 技能加点 + 符文页, 竖排.
    数据为 app.lol.tools.loadGameBuildData 的返回值
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.data = None
        self.participantId = None

        self.vBoxLayout = QVBoxLayout(self)
        self.vBoxLayout.setContentsMargins(0, 0, 0, 0)
        self.vBoxLayout.setSpacing(12)
        self.messageCard = MessageCard()
        self.itemCard = ItemBuildCard()
        self.skillCard = SkillOrderCard()
        self.runesCard = RunesCard()
        for card in (self.messageCard, self.itemCard, self.skillCard, self.runesCard):
            self.vBoxLayout.addWidget(card)
            card.setVisible(False)

    def setLoading(self):
        self.data = None
        for card in (self.itemCard, self.skillCard, self.runesCard):
            card.setVisible(False)
        self.messageCard.setMessage(self.tr("Loading build data..."), quiet=True)
        self.messageCard.setVisible(True)

    def setData(self, data, participantId):
        self.data = data or {'builds': {}, 'itemIcons': {}, 'hasTimeline': False}
        self.showPlayer(participantId)

    def showPlayer(self, participantId):
        self.participantId = participantId
        if self.data is None:
            return

        build = self.data['builds'].get(participantId) or {}
        timeline = self.data.get('hasTimeline') and build.get('itemGroups') is not None
        runes = build.get('runes')

        if timeline:
            self.itemCard.setGroups(build['itemGroups'], self.data.get('itemIcons'))
            self.skillCard.setSkills(build.get('skills'))
            self.messageCard.setVisible(False)
        else:
            detail = self.tr("The client's match timeline has no item purchases or skill "
                             "level-ups.") if runes else ""
            self.messageCard.setMessage(self.tr("Build data isn't available for this game."),
                                        detail)
            self.messageCard.setVisible(True)

        self.itemCard.setVisible(bool(timeline))
        self.skillCard.setVisible(bool(timeline))
        if runes:
            self.runesCard.setPage(runes)
        self.runesCard.setVisible(bool(runes))
