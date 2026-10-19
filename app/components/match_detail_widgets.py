"""
对局详情 (搜索页右侧) 的 OP.GG 风格记分表: 每队一张表 + 两队之间的目标 / 击杀 / 经济对比条.

布局: 名字列紧凑 (名字 + 段位, 封顶 NAME_W), 多出来的宽度平均分给各数据列, 表头标题与行内数值
用同一个 columns() 居中对齐. 每行整行自绘并缓存成 pixmap (同战绩行), 物品详情 / 段位 / 评分说明 /
点名字打开生涯靠命中测试. 颜色与字体见 design_tokens.py.
"""
from PySide6.QtCore import Qt, QRect, QRectF, QPointF, QEvent
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QFontMetrics, QPixmap
from PySide6.QtWidgets import QFrame, QVBoxLayout, QWidget, QSizePolicy, QToolTip

from app.common.signals import signalBus
from app.components.design_tokens import numberFont, textFont, resultColor, tint, deathsColor
from app.components.match_widgets import itemTooltipHtml, rankEmblemPath, ordinal, TAG_COLORS
from app.components.scoreboard import (cachedContent, drawText, drawRuns, runsWidth,
                                       drawRoundedPixmap, drawChampion, drawItemSlot, drawTag,
                                       drawResultCard, stripeColor)

STRIPE = 4                      # 左侧结果色边条
PAD_L, PAD_R = STRIPE + 10, 10
CHAMP, SMALL = 32, 16
ICONS_W = CHAMP + 4 + SMALL + 2 + SMALL
ICONS_GAP = 10
NAME_MIN_W, NAME_W = 104, 150   # 名字列: 不够宽时先缩它 (省略号), 够宽时封顶
ITEM, ITEM_GAP = 22, 2
ITEMS_W = 7 * ITEM + 6 * ITEM_GAP
STAT_COLUMNS = (('score', 64), ('kda', 94), ('damage', 116), ('wards', 54), ('cs', 54))
FIXED_W = PAD_L + ICONS_W + ICONS_GAP + ITEMS_W + PAD_R
TABLE_MIN_W = FIXED_W + NAME_MIN_W + sum(w for _, w in STAT_COLUMNS)


def columns(width):
    """
    各列的 (x, w). 名字列在 NAME_MIN_W .. NAME_W 之间, 数据列 (评分 / KDA / 伤害 / 视野 / 补刀)
    平分剩下的宽度, 物品列定宽贴右
    """
    statsMin = sum(w for _, w in STAT_COLUMNS)
    nameW = max(NAME_MIN_W, min(NAME_W, width - FIXED_W - statsMin))
    extra = max(width - FIXED_W - nameW - statsMin, 0) / len(STAT_COLUMNS)

    x = PAD_L
    cols = {'icons': (x, ICONS_W)}
    x += ICONS_W + ICONS_GAP
    cols['name'] = (x, nameW)
    x += nameW
    for key, w in STAT_COLUMNS:
        cols[key] = (x, w + extra)
        x += w + extra
    cols['items'] = (x, ITEMS_W)
    return cols


def tierText(tier, division):
    if not tier or tier in ('NONE', 'UNRANKED'):
        return ""
    division = "" if division in ("", "NA", None) else division
    return f"{tier.title()} {division}".strip()


def kdaColor(kda):
    if 3 <= kda < 4:
        return QColor(0, 163, 80)
    elif 4 <= kda < 5:
        return QColor(0, 147, 255)
    elif 5 <= kda:
        return QColor(240, 111, 0)
    return None


class DetailTeamHeader(QWidget):
    """表头: 'Victory (Blue team)' + 各列标题 (与行内数值同列居中)"""

    HEIGHT = 32

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(self.HEIGHT)
        self.type = 'lose'
        self.teamName = ""
        self._cache = self._cacheKey = None

    def setTeam(self, type, teamName):
        self.type, self.teamName = type, teamName
        self._cacheKey = None
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.drawPixmap(0, 0, cachedContent(self, self.__draw))
        p.end()

    def __draw(self, p, pal):
        h = self.HEIGHT
        cols = columns(self.width())

        x, _ = cols['icons']
        result = {'win': self.tr("Victory"), 'lose': self.tr("Defeat"),
                  'remake': self.tr("Remake")}[self.type]
        end = drawRuns(p, x, 0, h, ((result, textFont(13, True), stripeColor(self.type, pal)),))
        drawText(p, QRectF(end + 6, 0, 200, h), f"({self.teamName})", textFont(12), pal['muted'])

        for key, title in (('score', self.tr("Score")), ('kda', "KDA"),
                           ('damage', self.tr("Damage")), ('wards', self.tr("Wards")),
                           ('cs', "CS"), ('items', self.tr("Items"))):
            cx, cw = cols[key]
            drawText(p, QRectF(cx, 0, cw, h), title, textFont(12), pal['muted'], Qt.AlignCenter)


