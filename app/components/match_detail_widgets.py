"""
对局详情 (搜索页右侧) 的 OP.GG 风格控件: 每队一张表 + 两队之间的目标 / 击杀 / 经济对比条.
每行整行自绘 (同战绩行的做法), 物品详情 / 段位 / 评分说明 / 点名字打开生涯靠命中测试.
"""
from PySide6.QtCore import Qt, QRect, QRectF, QEvent
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QFont, QFontMetrics, QPixmap
from PySide6.QtWidgets import QFrame, QVBoxLayout, QWidget, QSizePolicy, QToolTip

from app.common.qfluentwidgets import isDarkTheme
from app.common.signals import signalBus
from app.common.style_sheet import colorManager
from app.components.match_widgets import itemTooltipHtml, rankEmblemPath, ordinal, TAG_COLORS

WIN_COLOR = QColor(83, 131, 232)
LOSS_COLOR = QColor(232, 64, 87)
DEALT_COLOR = QColor(232, 64, 87)

PAD = 10
ICONS_W = 32 + 4 + 15 + 3 + 15
SCORE_W, KDA_W, DMG_W, WARDS_W, CS_W = 80, 100, 124, 64, 64
ITEM, ITEM_GAP = 21, 1
ITEMS_W = 7 * ITEM + 6 * ITEM_GAP
FIXED_W = PAD + ICONS_W + 8 + SCORE_W + KDA_W + DMG_W + WARDS_W + CS_W + ITEMS_W + PAD
NAME_MIN_W = 110
TABLE_MIN_W = FIXED_W + NAME_MIN_W


def columns(width):
    """各列的 (x, w); 名字列吃掉多余宽度"""
    nameW = max(width - FIXED_W, NAME_MIN_W)
    x = PAD
    cols = {'icons': (x, ICONS_W)}
    x += ICONS_W + 8
    for key, w in (('name', nameW), ('score', SCORE_W), ('kda', KDA_W), ('damage', DMG_W),
                   ('wards', WARDS_W), ('cs', CS_W), ('items', ITEMS_W)):
        cols[key] = (x, w)
        x += w
    return cols


def font(size, bold=False):
    f = QFont(['Segoe UI', 'Microsoft YaHei'])
    f.setPixelSize(size)
    f.setBold(bold)
    return f


def themeColors():
    dark = isDarkTheme()
    fg = QColor(255, 255, 255) if dark else QColor(0, 0, 0)
    sub = QColor(255, 255, 255, 153) if dark else QColor(0, 0, 0, 153)
    return dark, fg, sub


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


def shortNumber(n):
    return f"{n:,}"


def drawRounded(p, pixmap, rect, radius):
    if pixmap is None or pixmap.isNull():
        return
    p.save()
    clip = QPainterPath()
    clip.addRoundedRect(rect, radius, radius)
    p.setClipPath(clip)
    p.drawPixmap(rect.toRect(), pixmap)
    p.restore()


class DetailTeamHeader(QWidget):
    """表头: 'Victory (Blue team)' + 各列标题"""

    HEIGHT = 30

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(self.HEIGHT)
        self.win = None
        self.teamName = ""

    def setTeam(self, win, teamName):
        self.win, self.teamName = win, teamName
        self.update()

    def paintEvent(self, e):
        _, fg, sub = themeColors()
        p = QPainter(self)
        p.setRenderHint(QPainter.TextAntialiasing)
        cols = columns(self.width())

        x, w = cols['icons']
        result = self.tr("Victory") if self.win else self.tr("Defeat")
        p.setFont(font(13, True))
        p.setPen(WIN_COLOR if self.win else LOSS_COLOR)
        p.drawText(QRectF(x, 0, 200, self.HEIGHT), Qt.AlignLeft | Qt.AlignVCenter, result)
        rw = QFontMetrics(font(13, True)).horizontalAdvance(result)
        p.setFont(font(12))
        p.setPen(sub)
        p.drawText(QRectF(x + rw + 6, 0, 200, self.HEIGHT), Qt.AlignLeft | Qt.AlignVCenter,
                   f"({self.teamName})")

        p.setFont(font(12))
        for key, title in (('score', self.tr("Score")), ('kda', "KDA"),
                           ('damage', self.tr("Damage")), ('wards', self.tr("Wards")),
                           ('cs', "CS"), ('items', self.tr("Items"))):
            cx, cw = cols[key]
            p.drawText(QRectF(cx, 0, cw, self.HEIGHT), Qt.AlignCenter, title)
        p.end()


