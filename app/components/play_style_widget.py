"""
Career page "Play style" view: season play-style statistics, one custom-painted card.

    Season 2026 · 93 games                                   [All 93] [Solo/Duo 59] [Flex 20]
    Bot · Marksman · Kalista 32%                               headline (clear-cut parts only)
    [Games] [Win rate] [KDA] [Play time] [Champion pool]          summary strip
    [Kill part.] [Damage/min] [CS/min] [Gold/min] [Vision/min]    per-minute strip
    [Positions              ] [Champion focus          ]
    [Sides      ] [Classes     ] [Recent score ]
    [Activity: weekday bars          |  hour-of-day bars          ]

Data: app/lol/play_style.buildProfile over the season sample the Performance panel already
fetched; the queue filter re-aggregates that sample (no requests). Champion icons are loaded
by the caller and handed over with setChampionIcons, never on the paint path.
Sections whose data is missing for the sample (no positions, no classes ...) are left out.

Win-rate colours follow the career page's activity card: win colour at >= 50%, loss colour
below, paler the closer to 50%; buckets with fewer than MIN_BUCKET_GAMES games are drawn
hollow / muted because their win rate is noise.
"""
import math
from datetime import datetime

from PySide6.QtCore import Qt, QRectF, QRect, QEvent
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QFontMetrics, QPixmap
from PySide6.QtWidgets import QFrame, QSizePolicy

from app.common.qfluentwidgets import isDarkTheme
from app.components.design_tokens import (numberFont, textFont, resultColor, tint,
                                          opScoreColor, palette)
from app.components.match_widgets import TAG_COLORS
from app.components.performance_widget import BETTER, WORSE, lengthText
from app.components.rich_tooltip import RichToolTip
from app.components.scoreboard import drawText, drawRuns, runsWidth, cachedContent, drawChampion
from app.lol import play_style

ICON_PX = 44
SMALL = play_style.MIN_BUCKET_GAMES


def pct(v):
    return f"{round(v * 100)}%" if v is not None else "--"


def compactNumber(v):
    return f"{v / 1000:.1f}k" if v >= 10000 else f"{round(v):,}"


def playTimeText(secs):
    hours, rest = divmod(int(secs), 3600)
    return f"{hours}h {rest // 60:02d}m" if hours else f"{rest // 60}m"


def winRateInk(rate, games, pal):
    """win rate text: win / loss colour split at 50% (as the career rows); small samples muted"""
    if rate is None or games < SMALL:
        return pal['muted']
    return resultColor('win' if rate >= 0.5 else 'lose')


def winRateFill(rate, games, pal):
    """
    bar fill, same rule as the career activity card: win / loss colour, paler the closer the
    win rate is to 50% (exactly even = palest win tint). None for small samples: drawn hollow
    """
    if rate is None or games < SMALL:
        return None
    color = resultColor('win' if rate >= 0.5 else 'lose')
    return tint(color, 100 + int(155 * min(abs(rate - 0.5) / 0.3, 1)))