class DetailPlayerRow(QWidget):
    """
    一名玩家 (记分表的一行), 整行自绘并缓存:

    [英雄 等级][技能][符文] 名字 / 段位 | 9.9 / MVP | 14/4/8 (63%) / 5.50:1 |
    伤害 / 承伤 (带条) | 控制守卫 / 插 · 排 | CS / 每分 | 物品 x 7
    """

    HEIGHT = 46

    def __init__(self, summoner: dict, maxDealt: int, maxTaken: int, durationSecs: int,
                 teamType='win', parent=None):
        super().__init__(parent)
        self.setFixedHeight(self.HEIGHT)
        self.setMouseTracking(True)

        self.s = summoner
        self.teamType = teamType
        self.maxDealt = max(maxDealt, 1)
        self.maxTaken = max(maxTaken, 1)
        self.minutes = durationSecs / 60 if durationSecs else 0

        def pix(path):
            return QPixmap(path) if path else QPixmap()

        self.championPixmap = pix(summoner.get('championIcon'))
        self.spells = [pix(summoner.get('spell1Icon')), pix(summoner.get('spell2Icon'))]
        self.rune = pix(summoner.get('runeIcon'))
        self.subStyle = pix(summoner.get('subStyleIcon'))
        itemIds = summoner.get('itemIds') or [0] * len(summoner.get('itemIcons', []))
        self.items = [(pix(icon) if itemId else QPixmap(), itemId)
                      for icon, itemId in zip(summoner.get('itemIcons', []), itemIds)]
        self.emblem = QPixmap(rankEmblemPath(summoner.get('tierKey'))) \
            if summoner.get('tierKey') else QPixmap()

        # 命中区域, 绘制缓存时按当前宽度算出
        self.itemRects, self.nameRect, self.rankRect, self.scoreRect = [], QRect(), QRect(), QRect()
        self._cache = self._cacheKey = None

    # ---------------- 交互 ----------------

    def event(self, e):
        if e.type() == QEvent.ToolTip:
            pos = e.pos()
            for rect, itemId in self.itemRects:
                if rect.contains(pos):
                    html = itemTooltipHtml(itemId)
                    if html:
                        QToolTip.showText(e.globalPos(), html, self, rect)
                        return True
            if self.nameRect.contains(pos):
                QToolTip.showText(e.globalPos(), self.s.get('summonerName') or '', self,
                                  self.nameRect)
                return True
            if self.rankRect.contains(pos) and self.s.get('tierKey'):
                tip = tierText(self.s['tierKey'], self.s.get('division'))
                if self.s.get('lp') not in ("", None):
                    tip += f", {self.s['lp']} LP"
                QToolTip.showText(e.globalPos(), tip, self, self.rankRect)
                return True
            if self.scoreRect.contains(pos) and self.s.get('scoreRank'):
                QToolTip.showText(e.globalPos(), self.tr(
                    "Seraphine score (0-10): kill participation, damage, KDA, damage taken, "
                    "gold and vision vs. the other 9 players.\n"
                    "MVP: best on the winning team, SVP: best on the losing team"),
                    self, self.scoreRect)
                return True
            QToolTip.hideText()
            e.ignore()
            return True

        return super().event(e)

    def mouseMoveEvent(self, e):
        onName = self.nameRect.contains(e.position().toPoint()) and self.s.get('puuid')
        self.setCursor(Qt.PointingHandCursor if onName else Qt.ArrowCursor)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self.nameRect.contains(e.position().toPoint()) and self.s.get('puuid'):
            signalBus.toCareerInterface.emit(self.s['puuid'])
            e.accept()
            return
        super().mouseReleaseEvent(e)

    # ---------------- 绘制 ----------------

    def paintEvent(self, e):
        p = QPainter(self)
        p.drawPixmap(0, 0, cachedContent(self, self.__draw, (self.teamType,)))
        p.end()

    def __draw(self, p, pal):
        s, w, h = self.s, self.width(), self.HEIGHT
        ink, muted = pal['ink'], pal['muted']
        cols = columns(w)
        top, bottom = QRectF(0, h / 2 - 19, 0, 18), QRectF(0, h / 2 + 1, 0, 18)

        def line(rect, x, width):
            return QRectF(x, rect.top(), width, rect.height())

        if s.get('isCurrent'):
            # 当前玩家: 整行加深结果色底
            p.fillRect(QRectF(STRIPE, 0, w - STRIPE - 1, h),
                       tint(stripeColor(self.teamType, pal), 34))

        # 英雄 (金边) + 等级角标
        x, _ = cols['icons']
        icon = QRectF(x, (h - CHAMP) / 2, CHAMP, CHAMP)
        drawChampion(p, self.championPixmap, icon, pal['gold'], 1.5, crop=3)
        level = QRectF(icon.left() - 3, icon.bottom() - 13, 16, 16)
        p.setPen(QPen(pal['gold'], 1))
        p.setBrush(pal['sheet'])
        p.drawEllipse(level)
        drawText(p, level, str(s.get('champLevel', '')), numberFont(10), ink, Qt.AlignCenter)

        # 技能 (竖排) + 基石 / 副系符文
        sx = icon.right() + 4
        for i, spell in enumerate(self.spells):
            drawRoundedPixmap(p, spell, QRectF(sx, h / 2 - 17 + i * 18, SMALL, SMALL), 3)
        rx = sx + SMALL + 2
        if not self.rune.isNull():
            p.drawPixmap(QRectF(rx, h / 2 - 17, SMALL, SMALL).toRect(), self.rune)
        if not self.subStyle.isNull():
            p.drawPixmap(QRectF(rx + 2, h / 2 + 3, 12, 12).toRect(), self.subStyle)

        # 名字 + 段位 (没有段位时名字居中)
        nx, nw = cols['name']
        rank = tierText(s.get('tierKey'), s.get('division'))
        nameFont = textFont(13, bool(s.get('isCurrent')))
        metrics = QFontMetrics(nameFont)
        name = metrics.elidedText(s.get('summonerName') or '?', Qt.ElideRight, int(nw - 8))
        nameBox = QRectF(nx, h / 2 - 20, nw - 8, 20) if rank else QRectF(nx, 0, nw - 8, h)
        drawText(p, nameBox, name, nameFont, ink)
        self.nameRect = QRect(int(nx), int(nameBox.center().y() - 10),
                              metrics.horizontalAdvance(name) + 2, 20)
        if rank:
            drawRoundedPixmap(p, self.emblem, QRectF(nx - 2, h / 2 + 1, 18, 18), 0)
            drawText(p, QRectF(nx + 18, h / 2 + 1, nw - 26, 18), rank, textFont(12), muted)
            self.rankRect = QRect(int(nx), int(h / 2 + 1), int(nw - 8), 18)
        else:
            self.rankRect = QRect()

        # 评分 + 名次 / MVP / SVP (竖排)
        cx, cw = cols['score']
        if s.get('scoreRank'):
            drawText(p, line(top, cx, cw), f"{s['score']:.1f}", numberFont(15), ink,
                     Qt.AlignCenter)
            badge = s.get('badge')
            tag = badge or ordinal(s['scoreRank'])
            tagFont = textFont(11, True)
            tagW = QFontMetrics(tagFont).horizontalAdvance(tag) + 14
            drawTag(p, QRectF(cx + (cw - tagW) / 2, h / 2 + 2, tagW, 16), tag,
                    TAG_COLORS[badge] if badge else TAG_COLORS['rank'], tagFont, pal)
            self.scoreRect = QRect(int(cx), 0, int(cw), h)
        else:
            self.scoreRect = QRect()

        # K / D / A (参团率) + KDA
        kx, kw = cols['kda']
        bold, thin = numberFont(13), numberFont(13, 300)
        runs = [(str(s['kills']), bold, ink), ("/", thin, muted),
                (str(s['deaths']), bold, deathsColor()), ("/", thin, muted),
                (str(s['assists']), bold, ink)]
        if s.get('kp') is not None:
            runs.append((f" ({round(s['kp'] * 100)}%)", numberFont(12, 400), muted))
        drawRuns(p, kx + (kw - runsWidth(runs)) / 2, top.top(), top.height(), runs)
        kda = (s['kills'] + s['assists']) / max(s['deaths'], 1)
        drawText(p, line(bottom, kx, kw), f"{kda:.2f}:1", numberFont(12),
                 kdaColor(kda) or muted, Qt.AlignCenter)

        # 伤害 / 承伤: 数字 + 条
        dx, dw = cols['damage']
        half = dw / 2
        for i, (value, maxValue, color, textColor) in enumerate(
                ((s.get('demage', 0), self.maxDealt, resultColor('lose'), ink),
                 (s.get('damageTaken', 0), self.maxTaken, muted, muted))):
            bx = dx + i * half
            drawText(p, line(top, bx, half), f"{value:,}", numberFont(12, 400), textColor,
                     Qt.AlignCenter)
            barW = min(half - 12, 64)
            bar = QRectF(bx + (half - barW) / 2, h / 2 + 5, barW, 6)
            p.setPen(Qt.NoPen)
            p.setBrush(pal['hairline'])
            p.drawRoundedRect(bar, 3, 3)
            if value:
                p.setBrush(color)
                p.drawRoundedRect(QRectF(bar.left(), bar.top(),
                                         max(bar.width() * value / maxValue, 3), bar.height()),
                                  3, 3)

        # 视野: 控制守卫 / 插眼 · 排眼
        wx, ww = cols['wards']
        drawText(p, line(top, wx, ww), str(s.get('controlWards', 0)), numberFont(13), ink,
                 Qt.AlignCenter)
        drawText(p, line(bottom, wx, ww), f"{s.get('wardsPlaced', 0)} / {s.get('wardsKilled', 0)}",
                 numberFont(12, 400), muted, Qt.AlignCenter)

        # 补刀 / 每分钟
        csx, csw = cols['cs']
        drawText(p, line(top, csx, csw), str(s.get('cs', 0)), numberFont(13), ink,
                 Qt.AlignCenter)
        if self.minutes >= 1:
            drawText(p, line(bottom, csx, csw), f"{s.get('cs', 0) / self.minutes:.1f}/m",
                     numberFont(12, 400), muted, Qt.AlignCenter)

        # 物品 (空格画发丝线框)
        ix, _ = cols['items']
        self.itemRects = []
        for i, (pixmap, itemId) in enumerate(self.items):
            rect = QRectF(ix + i * (ITEM + ITEM_GAP), (h - ITEM) / 2, ITEM, ITEM)
            drawItemSlot(p, pixmap, itemId, rect, 4, pal)
            if itemId:
                self.itemRects.append((rect.toRect(), itemId))