class DetailPlayerRow(QWidget):
    """
    一名玩家 (OP.GG 详情表的一行):

    [英雄 等级][技能][符文] 名字 / 段位 | 9.9 [MVP] | 14/4/8 (63%) 5.50:1 |
    伤害 / 承伤 (带条) | 控制守卫 · 插 / 排 | CS · 每分 | 物品 x 7
    """

    HEIGHT = 46

    def __init__(self, summoner: dict, maxDealt: int, maxTaken: int, durationSecs: int,
                 parent=None):
        super().__init__(parent)
        self.setFixedHeight(self.HEIGHT)
        self.setMouseTracking(True)

        self.s = summoner
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
        self.items = [(pix(icon), itemId)
                      for icon, itemId in zip(summoner.get('itemIcons', []), itemIds)]
        self.emblem = QPixmap(rankEmblemPath(summoner.get('tierKey'))) \
            if summoner.get('tierKey') else QPixmap()

        self.itemRects, self.nameRect, self.rankRect, self.scoreRect = [], QRect(), QRect(), QRect()

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
                    tip += f" · {self.s['lp']} LP"
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
        dark, fg, sub = themeColors()
        s, h = self.s, self.HEIGHT
        cols = columns(self.width())

        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform
                         | QPainter.TextAntialiasing)

        def text(rect, string, f, color, align=Qt.AlignLeft | Qt.AlignVCenter):
            p.setFont(f)
            p.setPen(color)
            p.drawText(rect, align, string)

        if s.get('isCurrent'):
            # 当前玩家整行高亮
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 28) if dark else QColor(0, 0, 0, 18))
            p.drawRect(self.rect())

        # 英雄 + 等级角标
        x, _ = cols['icons']
        icon = QRectF(x, (h - 32) / 2, 32, 32)
        if not self.championPixmap.isNull():
            p.save()
            clip = QPainterPath()
            clip.addEllipse(icon)
            p.setClipPath(clip)
            p.drawPixmap(icon.toRect(), self.championPixmap)
            p.restore()
        level = QRectF(icon.left() - 3, icon.bottom() - 13, 16, 16)
        p.setPen(QPen(QColor(120, 90, 40), 1))
        p.setBrush(QColor(1, 10, 19) if dark else QColor(249, 249, 249))
        p.drawEllipse(level)
        text(level, str(s.get('champLevel', '')), font(10, True),
             QColor(200, 190, 170) if dark else QColor(1, 10, 19), Qt.AlignCenter)

        # 技能 / 符文
        sx = icon.right() + 4
        for i, spell in enumerate(self.spells):
            drawRounded(p, spell, QRectF(sx, h / 2 - 16 + i * 17, 15, 15), 3)
        rx = sx + 15 + 3
        if not self.rune.isNull():
            p.drawPixmap(QRectF(rx, h / 2 - 16, 15, 15).toRect(), self.rune)
        if not self.subStyle.isNull():
            p.drawPixmap(QRectF(rx + 1, h / 2 + 2, 13, 13).toRect(), self.subStyle)

        # 名字 + 段位
        nx, nw = cols['name']
        name = s.get('summonerName') or '?'
        nameFont = font(13, bool(s.get('isCurrent')))
        name = QFontMetrics(nameFont).elidedText(name, Qt.ElideRight, int(nw - 6))
        text(QRectF(nx, h / 2 - 19, nw - 6, 19), name, nameFont,
             WIN_COLOR if s.get('isCurrent') else fg)
        self.nameRect = QRect(int(nx), int(h / 2 - 19),
                              QFontMetrics(nameFont).horizontalAdvance(name) + 2, 19)
        rank = tierText(s.get('tierKey'), s.get('division'))
        if rank:
            drawRounded(p, self.emblem, QRectF(nx - 2, h / 2 + 1, 18, 18), 0)
            text(QRectF(nx + 17, h / 2, nw - 23, 18), rank, font(12), sub)
            self.rankRect = QRect(int(nx), int(h / 2), int(nw), 18)
        else:
            self.rankRect = QRect()

        # 评分 + 名次 / MVP / SVP
        cx, cw = cols['score']
        if s.get('scoreRank'):
            scoreText = f"{s['score']:.1f}"
            sf = font(13, True)
            badge = s.get('badge')
            tag = badge or ordinal(s['scoreRank'])
            tagColor = QColor(TAG_COLORS[badge] if badge else TAG_COLORS['rank'])
            pf = font(10, True)
            tagW = QFontMetrics(pf).horizontalAdvance(tag) + 12
            total = QFontMetrics(sf).horizontalAdvance(scoreText) + 5 + tagW
            x0 = cx + (cw - total) / 2
            text(QRectF(x0, 0, 40, h), scoreText, sf, fg)
            pill = QRectF(x0 + total - tagW, h / 2 - 8, tagW, 16)
            p.setPen(Qt.NoPen)
            p.setBrush(tagColor)
            p.drawRoundedRect(pill, 8, 8)
            text(pill, tag, pf, QColor(255, 255, 255), Qt.AlignCenter)
            self.scoreRect = QRect(int(cx), 0, cw, h)

        # KDA
        kx, kw = cols['kda']
        kp = s.get('kp')
        line = f"{s['kills']}/{s['deaths']}/{s['assists']}"
        if kp is not None:
            line += f" ({round(kp * 100)}%)"
        text(QRectF(kx, h / 2 - 18, kw, 18), line, font(12), fg, Qt.AlignCenter)
        kda = (s['kills'] + s['assists']) / max(s['deaths'], 1)
        text(QRectF(kx, h / 2, kw, 18), f"{kda:.2f}:1", font(12, True),
             kdaColor(kda) or sub, Qt.AlignCenter)

        # 伤害 / 承伤
        dx, dw = cols['damage']
        half = (dw - 8) / 2
        track = QColor(255, 255, 255, 40) if dark else QColor(0, 0, 0, 30)
        for i, (value, maxValue, color) in enumerate(
                ((s.get('demage', 0), self.maxDealt, DEALT_COLOR),
                 (s.get('damageTaken', 0), self.maxTaken, QColor(140, 140, 140)))):
            bx = dx + i * (half + 8)
            text(QRectF(bx, h / 2 - 18, half, 16), shortNumber(value), font(11), sub,
                 Qt.AlignCenter)
            bar = QRectF(bx + 4, h / 2 + 2, half - 8, 6)
            p.setPen(Qt.NoPen)
            p.setBrush(track)
            p.drawRect(bar)
            p.setBrush(color)
            p.drawRect(QRectF(bar.left(), bar.top(), bar.width() * value / maxValue, bar.height()))

        # 视野
        wx, ww = cols['wards']
        text(QRectF(wx, h / 2 - 18, ww, 18), str(s.get('controlWards', 0)), font(12), fg,
             Qt.AlignCenter)
        text(QRectF(wx, h / 2, ww, 18), f"{s.get('wardsPlaced', 0)} / {s.get('wardsKilled', 0)}",
             font(12), sub, Qt.AlignCenter)

        # 补刀
        csx, csw = cols['cs']
        text(QRectF(csx, h / 2 - 18, csw, 18), str(s.get('cs', 0)), font(12), fg,
             Qt.AlignCenter)
        if self.minutes >= 1:
            text(QRectF(csx, h / 2, csw, 18), f"{s.get('cs', 0) / self.minutes:.1f}/m",
                 font(12), sub, Qt.AlignCenter)

        # 物品
        ix, _ = cols['items']
        self.itemRects = []
        for i, (pixmap, itemId) in enumerate(self.items):
            rect = QRectF(ix + i * (ITEM + ITEM_GAP), (h - ITEM) / 2, ITEM, ITEM)
            drawRounded(p, pixmap, rect, 3)
            self.itemRects.append((rect.toRect(), itemId))

        p.end()