class PlayStyleView(QFrame):
    MARGIN = 18
    TITLE_H = 50
    HEADLINE_H = 38
    GAP = 10
    STAT_H = 84
    FOCUS_H = 232
    SMALL_H = 160
    ACTIVITY_H = 212
    STATE_H = 150
    CHIP_H = 28

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.setMouseTracking(True)

        # 'loading' / 'ready' / 'empty'
        self.state = 'empty'
        self.games = []
        self.source = ""
        self.capped = False
        self.recentGames = []
        self.roles = {}
        self.profiles = {}
        self.queueId = None
        self.icons = {}
        self.names = {}

        self.tips = []  # [(QRect, text, isHelp)]
        self.chipRects = []  # [(QRect, queueId)]
        self._hover = -1
        self._chipHover = None  # queueId of the hovered chip, 'none' for no chip
        self._cache = None
        self._cacheKey = None
        self.setFixedHeight(self.STATE_H)

    # ---------------- data ----------------

    def setLoading(self, recentGames=None):
        """another summoner / refresh: wait for the season sample; the queue filter resets"""
        self.state = 'loading'
        self.games, self.profiles = [], {}
        self.recentGames = recentGames or []
        self.queueId = None
        self.icons, self.names = {}, {}
        self._relayout()

    def setEmpty(self):
        self.state = 'empty'
        self.games, self.profiles = [], {}
        self._relayout()

    def setSample(self, games, source="", capped=False, roles=None, recentGames=None,
                  names=None):
        """
        games: season_stats.fetchSeasonGames result (None / [] -> empty state);
        names: {championId: name} so the headline has names before the icons arrive
        """
        if names:
            self.names.update(names)
        self.games = games or []
        self.source = source
        self.capped = capped
        if roles is not None:
            self.roles = roles
        if recentGames is not None:
            self.recentGames = recentGames
        self.profiles = {q: play_style.buildProfile(self.games, q, self.roles, self.recentGames)
                         for q in play_style.QUEUE_FILTERS}
        if self.profiles.get(None) is None:
            self.state = 'empty'
        else:
            self.state = 'ready'
            if self.profiles.get(self.queueId) is None:
                self.queueId = None
        self._relayout()

    def setChampionIcons(self, icons):
        """icons: {championId: (path, name)}; pixmaps are scaled once here"""
        for cid, (path, name) in (icons or {}).items():
            pm = QPixmap(path)
            if not pm.isNull():
                self.icons[cid] = pm.scaled(ICON_PX, ICON_PX, Qt.KeepAspectRatio,
                                            Qt.SmoothTransformation)
            self.names[cid] = name
        self._dirty()

    def setQueue(self, queueId):
        if queueId == self.queueId or self.profiles.get(queueId) is None:
            return
        self.queueId = queueId
        self._relayout()

    def profile(self):
        return self.profiles.get(self.queueId) if self.state == 'ready' else None

    def _dirty(self):
        self._cacheKey = None
        self.update()

    def _relayout(self):
        self.setFixedHeight(self.layoutHeight(self.width()))
        self._dirty()

    def resizeEvent(self, e):
        h = self.layoutHeight(self.width())
        if h != self.height():
            self.setFixedHeight(h)
        super().resizeEvent(e)

    # ---------------- layout ----------------

    def _grid(self, y, n, h, minW, inner):
        """
        n tiles, as many per line as fit minW, spread evenly over the lines (e.g. 5 -> 3 + 2);
        every line is stretched to the full width, so there are no holes. Returns (rects, y)
        """
        m, gap = self.MARGIN, self.GAP
        fit = max(1, min(n, int((inner + gap) // (minW + gap))))
        rows = math.ceil(n / fit)
        counts = [n // rows + (1 if i < n % rows else 0) for i in range(rows)]
        rects = []
        for row, cols in enumerate(counts):
            w = (inner - gap * (cols - 1)) / cols
            top = y + row * (h + gap)
            rects += [QRectF(m + c * (w + gap), top, w, h) for c in range(cols)]
        return rects, y + rows * h + (rows - 1) * gap + gap

    def headerHeight(self):
        return self.TITLE_H + (self.HEADLINE_H if self.headlineRuns() else 0)

    def sections(self, width):
        """[(kind, [rects])], total height; kinds follow the profile's available data"""
        prof = self.profile()
        if prof is None:
            return [], self.STATE_H
        inner = max(width - 2 * self.MARGIN, 100)
        y = self.headerHeight()
        out = []

        rects, y = self._grid(y, 5, self.STAT_H, 160, inner)
        out.append(('summary', rects))

        rateKeys = self.rateKeys(prof)
        if rateKeys:
            rects, y = self._grid(y, len(rateKeys), self.STAT_H, 160, inner)
            out.append(('rates', rects))

        cards = [k for k, ok in (('positions', prof['positions']['known'] > 0),
                                 ('focus', prof['focus']['champions'])) if ok]
        if cards:
            rects, y = self._grid(y, len(cards), self.FOCUS_H, 380, inner)
            out += [(k, [r]) for k, r in zip(cards, rects)]

        cards = [k for k, ok in (('sides', prof['sides']['games'] > 0),
                                 ('classes', prof['classes']),
                                 ('recent', prof['recent'])) if ok]
        if cards:
            rects, y = self._grid(y, len(cards), self.SMALL_H, 290, inner)
            out += [(k, [r]) for k, r in zip(cards, rects)]

        stacked = inner < 700
        h = self.ACTIVITY_H * 2 - 60 if stacked else self.ACTIVITY_H
        out.append(('activity', [QRectF(self.MARGIN, y, inner, h)]))
        y += h + self.MARGIN - 2
        return out, int(y)

    def layoutHeight(self, width):
        return self.sections(width)[1]

    def rateKeys(self, prof):
        r = prof['rates']
        keys = ['kp'] if r['kp'] is not None else []
        keys += [k for k in ('damage', 'cs', 'gold', 'vision') if r[k] is not None]
        if r['damageShare'] is not None:
            keys.append('damageShare')
        return keys

    # ---------------- interaction ----------------

    def _chipAt(self, pos):
        return next((q for rect, q in self.chipRects
                     if rect.contains(pos) and self.profiles.get(q) is not None), 'none')

    def mouseMoveEvent(self, e):
        pos = e.position().toPoint()
        chip = self._chipAt(pos)
        if chip != self._chipHover:
            self._chipHover = chip
            self.update()
        hover = next((i for i, (rect, _, _) in enumerate(self.tips) if rect.contains(pos)), -1)
        if chip != 'none' or (hover >= 0 and self.tips[hover][2]):
            self.setCursor(Qt.PointingHandCursor)
        else:
            self.unsetCursor()
        if hover != self._hover:
            self._setHover(hover)
            if hover >= 0:
                rect, tip, _ = self.tips[hover]
                RichToolTip.showText(e.globalPosition().toPoint(), tip, self, rect)
        super().mouseMoveEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            chip = self._chipAt(e.position().toPoint())
            if chip != 'none':
                self.setQueue(chip)
                return
        super().mousePressEvent(e)

    def leaveEvent(self, e):
        self._setHover(-1)
        self._chipHover = 'none'
        self.unsetCursor()
        self.update()
        super().leaveEvent(e)

    def _setHover(self, index):
        if index < 0:
            tip = RichToolTip._instance
            if self._hover >= 0 and tip is not None and tip._owner is self:
                RichToolTip.hideText()
        old = self._hover
        self._hover = index
        # only (?) icons highlight, so only those need a repaint
        if self._helpIndex(old) != self._helpIndex(index):
            self.update()

    def _helpIndex(self, index):
        return index if 0 <= index < len(self.tips) and self.tips[index][2] else -1

    def event(self, e):
        if e.type() == QEvent.ToolTip:
            for rect, tip, _ in self.tips:
                if rect.contains(e.pos()):
                    RichToolTip.showText(e.globalPos(), tip, self, rect)
                    return True
            RichToolTip.hideText()
            e.ignore()
            return True
        return super().event(e)

    # ---------------- text ----------------

    def queueName(self, queueId):
        return {None: self.tr("All"), 420: self.tr("Solo/Duo"), 440: self.tr("Flex")}[queueId]

    def seasonLabel(self):
        return self.tr("Season %1").replace("%1", str(datetime.now().year))

    def titleText(self):
        """'Season 2026 · 93 games' (+ queue, + cap note); leads the card instead of a name"""
        if self.state != 'ready':
            return self.seasonLabel()
        n = self.profiles[self.queueId]['summary']['games']
        if self.queueId is None:
            return f"{self.seasonLabel()}  ·  {self.gamesText(n)}"
        return f"{self.seasonLabel()}  ·  {self.queueName(self.queueId)}  ·  " + \
            self.tr("%1 of %2 games").replace("%1", str(n)).replace(
                "%2", str(self.profiles[None]['summary']['games']))

    def headerTip(self):
        tip = self.tr("Season sample: games since January 1 on the current major patch, "
                      "newest first. Remakes and custom games (practice tool, custom lobbies) "
                      "are not counted, so the count can be lower than the Performance "
                      "panel's season games.")
        if self.capped:
            tip += "\n" + self.tr("The sample stops at the newest %1 games, so older season "
                                  "games are not included.").replace("%1", str(len(self.games)))
        tip += "\n" + self.tr("The filter only re-groups this sample by queue.")
        return tip

    def headlineRuns(self):
        """the clear-cut summary (play_style.headline) as text runs; [] when nothing is clear"""
        head = play_style.headline(self.profile())
        if not head:
            return []
        pal = palette(isDarkTheme())
        font, num = textFont(20, True), numberFont(20)
        sep = ("  ·  ", textFont(20), pal['muted'])
        parts = []
        if head['position']:
            parts.append([(self.positionName(head['position']), font, pal['ink'])])
        if head['class']:
            parts.append([(self.className(head['class']), font, pal['ink'])])
        if head['championId'] is not None:
            name = self.names.get(head['championId']) or f"#{head['championId']}"
            parts.append([(name + " ", font, pal['ink']),
                          (pct(head['championShare']), num, pal['gold'])])
        runs = []
        for i, part in enumerate(parts):
            if i:
                runs.append(sep)
            runs += part
        return runs

    def headlineTip(self):
        return self.tr("Summary of the clearest patterns: the main position and class when "
                       "they cover at least half of the games, and the most played champion "
                       "with its share of the games. Parts that are not clear-cut are left out.")

    def positionName(self, pos):
        return {'TOP': self.tr("Top"), 'JUNGLE': self.tr("Jungle"), 'MIDDLE': self.tr("Mid"),
                'BOTTOM': self.tr("Bot"), 'UTILITY': self.tr("Support")}[pos]

    def className(self, cls):
        return {'marksman': self.tr("Marksman"), 'mage': self.tr("Mage"),
                'fighter': self.tr("Fighter"), 'tank': self.tr("Tank"),
                'assassin': self.tr("Assassin"), 'support': self.tr("Support")}[cls]

    def weekdayNames(self):
        return [self.tr("Mon"), self.tr("Tue"), self.tr("Wed"), self.tr("Thu"),
                self.tr("Fri"), self.tr("Sat"), self.tr("Sun")]

    def gamesText(self, n):
        return self.tr("1 game") if n == 1 else self.tr("%1 games").replace("%1", str(n))

    def recordText(self, rec):
        return self.gamesText(rec['games']) + \
            f"  ·  {rec['wins']}W {rec['losses']}L  ·  " + \
            self.tr("%1 win rate").replace("%1", pct(rec['winRate']))

    def smallNote(self):
        return self.tr("Small sample: fewer than %1 games, the win rate says little.") \
            .replace("%1", str(SMALL))

    # ---------------- painting ----------------

    def paintEvent(self, e):
        p = QPainter(self)
        key = (self.state, self.queueId, self._helpIndex(self._hover), id(self.profile()),
               len(self.icons), self._chipHover)
        p.drawPixmap(0, 0, cachedContent(self, self.__draw, key))
        p.end()

    def __draw(self, p, pal):
        p.setPen(QPen(pal['hairline'], 1))
        p.setBrush(pal['sheet'])
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 10, 10)

        self.tips, self.chipRects = [], []
        m, w = self.MARGIN, self.width()
        chipsLeft = self.__drawChips(p, pal) if self.state == 'ready' else w - m

        # title: season label + sample (+ cap note)
        title = self.titleText()
        titleFont = textFont(13, True)
        runs = [(title, titleFont, pal['ink'])]
        if self.state == 'ready' and self.capped:
            runs.append(("  ·  " + self.tr("capped at the newest %1").replace(
                "%1", str(len(self.games))), textFont(12), pal['muted']))
        room = max(chipsLeft - m - 12, 40)
        if runsWidth(runs) > room:
            runs = [(QFontMetrics(titleFont).elidedText(title, Qt.ElideRight, int(room)),
                     titleFont, pal['ink'])]
        end = drawRuns(p, m, 14, 22, runs)
        if self.state == 'ready':
            self.tips.append((QRect(m, 12, int(end - m), 26), self.headerTip(), False))

        if self.state != 'ready':
            text = self.tr("Loading the season sample...") if self.state == 'loading' else \
                self.tr("No season games to analyse yet.")
            drawText(p, QRectF(m, self.TITLE_H, w - 2 * m, self.STATE_H - self.TITLE_H - 20),
                     text, textFont(13), pal['muted'], Qt.AlignCenter)
            return

        headline = self.headlineRuns()
        if headline:
            end = drawRuns(p, m, self.TITLE_H - 6, 32, headline)
            self.tips.append((QRect(m, self.TITLE_H - 6, int(end - m), 32), self.headlineTip(),
                              False))

        prof = self.profile()
        for kind, rects in self.sections(w)[0]:
            getattr(self, '_draw_' + kind)(p, pal, prof, rects)

    def __drawChips(self, p, pal):
        """queue filter chips, right-aligned in the header; returns their left edge"""
        font = textFont(12)
        fm = QFontMetrics(font)
        specs = []
        for q in play_style.QUEUE_FILTERS:
            prof = self.profiles.get(q)
            n = prof['summary']['games'] if prof else 0
            specs.append((q, f"{self.queueName(q)} {n}", n))
        x = self.width() - self.MARGIN
        h = self.CHIP_H
        for q, text, n in reversed(specs):
            cw = fm.horizontalAdvance(text) + 24
            x -= cw
            rect = QRectF(x, 11, cw, h)
            selected = q == self.queueId
            hovered = q == self._chipHover and not selected
            if selected:
                p.setPen(Qt.NoPen)
                p.setBrush(pal['ink'])
            else:
                p.setPen(QPen(tint(pal['muted'], 160) if hovered else pal['hairline'], 1))
                p.setBrush(pal['hairline'] if hovered else Qt.NoBrush)
            p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), h / 2, h / 2)
            color = pal['sheet'] if selected else (pal['ink'] if n else tint(pal['muted'], 120))
            drawText(p, rect, text, font, color, Qt.AlignCenter)
            self.chipRects.append((rect.toRect(), q))
            x -= 6
        return x

    def __tile(self, p, pal, rect, title, tip=None, note=None, noteColor=None, help=True):
        """
        tile background + title + optional right-aligned note. tip: shown on a (?) next to the
        title when help, otherwise on hover anywhere over the tile (self-explanatory numbers)
        """
        p.setPen(QPen(pal['hairline'], 1))
        p.setBrush(tint(pal['hairline'], 70))
        p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 8, 8)
        x, y = rect.left() + 14, rect.top()
        titleFont = textFont(12)
        titleW = QFontMetrics(titleFont).horizontalAdvance(title)
        drawText(p, QRectF(x, y + 10, titleW + 2, 18), title, titleFont, pal['muted'])
        end = x + titleW
        if tip and help:
            hover = self._helpIndex(self._hover) == len(self.tips)
            color = pal['ink'] if hover else pal['muted']
            help_ = QRectF(x + titleW + 6, y + 11, 16, 16)
            p.setPen(QPen(color, 1.1))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(help_.adjusted(0.5, 0.5, -0.5, -0.5))
            drawText(p, help_, "?", textFont(11, True), color, Qt.AlignCenter)
            self.tips.append((QRect(int(x), int(y + 8), int(titleW + 26), 22), tip, True))
            end = help_.right()
        elif tip:
            self.tips.append((rect.toRect(), tip, False))
        if note:
            font = textFont(11)
            room = rect.right() - 14 - end - 8
            note = QFontMetrics(font).elidedText(note, Qt.ElideRight, int(max(room, 0)))
            drawText(p, QRectF(end + 8, y + 10, room, 18), note, font,
                     noteColor or pal['muted'], Qt.AlignRight | Qt.AlignVCenter)

    def __stat(self, p, pal, rect, title, tip, value, sub, valueColor=None, help=False):
        self.__tile(p, pal, rect, title, tip, help=help)
        x, y, w = rect.left() + 14, rect.top(), rect.width() - 28
        font = numberFont(22)
        drawText(p, QRectF(x, y + 30, w, 30), QFontMetrics(font).elidedText(
            value, Qt.ElideRight, int(w)), font, valueColor or pal['ink'])
        if sub:
            subFont = textFont(11)
            drawText(p, QRectF(x, y + 60, w, 16),
                     QFontMetrics(subFont).elidedText(sub, Qt.ElideRight, int(w)), subFont,
                     pal['muted'])

    # ----- summary / rates -----

    def _draw_summary(self, p, pal, prof, rects):
        s = prof['summary']
        self.__stat(p, pal, rects[0], self.tr("Games"),
                    self.tr("Games in the sample. Remakes and custom games (practice tool, "
                            "custom lobbies) are not counted, so this can be lower than the "
                            "Performance panel's season games."),
                    str(s['games']), f"{s['wins']}W  {s['losses']}L")
        self.__stat(p, pal, rects[1], self.tr("Win rate"),
                    self.tr("Share of the sample's games won."), pct(s['winRate']),
                    self.gamesText(s['games']), winRateInk(s['winRate'], s['games'], pal))
        self.__stat(p, pal, rects[2], self.tr("KDA"),
                    self.tr("(Kills + Assists) / Deaths, summed over the games. Below: "
                            "average kills / deaths / assists per game."),
                    f"{s['kda']:.2f}",
                    f"{s['kills']:.1f} / {s['deaths']:.1f} / {s['assists']:.1f}")
        self.__stat(p, pal, rects[3], self.tr("Play time"),
                    self.tr("Total time in the sample's games. Below: average game length."),
                    playTimeText(s['totalSecs']),
                    self.tr("avg. %1").replace("%1", lengthText(s['avgSecs'] or 0)))
        self.__stat(p, pal, rects[4], self.tr("Champion pool"),
                    self.tr("Distinct champions played in the sample, and how many games "
                            "each got on average."), str(s['champions']),
                    self.tr("%1 games each on average").replace(
                        "%1", f"{s['games'] / max(s['champions'], 1):.1f}"), help=True)

    def _draw_rates(self, p, pal, prof, rects):
        r = prof['rates']
        n = prof['summary']['games']

        def perGame(key, text):
            v = r[key + 'PerGame']
            sub = self.tr("%1 per game").replace("%1", text(v)) if v is not None else ""
            if r[key + 'Games'] < n:
                sub += "  ·  " + self.tr("%1 games").replace("%1", str(r[key + 'Games']))
            return sub

        arena = self.tr("Arena games are not counted.")
        specs = {
            'kp': (self.tr("Kill participation"),
                   self.tr("(Kills + assists) / your team's kills, averaged over the games "
                           "that have full team data (%1 of %2 games). The match list of this "
                           "server only has your own stats, so the rest is not known.")
                   .replace("%1", str(r['kpGames'])).replace("%2", str(n))
                   if self.source == 'lcu' else
                   self.tr("(Kills + assists) / your team's kills, averaged over %1 games.")
                   .replace("%1", str(r['kpGames'])),
                   pct(r['kp']),
                   self.tr("%1 games with team data").replace("%1", str(r['kpGames']))
                   if r['kpGames'] < n else self.gamesText(r['kpGames']), True),
            'damage': (self.tr("Damage / min"),
                       self.tr("Damage dealt to champions per minute: total damage / total "
                               "minutes. Below: average per game over the same games.")
                       + " " + arena,
                       f"{round(r['damage']):,}", perGame('damage', compactNumber), False),
            'cs': (self.tr("CS / min"),
                   self.tr("Minions + monsters killed per minute.") + " " + arena,
                   f"{r['cs']:.1f}", perGame('cs', compactNumber), False),
            'gold': (self.tr("Gold / min"),
                     self.tr("Gold earned per minute.") + " " + arena,
                     f"{round(r['gold']):,}", perGame('gold', compactNumber), False),
            'vision': (self.tr("Vision / min"),
                       self.tr("Vision score per minute: wards placed and destroyed and the "
                               "time they reveal. Supports usually have 1.5-3, other roles "
                               "0.5-1.2.") + " " + arena,
                       f"{r['vision']:.2f}", perGame('vision', lambda v: f"{v:.0f}"), True),
            'damageShare': (self.tr("Team damage share"),
                            self.tr("Your damage to champions / your team's, averaged over "
                                    "%1 games. Only the CN match data has all ten players.")
                            .replace("%1", str(r['damageShareGames'])),
                            pct(r['damageShare']),
                            self.tr("sample: %1 games").replace(
                                "%1", str(r['damageShareGames'])), True),
        }
        for rect, key in zip(rects, self.rateKeys(prof)):
            title, tip, value, sub, help_ = specs[key]
            self.__stat(p, pal, rect, title, tip, value, sub, help=help_)

    # ----- shared row pieces -----

    def __bar(self, p, pal, rect, share, color):
        p.setPen(Qt.NoPen)
        p.setBrush(pal['hairline'])
        p.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        if share > 0:
            p.setBrush(color)
            p.drawRoundedRect(QRectF(rect.left(), rect.top(), max(rect.width() * share,
                                                                  rect.height()),
                                     rect.height()), rect.height() / 2, rect.height() / 2)

    COL_WR, COL_GAMES, COL_SHARE = 52, 62, 44

    def __columnHeads(self, p, pal, right, y, games=True):
        """small 'Share  Games  Win rate' captions over the record columns"""
        font = textFont(10)
        align = Qt.AlignRight | Qt.AlignVCenter
        x = right - self.COL_WR
        drawText(p, QRectF(x, y, self.COL_WR, 14), self.tr("Win rate"), font, pal['muted'], align)
        if games:
            x -= self.COL_GAMES
            drawText(p, QRectF(x, y, self.COL_GAMES, 14), self.tr("Games"), font, pal['muted'],
                     align)
        x -= self.COL_SHARE
        drawText(p, QRectF(x, y, self.COL_SHARE, 14), self.tr("Share"), font, pal['muted'],
                 align)

    def __recordColumns(self, p, pal, right, y, h, rec, games=True, numSize=12):
        """share %, games, win rate; rows under SMALL games are dimmed. Returns left edge"""
        dim = rec['games'] < SMALL
        num = numberFont(numSize, 600)
        align = Qt.AlignRight | Qt.AlignVCenter
        x = right - self.COL_WR
        drawText(p, QRectF(x, y, self.COL_WR, h), pct(rec['winRate']) if rec['games'] else "",
                 num, winRateInk(rec['winRate'], rec['games'], pal), align)
        if games:
            x -= self.COL_GAMES
            drawText(p, QRectF(x, y, self.COL_GAMES, h), str(rec['games']), num,
                     pal['muted'] if dim else pal['ink'], align)
        x -= self.COL_SHARE
        drawText(p, QRectF(x, y, self.COL_SHARE, h), pct(rec['share']), num,
                 pal['muted'] if dim else pal['ink'], align)
        return x

    def __rowBarColor(self, pal, rec, first):
        if rec['games'] < SMALL:
            return tint(pal['muted'], 90)
        return pal['gold'] if first else tint(pal['ink'], 110)

    # ----- positions / focus -----

    def _draw_positions(self, p, pal, prof, rects):
        rect = rects[0]
        pos = prof['positions']
        n = prof['summary']['games']
        note = self.tr("%1 of %2 games").replace("%1", str(pos['known'])).replace("%2", str(n)) \
            if pos['known'] < n else None
        tip = self.tr("Share of games per position, out of the games whose position is known.")
        if self.source == 'lcu':
            tip += "\n" + self.tr("On this server the match list only tells the position in "
                                  "Summoner's Rift Draft / Ranked games (lobby order); other "
                                  "games are left out.")
        tip += "\n" + self.smallNote()
        self.__tile(p, pal, rect, self.tr("Positions"), tip, note)
        x, right = rect.left() + 14, rect.right() - 14
        self.__columnHeads(p, pal, right, rect.top() + 34)
        best = max(pos['rows'], key=lambda r: r['games'])['position']
        for i, row in enumerate(pos['rows']):
            y = rect.top() + 52 + i * 34
            dim = row['games'] < SMALL
            drawText(p, QRectF(x, y, 70, 24), self.positionName(row['position']),
                     textFont(12, row['position'] == best and not dim),
                     pal['muted'] if dim else pal['ink'])
            left = self.__recordColumns(p, pal, right, y, 24, row)
            self.__bar(p, pal, QRectF(x + 72, y + 9, max(left - x - 84, 10), 6), row['share'],
                       self.__rowBarColor(pal, row, row['position'] == best))
            tipText = f"{self.positionName(row['position'])}  ·  " + self.recordText(row) + \
                "\n" + self.tr("%1 of the games with a known position").replace(
                    "%1", pct(row['share']))
            if 0 < row['games'] < SMALL:
                tipText += "\n" + self.smallNote()
            self.tips.append((QRect(int(x), int(y - 4), int(right - x), 32), tipText, False))

    def _draw_focus(self, p, pal, prof, rects):
        rect = rects[0]
        f = prof['focus']
        tip = self.tr("How concentrated the champion pool is: share of games on the most "
                      "played champion, and on the 3 / 5 most played together (each figure "
                      "includes the ones before it).") + "\n" + self.smallNote()
        self.__tile(p, pal, rect, self.tr("Champion focus"), tip,
                    self.tr("%1 champions").replace("%1", str(f['distinct'])))
        x, right = rect.left() + 14, rect.right() - 14
        y = rect.top() + 36

        # cumulative legend: each swatch matches its bar segment's shade
        shades = (pal['gold'], tint(pal['gold'], 150), tint(pal['gold'], 70))
        parts = ((self.tr("Main"), f['main'], self.tr("the most played champion")),
                 (self.tr("Top 3"), f['top3'], self.tr("the 3 most played champions")),
                 (self.tr("Top 5"), f['top5'], self.tr("the 5 most played champions")))
        num, label = numberFont(15), textFont(11)
        cx = x
        for (name, share, what), shade in zip(parts, shades):
            start = cx
            p.setPen(Qt.NoPen)
            p.setBrush(shade)
            p.drawRoundedRect(QRectF(cx, y + 6, 10, 10), 2, 2)
            cx = drawRuns(p, cx + 15, y, 22, ((name + " ", label, pal['muted']),
                                              (pct(share), num, pal['ink'])))
            self.tips.append((QRect(int(start), int(y), int(cx - start), 22),
                              self.tr("%1 of the games were on %2").replace(
                                  "%1", pct(share)).replace("%2", what), False))
            cx += 20
        bar = QRectF(x, y + 28, right - x, 10)
        clip = QPainterPath()
        clip.addRoundedRect(bar, 5, 5)
        p.save()
        p.setClipPath(clip)
        p.fillRect(bar, pal['hairline'])
        prev = 0
        for (_, share, _), shade in zip(parts, shades):
            if share > prev:
                p.fillRect(QRectF(bar.left() + bar.width() * prev, bar.top(),
                                  bar.width() * (share - prev), bar.height()), shade)
                # thin sheet-coloured cut so neighbouring segments read as separate
                p.fillRect(QRectF(bar.left() + bar.width() * share - 1, bar.top(), 2,
                                  bar.height()), pal['sheet'])
            prev = share
        p.restore()

        self.__columnHeads(p, pal, right, y + 46)
        rowsTop = y + 62
        rowH = 26
        for i, c in enumerate(f['champions']):
            ry = rowsTop + i * rowH
            dim = c['games'] < SMALL
            icon = QRectF(x, ry + 1, 22, 22)
            drawChampion(p, self.icons.get(c['championId']), icon, pal['gold'], 1.2)
            name = self.names.get(c['championId']) or f"#{c['championId']}"
            nameFont = textFont(12, i == 0 and not dim)
            drawText(p, QRectF(x + 30, ry, 110, 24),
                     QFontMetrics(nameFont).elidedText(name, Qt.ElideRight, 108), nameFont,
                     pal['muted'] if dim else pal['ink'])
            left = self.__recordColumns(p, pal, right, ry, 24, c)
            self.__bar(p, pal, QRectF(x + 146, ry + 9, max(left - x - 158, 10), 6),
                       c['share'], self.__rowBarColor(pal, c, i == 0))
            tipText = f"{name}  ·  " + self.recordText(c) + "\n" + \
                self.tr("%1 of the sample's games").replace("%1", pct(c['share']))
            if dim:
                tipText += "\n" + self.smallNote()
            self.tips.append((QRect(int(x), int(ry), int(right - x), rowH), tipText, False))

    # ----- sides / classes / recent -----

    def _draw_sides(self, p, pal, prof, rects):
        rect = rects[0]
        s = prof['sides']
        dark = isDarkTheme()
        blueC, redC = BETTER[dark], WORSE[dark]
        b, r = s['blue'], s['red']
        note, noteColor = None, pal['muted']
        if b['winRate'] is not None and r['winRate'] is not None:
            # from the same rounded values that are shown
            d = round(b['winRate'] * 100) - round(r['winRate'] * 100)
            if d > 0:
                note = self.tr("Blue +%1 pts").replace("%1", str(d))
            elif d < 0:
                note = self.tr("Red +%1 pts").replace("%1", str(-d))
            else:
                note = self.tr("Even")
            if d and min(b['games'], r['games']) >= SMALL:
                noteColor = blueC if d > 0 else redC
        tip = self.tr("Top bar: share of games played on each side. Big numbers: win rate on "
                      "that side. The corner compares the two win rates in percentage points. "
                      "Arena has no sides and is not counted.")
        self.__tile(p, pal, rect, self.tr("Sides"), tip, note, noteColor)
        x, right = rect.left() + 14, rect.right() - 14
        y = rect.top() + 38
        label = textFont(11)
        drawText(p, QRectF(x, y, right - x, 16), self.tr("Blue %1 of games").replace(
            "%1", pct(b['share'])), label, blueC)
        drawText(p, QRectF(x, y, right - x, 16), self.tr("Red %1 of games").replace(
            "%1", pct(r['share'])), label, redC, Qt.AlignRight | Qt.AlignVCenter)
        bar = QRectF(x, y + 20, right - x, 8)
        clip = QPainterPath()
        clip.addRoundedRect(bar, 4, 4)
        p.save()
        p.setClipPath(clip)
        p.fillRect(bar, redC)
        p.fillRect(QRectF(bar.left(), bar.top(), bar.width() * b['share'], bar.height()), blueC)
        p.restore()
        self.tips.append((QRect(int(x), int(y), int(right - x), 30),
                          self.tr("Share of games: blue %1, red %2").replace(
                              "%1", pct(b['share'])).replace("%2", pct(r['share'])), False))

        num, cap = numberFont(24), textFont(11)
        half = (right - x) / 2
        for i, rec in enumerate((b, r)):
            cx = x + i * half
            runs = ((pct(rec['winRate']) if rec['games'] else "--", num,
                     winRateInk(rec['winRate'], rec['games'], pal)),
                    ("  " + self.tr("win rate"), cap, pal['muted']))
            start = cx if i == 0 else right - runsWidth(runs)
            drawRuns(p, start, y + 40, 32, runs)
            drawText(p, QRectF(cx, y + 74, half, 16),
                     self.gamesText(rec['games']) + f"  ·  {rec['wins']}W {rec['losses']}L",
                     textFont(11), pal['muted'],
                     (Qt.AlignLeft if i == 0 else Qt.AlignRight) | Qt.AlignVCenter)
            name = self.tr("Blue side") if i == 0 else self.tr("Red side")
            self.tips.append((QRect(int(cx), int(y + 38), int(half), 54),
                              f"{name}  ·  " + self.recordText(rec), False))

    def _draw_classes(self, p, pal, prof, rects):
        rect = rects[0]
        c = prof['classes']
        tip = self.tr("Each game counts toward its champion's primary class (from the "
                      "client's champion data).") + "\n" + self.smallNote()
        self.__tile(p, pal, rect, self.tr("Classes"), tip)
        x, right = rect.left() + 14, rect.right() - 14
        self.__columnHeads(p, pal, right, rect.top() + 32, games=False)
        rowH = 17
        for i, row in enumerate(c['rows'][:6]):
            y = rect.top() + 48 + i * rowH
            dim = row['games'] < SMALL
            drawText(p, QRectF(x, y, 80, rowH), self.className(row['class']), textFont(11),
                     pal['muted'] if dim else pal['ink'])
            left = self.__recordColumns(p, pal, right, y, rowH, row, games=False, numSize=11)
            self.__bar(p, pal, QRectF(x + 80, y + 6, max(left - x - 88, 10), 5),
                       row['share'], self.__rowBarColor(pal, row, i == 0))
            tipText = f"{self.className(row['class'])}  ·  " + self.recordText(row)
            if dim:
                tipText += "\n" + self.smallNote()
            self.tips.append((QRect(int(x), int(y), int(right - x), rowH), tipText, False))

    def _draw_recent(self, p, pal, prof, rects):
        rect = rects[0]
        r = prof['recent']
        tip = self.tr("From the recent games listed on Overview (not the season sample): "
                      "the 0-10 game score of each game, MVP (best score of the winning team) "
                      "and SVP (best score of the losing team).")
        self.__tile(p, pal, rect, self.tr("Recent score"), tip,
                    self.tr("last %1 games").replace("%1", str(r['games'])))
        x, right, y = rect.left() + 14, rect.right() - 14, rect.top() + 34
        end = drawRuns(p, x, y, 34, ((f"{r['avg']:.1f}", numberFont(26), opScoreColor(r['avg'])),
                                     (" / 10", numberFont(14, 500), pal['muted']),
                                     ("  " + self.tr("avg."), textFont(11), pal['muted'])))
        drawRuns(p, x, y + 36, 16, (
            (self.tr("best") + " ", textFont(11), pal['muted']),
            (f"{r['best']:.1f}", numberFont(12, 600), opScoreColor(r['best'])),
            ("   " + self.tr("worst") + " ", textFont(11), pal['muted']),
            (f"{r['worst']:.1f}", numberFont(12, 600), opScoreColor(r['worst']))))

        font = textFont(11, True)
        tx = x
        for badge, count in (("MVP", r['mvp']), ("SVP", r['svp'])):
            text = f"{badge} ×{count}"
            tw = QFontMetrics(font).horizontalAdvance(text) + 16
            tag = QRectF(tx, y + 66, tw, 22)
            if count:
                # same colours as the match rows' MVP / SVP tags (MVP in hextech gold)
                p.setPen(Qt.NoPen)
                p.setBrush(pal['gold'] if badge == "MVP" else QColor(TAG_COLORS[badge]))
                p.drawRoundedRect(tag, 11, 11)
                drawText(p, tag, text, font, QColor(255, 255, 255), Qt.AlignCenter)
            else:
                p.setPen(QPen(pal['hairline'], 1))
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(tag.adjusted(0.5, 0.5, -0.5, -0.5), 11, 11)
                drawText(p, tag, text, font, pal['muted'], Qt.AlignCenter)
            tx += tw + 6
        self.tips.append((QRect(int(x), int(y + 64), int(tx - x), 26),
                          self.tr("MVP: best score of the winning team. SVP: best score of "
                                  "the losing team."), False))

        # spark of the scores, oldest left -> newest right, on the free right side
        scores = list(reversed(r['scores']))
        left = max(end, x + 150) + 16
        left = max(left, right - 220)  # keep the spark compact on wide cards
        area = QRectF(left, y + 4, right - left, 70)
        if area.width() >= 60 and len(scores) >= 2:
            slot = area.width() / len(scores)
            bw = max(min(slot * 0.62, 10), 2)
            base = area.bottom()
            p.setPen(QPen(pal['hairline'], 1))
            mid = base - area.height() * 0.5
            p.drawLine(QRectF(area.left(), mid, area.width(), 0).topLeft(),
                       QRectF(area.left(), mid, area.width(), 0).topRight())
            p.setPen(Qt.NoPen)
            for i, score in enumerate(scores):
                h = max(area.height() * score / 10, 2)
                p.setBrush(opScoreColor(score))
                p.drawRoundedRect(QRectF(area.left() + slot * (i + 0.5) - bw / 2, base - h, bw, h),
                                  1.5, 1.5)
            drawText(p, QRectF(area.left(), base + 4, area.width(), 14),
                     self.tr("oldest → newest"), textFont(10), pal['muted'],
                     Qt.AlignRight | Qt.AlignVCenter)
            self.tips.append((area.toRect(), self.tr("Score of each recent game, oldest on the "
                                                     "left. The line marks 5 / 10."), False))

    # ----- activity -----

    def _draw_activity(self, p, pal, prof, rects):
        rect = rects[0]
        a = prof['activity']
        tip = self.tr("Games and win rate by weekday and by hour of day, in this computer's "
                      "local time of the game start. Hover a bar for its exact record.")
        self.__tile(p, pal, rect, self.tr("Activity"), tip)
        x, right = rect.left() + 14, rect.right() - 14

        # legend: what height and colour mean, and how small / even buckets look
        ly = rect.top() + 32
        legend = textFont(11)
        fm = QFontMetrics(legend)
        lead = self.tr("Height = games, colour = win rate:") + "  "
        items = (('win', self.tr("won more")), ('lose', self.tr("lost more")),
                 ('even', self.tr("fainter = nearer 50%")),
                 ('small', self.tr("hollow = fewer than %1 games").replace("%1", str(SMALL))))
        itemsW = sum(14 + fm.horizontalAdvance(t) + 14 for _, t in items)
        cx = drawRuns(p, x, ly, 16, ((lead, legend, pal['muted']),))
        wrapped = cx + itemsW > right
        if wrapped:  # narrow card: swatches on their own line
            cx, ly = x, ly + 18
        win, lose = resultColor('win'), resultColor('lose')
        for kind, text in items:
            sw = QRectF(cx, ly + 3, 10, 10)
            if kind == 'small':
                p.setPen(QPen(pal['muted'], 1))
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(sw.adjusted(0.5, 0.5, -0.5, -0.5), 2, 2)
            else:
                p.setPen(Qt.NoPen)
                p.setBrush({'win': win, 'lose': lose, 'even': tint(win, 100)}[kind])
                p.drawRoundedRect(sw, 2, 2)
            cx = drawRuns(p, cx + 14, ly, 16, ((text, legend, pal['muted']),)) + 14

        top = rect.top() + (74 if wrapped else 56)
        stacked = rect.height() > self.ACTIVITY_H
        if stacked:
            half = (rect.bottom() - 8 - top) / 2
            days = QRectF(x, top, right - x, half - 6)
            hours = QRectF(x, top + half, right - x, half - 6)
        else:
            split = x + (right - x) * 0.36
            days = QRectF(x, top, split - x - 16, rect.bottom() - 8 - top)
            hours = QRectF(split + 16, top, right - split - 16, rect.bottom() - 8 - top)
            p.setPen(QPen(pal['hairline'], 1))
            p.drawLine(QRectF(split, days.top() + 4, 0, days.height() - 8).topLeft(),
                       QRectF(split, days.top() + 4, 0, days.height() - 8).bottomLeft())

        fullNames = [self.tr("Monday"), self.tr("Tuesday"), self.tr("Wednesday"),
                     self.tr("Thursday"), self.tr("Friday"), self.tr("Saturday"),
                     self.tr("Sunday")]
        self.__bars(p, pal, days, a['weekdays'], self.weekdayNames(), fullNames, True,
                    self.tr("By weekday"))
        hourLabels = [f"{h}h" if h % 3 == 0 else "" for h in range(24)]
        hourNames = [f"{h:02d}:00 - {h:02d}:59" for h in range(24)]
        self.__bars(p, pal, hours, a['hours'], hourLabels, hourNames, False,
                    self.tr("By hour of day (local time)"))

    def __bars(self, p, pal, area, records, labels, names, showRate, title):
        drawText(p, QRectF(area.left(), area.top(), area.width(), 16), title, textFont(11),
                 pal['muted'])
        top = area.top() + 22 + (14 if showRate else 0)
        base = area.bottom() - 18
        slot = area.width() / len(records)
        barW = min(slot * (0.56 if showRate else 0.66), 30)
        peak = max((r['games'] for r in records), default=0) or 1
        p.setPen(QPen(pal['hairline'], 1))
        p.drawLine(QRectF(area.left(), base + 0.5, area.width(), 0).topLeft(),
                   QRectF(area.left(), base + 0.5, area.width(), 0).topRight())
        for i, rec in enumerate(records):
            cx = area.left() + slot * (i + 0.5)
            h = max((base - top) * rec['games'] / peak, 3)
            if rec['games']:
                bar = QRectF(cx - barW / 2, base - h, barW, h)
                fill = winRateFill(rec['winRate'], rec['games'], pal)
                if fill is None:
                    p.setPen(QPen(pal['muted'], 1))
                    p.setBrush(Qt.NoBrush)
                    p.drawRoundedRect(bar.adjusted(0.5, 0.5, -0.5, 0), 2, 2)
                else:
                    p.setPen(Qt.NoPen)
                    p.setBrush(fill)
                    p.drawRoundedRect(bar, 2, 2)
                if showRate:
                    drawText(p, QRectF(cx - slot / 2, base - h - 16, slot, 14),
                             pct(rec['winRate']), numberFont(11, 600),
                             winRateInk(rec['winRate'], rec['games'], pal), Qt.AlignCenter)
            if labels[i]:
                drawText(p, QRectF(cx - 20, base + 3, 40, 14), labels[i],
                         numberFont(11, 500) if not showRate else textFont(11), pal['muted'],
                         Qt.AlignCenter)
            tip = f"{names[i]}  ·  " + (self.recordText(rec) if rec['games']
                                        else self.tr("no games"))
            if 0 < rec['games'] < SMALL:
                tip += "\n" + self.smallNote()
            self.tips.append((QRect(int(cx - slot / 2), int(area.top() + 18), int(slot),
                                    int(base - area.top())), tip, False))