class DetailTeamTable(QFrame):
    """一队: 表头 + 5 行玩家; 纸面卡片 + 结果色浅底 + 左侧实色边条, 行间发丝线"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(TABLE_MIN_W)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.vBoxLayout = QVBoxLayout(self)
        self.vBoxLayout.setContentsMargins(0, 0, 0, 4)
        self.vBoxLayout.setSpacing(0)
        self.header = DetailTeamHeader()
        self.vBoxLayout.addWidget(self.header)
        self.rows = []
        self.type = 'lose'
        self._cache = self._cacheKey = None

    def updateTeam(self, team, teamName, maxDealt, maxTaken, durationSecs):
        for row in self.rows:
            self.vBoxLayout.removeWidget(row)
            row.deleteLater()

        self.type = 'win' if team.get('win') == 'Win' else 'lose'
        self.header.setTeam(self.type, teamName)
        self.rows = [DetailPlayerRow(s, maxDealt, maxTaken, durationSecs, self.type)
                     for s in team['summoners']]
        for row in self.rows:
            self.vBoxLayout.addWidget(row)
        self._cacheKey = None
        self.update()

    def paintEvent(self, e):
        rowTops = tuple(row.y() for row in self.rows)
        p = QPainter(self)
        p.drawPixmap(0, 0, cachedContent(self, self.__draw, (self.type,) + rowTops))
        p.end()

    def __draw(self, p, pal):
        drawResultCard(p, self.rect(), self.type, pal)
        p.setPen(QPen(pal['hairline'], 1))
        for y in [self.header.y() + self.header.height()] + [row.y() for row in self.rows[1:]]:
            p.drawLine(QPointF(STRIPE, y - 0.5), QPointF(self.width() - 1, y - 0.5))


class TeamCompareBar(QWidget):
    """
    两队之间: 左右两侧为各自的目标 (塔 / 水晶 / 男爵 / 小龙 / 先锋), 中间为总击杀 / 总经济对比条
    """

    HEIGHT = 64
    OBJECTIVES = (('towerIcon', 'towerKills'), ('inhibitorIcon', 'inhibitorKills'),
                  ('baronIcon', 'baronKills'), ('dragonIcon', 'dragonKills'),
                  ('riftHeraldIcon', 'riftHeraldKills'))
    STEP = 38

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(self.HEIGHT)
        self.setMinimumWidth(TABLE_MIN_W)
        self.teams = None
        self.icons = {}
        self._cache = self._cacheKey = None

    def setTeams(self, team1, team2):
        self.teams = (team1, team2)
        self.icons = {}
        for team in self.teams:
            for key, _ in self.OBJECTIVES:
                path = team.get(key)
                if path and path not in self.icons:
                    self.icons[path] = QPixmap(path)
        self._cacheKey = None
        self.update()

    def paintEvent(self, e):
        if not self.teams:
            return
        p = QPainter(self)
        p.drawPixmap(0, 0, cachedContent(self, self.__draw))
        p.end()

    def __draw(self, p, pal):
        w, h = self.width(), self.HEIGHT
        team1, team2 = self.teams
        color1 = resultColor('win' if team1.get('win') == 'Win' else 'lose')
        color2 = resultColor('win' if team2.get('win') == 'Win' else 'lose')

        # 目标
        def objectives(team, x, alignRight):
            if alignRight:
                x -= len(self.OBJECTIVES) * self.STEP - (self.STEP - 34)
            for i, (icon, count) in enumerate(self.OBJECTIVES):
                ex = x + i * self.STEP
                pixmap = self.icons.get(team.get(icon))
                if pixmap and not pixmap.isNull():
                    p.drawPixmap(QRectF(ex, h / 2 - 9, 18, 18).toRect(), pixmap)
                drawText(p, QRectF(ex + 21, h / 2 - 10, 16, 20), str(team.get(count, 0)),
                         numberFont(13), pal['ink'])

        objectives(team1, PAD_L, False)
        objectives(team2, w - PAD_R, True)

        # 总击杀 / 总经济
        side = PAD_L + len(self.OBJECTIVES) * self.STEP + 12
        barX, barW = side, w - 2 * side
        white = QColor(255, 255, 255)
        for i, (label, key) in enumerate(((self.tr("Total kills"), 'kills'),
                                          (self.tr("Total gold"), 'gold'))):
            v1, v2 = team1.get(key, 0), team2.get(key, 0)
            rect = QRectF(barX, 9 + i * 25, barW, 21)
            split = rect.width() * v1 / max(v1 + v2, 1)
            p.save()
            path = QPainterPath()
            path.addRoundedRect(rect, 4, 4)
            p.setClipPath(path)
            p.fillRect(QRectF(rect.left(), rect.top(), split, rect.height()), color1)
            p.fillRect(QRectF(rect.left() + split, rect.top(), rect.width() - split,
                              rect.height()), color2)
            p.restore()
            inner = rect.adjusted(8, 0, -8, 0)
            drawText(p, inner, f"{v1:,}", numberFont(13), white)
            drawText(p, inner, f"{v2:,}", numberFont(13), white, Qt.AlignRight | Qt.AlignVCenter)
            drawText(p, inner, label, textFont(12, True), white, Qt.AlignCenter)


def teamMaxima(teams):
    """两队所有玩家中的最大输出 / 最大承伤 (伤害条的满格)"""
    summoners = [s for team in teams for s in team['summoners']]
    return (max((s.get('demage', 0) for s in summoners), default=1),
            max((s.get('damageTaken', 0) for s in summoners), default=1))