class DetailTeamTable(QFrame):
    """一队: 表头 + 5 行玩家, 背景为胜 / 负配色"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(TABLE_MIN_W)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.vBoxLayout = QVBoxLayout(self)
        self.vBoxLayout.setContentsMargins(0, 2, 0, 4)
        self.vBoxLayout.setSpacing(0)
        self.header = DetailTeamHeader()
        self.vBoxLayout.addWidget(self.header)
        self.rows = []
        self.win = None

    def updateTeam(self, team, teamName, maxDealt, maxTaken, durationSecs):
        for row in self.rows:
            self.vBoxLayout.removeWidget(row)
            row.deleteLater()

        self.win = team.get('win') == 'Win'
        self.header.setTeam(self.win, teamName)
        self.rows = [DetailPlayerRow(s, maxDealt, maxTaken, durationSecs)
                     for s in team['summoners']]
        for row in self.rows:
            self.vBoxLayout.addWidget(row)
        self.update()

    def paintEvent(self, e):
        c = colorManager.getColor('win' if self.win else 'lose')[0]
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 6, 6)
        p.end()


class TeamCompareBar(QWidget):
    """
    两队之间: 左右两侧为各自的目标 (塔 / 水晶 / 男爵 / 小龙 / 先锋), 中间为总击杀 / 总经济对比条
    """

    HEIGHT = 64

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(self.HEIGHT)
        self.setMinimumWidth(TABLE_MIN_W)
        self.teams = None
        self.icons = {}

    def setTeams(self, team1, team2):
        self.teams = (team1, team2)
        self.icons = {}
        for team in self.teams:
            for key in ('towerIcon', 'inhibitorIcon', 'baronIcon', 'dragonIcon',
                        'riftHeraldIcon'):
                path = team.get(key)
                if path and path not in self.icons:
                    self.icons[path] = QPixmap(path)
        self.update()

    def paintEvent(self, e):
        if not self.teams:
            return

        _, fg, sub = themeColors()
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform
                         | QPainter.TextAntialiasing)
        w, h = self.width(), self.HEIGHT
        team1, team2 = self.teams
        color1 = WIN_COLOR if team1.get('win') == 'Win' else LOSS_COLOR
        color2 = WIN_COLOR if team2.get('win') == 'Win' else LOSS_COLOR

        # 目标
        def objectives(team, x, alignRight):
            entries = [('towerIcon', 'towerKills'), ('inhibitorIcon', 'inhibitorKills'),
                       ('baronIcon', 'baronKills'), ('dragonIcon', 'dragonKills'),
                       ('riftHeraldIcon', 'riftHeraldKills')]
            step = 38
            if alignRight:
                x -= len(entries) * step
            for i, (icon, count) in enumerate(entries):
                ex = x + i * step
                pixmap = self.icons.get(team.get(icon))
                if pixmap and not pixmap.isNull():
                    p.drawPixmap(QRectF(ex, h / 2 - 9, 18, 18).toRect(), pixmap)
                p.setFont(font(12))
                p.setPen(fg)
                p.drawText(QRectF(ex + 20, h / 2 - 10, 18, 20), Qt.AlignLeft | Qt.AlignVCenter,
                           str(team.get(count, 0)))

        objectives(team1, PAD, False)
        objectives(team2, w - PAD, True)

        # 总击杀 / 总经济
        barX, barW = PAD + 5 * 38 + 16, w - 2 * (PAD + 5 * 38 + 16)
        for i, (label, key) in enumerate(((self.tr("Total kills"), 'kills'),
                                          (self.tr("Total gold"), 'gold'))):
            v1, v2 = team1.get(key, 0), team2.get(key, 0)
            rect = QRectF(barX, 8 + i * 26, barW, 20)
            split = rect.width() * v1 / max(v1 + v2, 1)
            p.setPen(Qt.NoPen)
            p.setBrush(color1)
            p.drawRect(QRectF(rect.left(), rect.top(), split, rect.height()))
            p.setBrush(color2)
            p.drawRect(QRectF(rect.left() + split, rect.top(), rect.width() - split,
                              rect.height()))
            p.setFont(font(11, True))
            p.setPen(QColor(255, 255, 255))
            inner = rect.adjusted(6, 0, -6, 0)
            p.drawText(inner, Qt.AlignLeft | Qt.AlignVCenter, f"{v1:,}")
            p.drawText(inner, Qt.AlignRight | Qt.AlignVCenter, f"{v2:,}")
            p.drawText(inner, Qt.AlignCenter, label)

        p.end()


def teamMaxima(teams):
    """两队所有玩家中的最大输出 / 最大承伤 (伤害条的满格)"""
    summoners = [s for team in teams for s in team['summoners']]
    return (max((s.get('demage', 0) for s in summoners), default=1),
            max((s.get('damageTaken', 0) for s in summoners), default=1))
