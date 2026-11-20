"""
Career page "Champions" view: this season's statistics per champion, with lane matchups.
One custom-painted table card:

    Season 2026 · 93 games  · +2 columns hidden            [All 93] [Solo/Duo 59] [Flex 20]
    Champion        Games  Win rate  KDA  KP  Score  Lane lead  Damage  Vision  CS  Gold  Multi
    All champions   ...                                             (pinned, never sorted)
    > [icon] Ahri   ...                                             click: expand
        vs [icon] Zed ...                                           (first MATCHUP_LIMIT,
        Show 4 more                         1 game without a lane opponent    then all)

Data: app/lol/champion_stats.build over the season sample Play style already got; the queue
filter only re-groups it. Column headers sort (again: reverse); the total row stays on top.
Champion icons are handed over with setChampionIcons, never loaded on the paint path; a
champion's lane opponents are asked for (opponentsWanted) the first time it is expanded.

Narrow widths: columns drop in DROP_ORDER (multi-kills first, then lane lead, vision, gold,
kill participation, score, CS, damage) until the rest fits at its base width; the title
line then says "+N columns hidden" and the champion cell's tooltip keeps their figures.
Columns with no data in the current filter (score / lane lead on the LCU fallback, CS on
ARAM only ...) are left out instead of showing dashes; on the LCU fallback kill
participation is left out too (it would come from a small subset of the games).

Colours, the same on champion, matchup and All champions rows: win rate and lane lead in
the win / loss colour split at 50%, KDA by career_widgets.kdaColor, score by opScoreColor;
rows under MIN_GAMES games keep those hues at lower opacity.

Painting: only the visible part of the table (plus WINDOW_MARGIN above and below) is
rendered into a cached pixmap, re-rendered on data / filter / sort / expand / width / theme
changes, when a scroll exposes rows outside it, or when icons arrive for rows inside it.
Hover states (row, header cell with a faint sort mark, filter chip) are overlays painted
over that pixmap; the mouse only hit-tests (rows by y with bisect, columns by x), and
tooltip text is built on hover.
"""
from bisect import bisect_left, bisect_right
from datetime import datetime

from PySide6.QtCore import Qt, QRectF, QRect, QEvent, Signal, QPointF
from PySide6.QtGui import QPainter, QPainterPath, QPen, QFontMetrics, QPixmap, QPolygonF
from PySide6.QtWidgets import QFrame, QSizePolicy

from app.common.qfluentwidgets import isDarkTheme
from app.components.career_widgets import kdaColor
from app.components.design_tokens import (numberFont, textFont, resultColor, tint,
                                          opScoreColor, palette, deathsColor)
from app.components.play_style_widget import pct
from app.components.rich_tooltip import RichToolTip
from app.components.scoreboard import drawText, drawRuns, runsWidth, drawChampion
from app.lol import champion_stats

ICON_PX = 64
SMALL = champion_stats.MIN_GAMES
# rows rendered beyond the visible part, so short scrolls only blit
WINDOW_MARGIN = 360

# (key, base width) in display order; the champion column takes the rest
COLUMNS = (('games', 84), ('winRate', 68), ('kda', 98), ('kp', 50), ('score', 52),
           ('laneLead', 62), ('damage', 76), ('vision', 86), ('cs', 64), ('gold', 64),
           ('multi', 88))
DROP_ORDER = ('multi', 'laneLead', 'vision', 'gold', 'kp', 'score', 'cs', 'damage')
# columns that need some games with the field in the current filter
NEEDS = {'kp': 'kpGames', 'score': 'scoreGames', 'laneLead': 'laneLeadGames',
         'damage': 'damageGames', 'vision': 'visionGames', 'cs': 'csGames',
         'gold': 'goldGames', 'multi': 'multiGames'}


def rateColor(rate):
    """win rate / lane lead: the win / loss colour split at 50% (as the career rows)"""
    return resultColor('win' if rate >= 0.5 else 'lose')


def faded(color, dim):
    """small samples keep their hue at lower opacity"""
    return tint(color, int(color.alpha() * 0.45)) if dim else color


class ChampionStatsView(QFrame):
    # a champion row was expanded for the first time: load its lane opponents' icons
    opponentsWanted = Signal(int)

    MARGIN = 18
    TITLE_H = 50
    NOTE_H = 24
    HEAD_H = 40
    TOTAL_H = 52
    ROW_H = 48
    SUB_H = 40
    FOOT_H = 30
    STATE_H = 124
    CHIP_H = 28
    CHAMP_MIN, CHAMP_MAX = 160, 250
    MATCHUP_LIMIT = 5

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.setMouseTracking(True)

        # 'loading' / 'ready' / 'empty'
        self.state = 'empty'
        self.games = []
        self.source = ""
        self.capped = False
        self.profiles = {}
        self.queueId = None
        self.sortKey, self.sortDesc = 'games', True
        self.expanded = set()
        self.showAll = set()
        self.requested = set()
        self.icons = {}
        self.names = {}

        self._chipHover = 'none'  # queueId of the hovered chip, 'none' for no chip
        self._noteHover = False
        self._hover = None  # (row index, column key) under the mouse
        self._rowsKey = self._rows = None
        self._colsKey = self._cols = None
        self._chipsKey = self._chips = None
        self._rowTops = []
        # rendered window: pixmap, its top / bottom in widget coordinates, the content key it
        # was drawn for and the champion ids it shows (icons arriving for others skip it)
        self._cache = None
        self._cacheKey = None
        self._cacheTop = self._cacheBottom = 0
        self._cacheIds = set()
        self._version = 0  # bumped when icons / names change what the window shows
        self.setFixedHeight(self.STATE_H)

    # ---------------- data ----------------

    def setLoading(self):
        """another summoner / refresh: filter, sort and expanded rows reset"""
        self.state = 'loading'
        self.games, self.profiles = [], {}
        self.queueId = None
        self.sortKey, self.sortDesc = 'games', True
        self.expanded, self.showAll, self.requested = set(), set(), set()
        self.icons, self.names = {}, {}
        self._relayout()

    def setEmpty(self):
        self.state = 'empty'
        self.games, self.profiles = [], {}
        self._relayout()

    def setSample(self, games, source="", capped=False, names=None):
        """
        games: season_stats.fetchSeasonGames result; capped: only the newest season games
        (cap reached or a page failed); names: {championId: name}
        """
        if names:
            self.names.update(names)
        self.games = games or []
        self.source = source
        self.capped = capped
        self.profiles = champion_stats.buildAll(self.games)
        if self.profiles.get(None) is None:
            self.state = 'empty'
        else:
            self.state = 'ready'
            if self.profiles.get(self.queueId) is None:
                self.queueId = None
        self._relayout()

    def setChampionIcons(self, icons):
        """
        icons: {championId: (path, name)}; pixmaps are scaled once here. Repaints only when
        an icon or name is for a row of the rendered window, re-sorts only when sorted by name
        """
        changed, renamed = set(), False
        for cid, (path, name) in (icons or {}).items():
            pm = QPixmap(path)
            if not pm.isNull():
                self.icons[cid] = pm.scaled(ICON_PX, ICON_PX, Qt.KeepAspectRatio,
                                            Qt.SmoothTransformation)
                changed.add(cid)
            if name and self.names.get(cid) != name:
                self.names[cid] = name
                changed.add(cid)
                renamed = True
        if renamed and self.sortKey == 'champion':
            self._rowsKey = None
            self._version += 1
            self._relayout()
        elif changed & self._cacheIds:
            self._version += 1
            self.update()

    def setQueue(self, queueId):
        if queueId == self.queueId or self.profiles.get(queueId) is None:
            return
        self.queueId = queueId
        self._relayout()

    def setSort(self, key):
        """same column again: reverse; a new column starts descending (names ascending)"""
        if key == self.sortKey:
            self.sortDesc = not self.sortDesc
        else:
            self.sortKey, self.sortDesc = key, key != 'champion'
        self._relayout()

    def toggleExpanded(self, championId):
        if championId in self.expanded:
            self.expanded.discard(championId)
        else:
            self.expanded.add(championId)
            if championId not in self.requested:
                self.requested.add(championId)
                self.opponentsWanted.emit(championId)
        self._relayout()

    def toggleShowAll(self, championId):
        self.showAll ^= {championId}
        self._relayout()

    def profile(self):
        return self.profiles.get(self.queueId) if self.state == 'ready' else None

    def _relayout(self):
        self._hover = None
        self.setFixedHeight(self.layoutHeight(self.width()))
        self.update()

    def resizeEvent(self, e):
        h = self.layoutHeight(self.width())
        if h != self.height():
            self.setFixedHeight(h)
        super().resizeEvent(e)

    # ---------------- layout ----------------

    def note(self):
        """one line under the title when part of the data cannot be known"""
        if self.profile() is not None and self.source == 'lcu':
            return self.tr("Matchups, score, lane details and kill participation are not "
                           "available for this account right now.")
        return None

    def _fit(self, width):
        """(columns that fit at their base width, columns dropped for width, base widths)"""
        prof = self.profile()
        total = prof['total'] if prof else {}
        fm = QFontMetrics(textFont(11, True))
        # a translated title wider than the base widens its column (room for the sort mark)
        base = {k: max(w, fm.horizontalAdvance(self.columnTitle(k)) + 14) for k, w in COLUMNS}
        keys = [k for k, _ in COLUMNS if (k not in NEEDS or total.get(NEEDS[k]))
                and not (k == 'kp' and self.source == 'lcu')]
        inner = max(width - 2 * self.MARGIN, 100)
        dropped = []
        for drop in DROP_ORDER:
            if self.CHAMP_MIN + sum(base[k] for k in keys) <= inner:
                break
            if drop in keys:
                keys.remove(drop)
                dropped.append(drop)
        return keys, dropped, base

    def columns(self, width):
        """[(key, x, w)] with the champion column first; see DROP_ORDER"""
        prof = self.profile()
        total = prof['total'] if prof else {}
        key = (width, self.source, tuple(bool(total.get(f)) for f in NEEDS.values()))
        if key == self._colsKey:
            return self._cols
        inner = max(width - 2 * self.MARGIN, 100)
        keys, _, base = self._fit(width)
        spare = max(inner - self.CHAMP_MIN - sum(base[k] for k in keys), 0)
        champW = self.CHAMP_MIN + min(spare * 0.3, self.CHAMP_MAX - self.CHAMP_MIN)
        extra = (inner - champW - sum(base[k] for k in keys)) / max(len(keys), 1)
        x = self.MARGIN
        cols = [('champion', x, champW)]
        x += champW
        for k in keys:
            w = base[k] + max(extra, 0)
            cols.append((k, x, w))
            x += w
        self._colsKey, self._cols = key, cols
        return cols

    def hiddenColumns(self):
        """columns that have data but do not fit this width"""
        return self._fit(self.width())[1] if self.profile() is not None else []

    def chips(self):
        """[(QRectF, queueId, text, games)] right-aligned in the title line"""
        key = (self.width(), id(self.profiles), self.state)
        if key == self._chipsKey:
            return self._chips
        out = []
        if self.state == 'ready':
            fm = QFontMetrics(textFont(12))
            x = self.width() - self.MARGIN
            for q in reversed(champion_stats.QUEUE_FILTERS):
                prof = self.profiles.get(q)
                n = prof['total']['games'] if prof else 0
                text = f"{self.queueName(q)} {n}"
                cw = fm.horizontalAdvance(text) + 24
                x -= cw
                out.append((QRectF(x, 11, cw, self.CHIP_H), q, text, n))
                x -= 6
        self._chipsKey, self._chips = key, out
        return out

    def titleRuns(self, pal):
        """the title line: season / sample, cap note, hidden-columns note (its runs index)"""
        runs = [(self.titleText(), textFont(13, True), pal['ink'])]
        if self.state == 'ready' and self.capped:
            runs.append(("  ·  " + self.tr("only the newest %1").replace(
                "%1", str(len(self.games))), textFont(12), pal['muted']))
        hidden = self.hiddenColumns()
        if hidden:
            runs.append(("  ·  ", textFont(12), pal['muted']))
            runs.append((self.tr("+%1 columns hidden").replace("%1", str(len(hidden))),
                         textFont(12, True), pal['gold']))
        return runs

    def hiddenNoteRect(self):
        """where '+N columns hidden' is drawn (for its tooltip), or None"""
        if not self.hiddenColumns():
            return None
        runs = self.titleRuns(palette(isDarkTheme()))
        if runsWidth(runs) > self._titleRoom():
            return None
        start = self.MARGIN + runsWidth(runs[:-1])
        return QRectF(start, 12, runsWidth(runs[-1:]), 26)

    def _titleRoom(self):
        chips = self.chips()
        left = min((r.left() for r, *_ in chips), default=self.width() - self.MARGIN)
        return max(left - self.MARGIN - 12, 40)

    def sortedChampions(self, prof):
        return self._sorted(prof['champions'], 'championId')

    def _sorted(self, rows, idKey):
        if self.sortKey == 'champion':
            return sorted(rows, key=lambda r: self.championName(r[idKey]).lower(),
                          reverse=self.sortDesc)
        return champion_stats.sortRows(rows, self.sortKey, self.sortDesc)

    def rows(self):
        """[(top, height, kind, row, champion row)] for the current state, top to bottom"""
        prof = self.profile()
        key = (id(prof), self.sortKey, self.sortDesc, frozenset(self.expanded),
               frozenset(self.showAll), bool(self.note()),
               self._version if self.sortKey == 'champion' else 0)
        if key == self._rowsKey:
            return self._rows
        out = []
        if prof is not None:
            y = self.TITLE_H + (self.NOTE_H if self.note() else 0)
            out.append((y, self.HEAD_H, 'header', None, None))
            y += self.HEAD_H
            out.append((y, self.TOTAL_H, 'total', prof['total'], None))
            y += self.TOTAL_H + 4
            for champ in self.sortedChampions(prof):
                out.append((y, self.ROW_H, 'champ', champ, champ))
                y += self.ROW_H
                if champ['championId'] not in self.expanded or not champ['matchups']:
                    continue
                matchups = self._sorted(champ['matchups'], 'opponentId')
                shown = matchups if champ['championId'] in self.showAll else \
                    matchups[:self.MATCHUP_LIMIT]
                for m in shown:
                    out.append((y, self.SUB_H, 'match', m, champ))
                    y += self.SUB_H
                if len(matchups) > self.MATCHUP_LIMIT or champ['unknownOpponent']:
                    out.append((y, self.FOOT_H, 'foot', None, champ))
                    y += self.FOOT_H
        self._rowsKey, self._rows = key, out
        self._rowTops = [r[0] for r in out]
        return out

    def layoutHeight(self, width):
        rows = self.rows()
        if not rows:
            return self.STATE_H
        top, h = rows[-1][:2]
        return int(top + h + self.MARGIN)

    # ---------------- interaction ----------------

    def hitTest(self, pos):
        """(row index, column key) under pos, or None"""
        rows = self.rows()
        if not rows:
            return None
        i = bisect_right(self._rowTops, pos.y()) - 1
        if i < 0 or pos.y() >= rows[i][0] + rows[i][1]:
            return None
        if not self.MARGIN <= pos.x() < self.width() - self.MARGIN:
            return None
        col = next((k for k, x, w in self.columns(self.width()) if x <= pos.x() < x + w), None)
        return (i, col) if col else None

    def _clickable(self, hit):
        if hit is None:
            return False
        _, _, kind, row, champ = self.rows()[hit[0]]
        if kind == 'header':
            return True
        if kind == 'champ':
            return bool(champ['matchups'])
        if kind == 'foot':
            return len(champ['matchups']) > self.MATCHUP_LIMIT and hit[1] == 'champion'
        return False

    def _chipAt(self, pos):
        return next((q for rect, q, _, n in self.chips() if rect.contains(pos) and n), 'none')

    def mouseMoveEvent(self, e):
        pos = e.position().toPoint()
        chip = self._chipAt(pos)
        if chip != self._chipHover:
            self._chipHover = chip
            self.update()  # overlay only
        noteRect = self.hiddenNoteRect()
        onNote = bool(noteRect and noteRect.contains(pos))
        if onNote != self._noteHover:
            self._noteHover = onNote
            if onNote:
                RichToolTip.showText(e.globalPosition().toPoint(), self.hiddenTip(), self,
                                     noteRect.toRect())
            else:
                RichToolTip.hideText()
        hit = self.hitTest(pos)
        if chip != 'none' or self._clickable(hit):
            self.setCursor(Qt.PointingHandCursor)
        else:
            self.unsetCursor()
        if hit != self._hover:
            self._setHover(hit)
            tip = self.tipText(hit) if hit else None
            if tip:
                RichToolTip.showText(e.globalPosition().toPoint(), tip, self,
                                     self.cellRect(hit).toRect())
        super().mouseMoveEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            pos = e.position().toPoint()
            chip = self._chipAt(pos)
            if chip != 'none':
                self.setQueue(chip)
                return
            hit = self.hitTest(pos)
            if self._clickable(hit):
                _, _, kind, _, champ = self.rows()[hit[0]]
                RichToolTip.hideText()
                if kind == 'header':
                    self.setSort(hit[1])
                elif kind == 'champ':
                    self.toggleExpanded(champ['championId'])
                else:
                    self.toggleShowAll(champ['championId'])
                return
        super().mousePressEvent(e)

    def leaveEvent(self, e):
        self._setHover(None)
        self._noteHover = False
        if self._chipHover != 'none':
            self._chipHover = 'none'
            self.update()
        self.unsetCursor()
        super().leaveEvent(e)

    def _setHover(self, hit):
        if hit is None:
            tip = RichToolTip._instance
            if self._hover is not None and tip is not None and tip._owner is self:
                RichToolTip.hideText()
        changed = self._overlayRect(hit) != self._overlayRect(self._hover)
        self._hover = hit
        if changed:  # the rendered window stays; only the overlay is repainted
            self.update()

    def event(self, e):
        if e.type() == QEvent.ToolTip:
            hit = self.hitTest(e.pos())
            tip = self.tipText(hit) if hit else None
            noteRect = self.hiddenNoteRect()
            if noteRect and noteRect.contains(e.pos()):
                RichToolTip.showText(e.globalPos(), self.hiddenTip(), self, noteRect.toRect())
            elif tip:
                RichToolTip.showText(e.globalPos(), tip, self, self.cellRect(hit).toRect())
            else:
                RichToolTip.hideText()
                e.ignore()
            return True
        return super().event(e)

    def cellRect(self, hit):
        top, h = self.rows()[hit[0]][:2]
        x, w = next((x, w) for k, x, w in self.columns(self.width()) if k == hit[1])
        return QRectF(x, top, w, h)

    def _overlayRect(self, hit):
        """what the hover highlights: a header cell or a whole champion / matchup row"""
        if hit is None:
            return None
        top, h, kind = self.rows()[hit[0]][:3]
        if kind == 'header':
            return self.cellRect(hit)
        if kind in ('champ', 'match'):
            return QRectF(self.MARGIN, top, self.width() - 2 * self.MARGIN, h)
        if kind == 'foot' and self._clickable(hit):
            return self.cellRect(hit)
        return None

    # ---------------- text ----------------

    def queueName(self, queueId):
        return {None: self.tr("All"), 420: self.tr("Solo/Duo"), 440: self.tr("Flex")}[queueId]

    def championName(self, cid):
        return self.names.get(cid) or f"#{cid}"

    def gamesText(self, n):
        return self.tr("1 game") if n == 1 else self.tr("%1 games").replace("%1", str(n))

    def columnTitle(self, key):
        return {'champion': self.tr("Champion"), 'games': self.tr("Games"),
                'winRate': self.tr("Win rate"), 'kda': self.tr("KDA"),
                'kp': self.tr("KP"), 'score': self.tr("Score"),
                'laneLead': self.tr("Lane lead"), 'damage': self.tr("Damage"),
                'vision': self.tr("Vision"), 'cs': self.tr("CS"), 'gold': self.tr("Gold"),
                'multi': self.tr("Multi-kills")}[key]

    def columnSub(self, key):
        """small caption under a column title saying what the second line is"""
        if key == 'multi':
            return "2× 3× 4× 5×"  # drawn per slot by _draw_header
        if key == 'damage':
            prof = self.profile()
            return self.tr("/min · team") if prof and prof['total']['damageShareGames'] else \
                self.tr("per min")
        return {'kda': self.tr("K / D / A"), 'vision': self.tr("wards P · D · C"),
                'cs': self.tr("game · min"), 'gold': self.tr("game · min")}.get(key)

    def sortNote(self):
        return self.tr("Click to sort. Sorting by this column lists champions with fewer than "
                       "%1 games after the others.").replace("%1", str(SMALL))

    def columnTip(self, key):
        rift = self.tr("Summoner's Rift games only.")
        tips = {
            'champion': self.tr("Champions played this season, most played first. Click a "
                                "champion to see its record against each lane opponent. "
                                "Champions and matchups with fewer than %1 games are "
                                "faded.").replace("%1", str(SMALL)) + "\n" +
            self.tr("Click to sort by name."),
            'games': self.tr("Games played, wins and losses. Remakes, custom games and Arena "
                             "are not counted.") + "\n" + self.tr("Click to sort."),
            'winRate': self.tr("Share of the games won. Below: wins (left) against losses."),
            'kda': self.tr("(Kills + assists) / deaths over all the games. Below: average "
                           "kills / deaths / assists per game."),
            'kp': self.tr("Kill participation: (kills + assists) / your team's kills, "
                          "averaged over the games."),
            'score': self.tr("Average 0-10 game score in ranked games (Solo/Duo and Flex), "
                             "computed from the full game data."),
            'laneLead': self.tr("Lane lead: share of games ahead in gold and XP after laning "
                                "(not a laning score). From Riot's end-of-game statistics."),
            'damage': self.tr("Damage per minute · share of team damage: damage to champions "
                              "per minute, and your average share of your team's damage to "
                              "champions.") + " " + rift,
            'vision': self.tr("Vision score per game. Below: wards placed · destroyed · "
                              "control wards bought, per game.") + " " + rift,
            'cs': self.tr("Minions and monsters killed: per game · per minute.") + " " + rift,
            'gold': self.tr("Gold earned: per game · per minute.") + " " + rift,
            'multi': self.tr("Double, triple, quadra and penta kills, added up over the "
                             "games."),
        }
        tip = tips[key]
        if key not in ('champion', 'games'):
            tip += "\n" + self.sortNote()
        return tip

    def hiddenTip(self):
        names = ", ".join(self.columnTitle(k) for k in self.hiddenColumns())
        return self.tr("Not enough room for: %1. Widen the window to see them; a champion's "
                       "tooltip also lists them.").replace("%1", names)

    def coverage(self, n, games):
        if n >= games:
            return self.tr("over %1").replace("%1", self.gamesText(n))
        return self.tr("over %1 of %2 games").replace("%1", str(n)).replace("%2", str(games))

    def recordText(self, r):
        return self.gamesText(r['games']) + f"  ·  {r['wins']}W {r['losses']}L  ·  " + \
            self.tr("%1 win rate").replace("%1", pct(r['winRate']))

    def smallNote(self):
        return self.tr("Small sample: fewer than %1 games, the numbers say little.") \
            .replace("%1", str(SMALL))

    def rowLabel(self, kind, row, champ):
        if kind == 'total':
            return self.tr("All champions")
        if kind == 'match':
            return self.tr("%1 vs %2").replace("%1", self.championName(champ['championId'])) \
                .replace("%2", self.championName(row['opponentId']))
        return self.championName(row['championId'])

    def cellTexts(self, key, r):
        """(main, second line or None) for a cell; '--' when no game has the field"""
        def num(v, fmt):
            return fmt(v) if v is not None else "--"

        if key == 'games':
            return str(r['games']), f"{r['wins']}W {r['losses']}L"
        if key == 'winRate':
            return pct(r['winRate']), None
        if key == 'kda':
            return f"{r['kda']:.2f}", \
                f"{r['kills']:.1f} / {r['deaths']:.1f} / {r['assists']:.1f}"
        if key == 'kp':
            return pct(r['kp']), None
        if key == 'score':
            return num(r['score'], lambda v: f"{v:.1f}"), None
        if key == 'laneLead':
            return pct(r['laneLead']), None
        if key == 'damage':
            return num(r['damagePerMin'], lambda v: f"{round(v):,}"), \
                pct(r['damageShare']) if r['damageShare'] is not None else None
        if key == 'vision':
            if r['vision'] is None:
                return "--", None
            wards = [r[k] for k in ('wardsPlaced', 'wardsKilled', 'controlWards')]
            return f"{r['vision']:.1f}", " · ".join(
                num(v, lambda v: f"{v:.1f}") for v in wards) if wards[0] is not None else None
        if key == 'cs':
            return num(r['cs'], lambda v: f"{v:.0f}"), \
                num(r['csPerMin'], lambda v: f"{v:.1f}/min") if r['cs'] is not None else None
        if key == 'gold':
            return num(r['gold'], lambda v: f"{v / 1000:.1f}k"), \
                num(r['goldPerMin'], lambda v: f"{round(v)}/min") if r['gold'] is not None \
                else None
        if key == 'multi':
            if r['doubleKills'] is None:
                return "--", None
            return " / ".join(str(r[k]) for k in champion_stats.MULTI_KILLS), None
        return "", None

    def cellTip(self, key, r):
        """one cell's figure in words, with how many games it is over"""
        n = r['games']
        main, sub = self.cellTexts(key, r)
        title = self.columnTitle(key)
        if key in ('games', 'winRate'):
            return self.recordText(r)
        if key == 'kda':
            return self.tr("KDA %1  ·  %2 per game").replace("%1", main).replace("%2", sub)
        have = r[NEEDS[key]]
        if not have:
            return f"{title}: " + self.tr("not known for these games")
        text = f"{title}: {main}"
        if key == 'kp':
            text = self.tr("Kill participation: %1").replace("%1", main)
        elif key == 'damage':
            text = self.tr("Damage per minute: %1").replace("%1", main)
            if r['damageShare'] is not None:
                text += "\n" + self.tr("Share of team damage: %1 (%2)").replace(
                    "%1", sub).replace("%2", self.coverage(r['damageShareGames'], n))
        elif key == 'vision':
            text = self.tr("Vision score: %1 per game").replace("%1", main)
            if r['wardsPlaced'] is not None:
                text += "\n" + self.tr("Wards placed %1, destroyed %2, control wards bought %3 "
                                       "per game").replace("%1", f"{r['wardsPlaced']:.1f}") \
                    .replace("%2", f"{r['wardsKilled'] or 0:.1f}") \
                    .replace("%3", f"{r['controlWards'] or 0:.1f}")
        elif key == 'cs':
            text = self.tr("CS: %1 per game, %2").replace("%1", main).replace("%2", sub)
        elif key == 'gold':
            text = self.tr("Gold: %1 per game, %2").replace("%1", main).replace("%2", sub)
        elif key == 'multi':
            text = self.tr("Double %1  ·  Triple %2  ·  Quadra %3  ·  Penta %4")
            for i, k in enumerate(champion_stats.MULTI_KILLS):
                text = text.replace(f"%{i + 1}", str(r[k]))
        elif key == 'laneLead':
            text = self.tr("Ahead in gold and XP after laning: %1").replace("%1", main)
        return text + "\n" + self.coverage(have, n)

    def tipText(self, hit):
        i, key = hit
        top, h, kind, row, champ = self.rows()[i]
        if kind == 'header':
            return self.columnTip(key)
        if kind == 'foot':
            parts = []
            hidden = len(champ['matchups']) - self.MATCHUP_LIMIT
            if hidden > 0 and key == 'champion':
                parts.append(self.tr("Show fewer matchups") if champ['championId'] in
                             self.showAll else self.tr("Show all %1 matchups").replace(
                                 "%1", str(len(champ['matchups']))))
            if champ['unknownOpponent']:
                parts.append(self.tr("Games without a known lane opponent (no position, e.g. "
                                     "ARAM) are counted in the champion's row only."))
            return "\n".join(parts) or None
        label = self.rowLabel(kind, row, champ)
        if key == 'champion':
            text = f"{label}  ·  " + self.recordText(row)
            if kind == 'champ' and row['matchups']:
                text += "\n" + (self.tr("Click to hide the matchups") if row['championId'] in
                                self.expanded else self.tr("Click to show the matchups"))
            # figures of the columns this width leaves out
            for k in self.hiddenColumns():
                if row.get(NEEDS.get(k, 'games')):
                    main, sub = self.cellTexts(k, row)
                    text += f"\n{self.columnTitle(k)}: {main}" + (f"  ({sub})" if sub else "")
        else:
            text = f"{label}\n" + self.cellTip(key, row)
        if row['games'] < SMALL and kind != 'total':
            text += "\n" + self.smallNote()
        return text

    def seasonLabel(self):
        return self.tr("Season %1").replace("%1", str(datetime.now().year))

    def titleText(self):
        if self.state != 'ready':
            return self.seasonLabel()
        n = self.profiles[self.queueId]['total']['games']
        if self.queueId is None:
            return f"{self.seasonLabel()}  ·  {self.gamesText(n)}"
        return f"{self.seasonLabel()}  ·  {self.queueName(self.queueId)}  ·  " + \
            self.tr("%1 of %2 games").replace("%1", str(n)).replace(
                "%2", str(self.profiles[None]['total']['games']))

    def emptyTexts(self):
        if self.state == 'loading':
            return self.tr("Loading the season sample..."), None
        if self.games:
            return self.tr("No season games to show here."), \
                self.tr("Remakes, custom games and Arena are not counted in this view.")
        return self.tr("No season games yet."), \
            self.tr("Games played since January 1 on the current patch show up here.")

    # ---------------- painting ----------------

    def contentKey(self):
        """everything the rendered window depends on (no hover state)"""
        return (self.width(), self.height(), self.devicePixelRatioF(), isDarkTheme(),
                resultColor('win').rgba(), resultColor('lose').rgba(), deathsColor().rgba(),
                self.state, self.queueId, id(self.profile()), self.sortKey, self.sortDesc,
                frozenset(self.expanded), frozenset(self.showAll), self.source, self.capped,
                self._version)

    def renderWindow(self, top, bottom):
        """draws rows top..bottom (widget coordinates) into the cache pixmap"""
        dpr = self.devicePixelRatioF()
        out = QPixmap(max(round(self.width() * dpr), 1), max(round((bottom - top) * dpr), 1))
        out.setDevicePixelRatio(dpr)
        out.fill(Qt.transparent)
        p = QPainter(out)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform
                         | QPainter.TextAntialiasing)
        p.translate(0, -top)
        self._cacheIds = set()
        self.__draw(p, palette(isDarkTheme()), top, bottom)
        p.end()
        self._cache, self._cacheTop, self._cacheBottom = out, top, bottom
        self._cacheKey = self.contentKey()
        return out

    def ensureWindow(self, exposed, visible=None):
        """
        the rendered window covers exposed (widget coordinates); otherwise render the visible
        part (the scroll area's viewport) plus WINDOW_MARGIN above and below
        """
        if visible is None or visible.isEmpty():
            visible = exposed
        if (self._cacheKey != self.contentKey() or exposed.top() < self._cacheTop
                or exposed.bottom() + 1 > self._cacheBottom):
            top = max(min(visible.top(), exposed.top()) - WINDOW_MARGIN, 0)
            bottom = min(max(visible.bottom(), exposed.bottom()) + 1 + WINDOW_MARGIN,
                         self.height())
            self.renderWindow(top, bottom)

    def paintEvent(self, e):
        self.ensureWindow(e.rect(), self.visibleRegion().boundingRect())
        p = QPainter(self)
        p.drawPixmap(0, self._cacheTop, self._cache)
        self.__drawOverlays(p, palette(isDarkTheme()))
        p.end()

    def __drawOverlays(self, p, pal):
        p.setRenderHint(QPainter.Antialiasing)
        chip = next(((r, q) for r, q, _, n in self.chips() if q == self._chipHover), None)
        if chip and chip[1] != self.queueId:
            rect = chip[0]
            p.setPen(QPen(tint(pal['muted'], 160), 1))
            p.setBrush(tint(pal['ink'], 18))
            p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), rect.height() / 2,
                              rect.height() / 2)
        rect = self._overlayRect(self._hover)
        if rect is None:
            return
        p.setPen(Qt.NoPen)
        p.setBrush(tint(pal['ink'], 16))
        p.drawRoundedRect(rect.adjusted(0, 1, 0, -1), 6, 6)
        i, key = self._hover
        top, h, kind = self.rows()[i][:3]
        if kind == 'header' and key != self.sortKey:
            # a faint mark where the sort arrow would go: this header sorts
            center = self.__sortMarkCenter(key, QRectF(rect.left(), top, rect.width(), h))
            self.__sortMark(p, tint(pal['gold'], 120), center,
                            key != 'champion')

    def __sortMarkCenter(self, key, cellRect):
        """where a header's sort triangle goes, right of its (shifted) title"""
        titleFont = textFont(11, True)
        tw = QFontMetrics(titleFont).horizontalAdvance(self.columnTitle(key))
        cy = cellRect.top() + (3 + 9 if self.columnSub(key) else cellRect.height() / 2)
        if key == 'champion':
            return QPointF(cellRect.left() + 8 + tw + 8, cy)
        return QPointF(cellRect.center().x() - 5 + tw / 2 + 7, cy)

    def __sortMark(self, p, color, c, down):
        d = 3.5 if down else -3.5
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawPolygon(QPolygonF([QPointF(c.x() - 3.5, c.y() - d / 2),
                                 QPointF(c.x() + 3.5, c.y() - d / 2),
                                 QPointF(c.x(), c.y() + d / 2 + (1 if d > 0 else -1))]))

    def __draw(self, p, pal, top, bottom):
        p.setPen(QPen(pal['hairline'], 1))
        p.setBrush(pal['sheet'])
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 10, 10)
        m, w = self.MARGIN, self.width()

        if top < self.TITLE_H + self.NOTE_H:
            self.__drawTitle(p, pal)

        if self.state != 'ready':
            text, hint = self.emptyTexts()
            drawText(p, QRectF(m, self.TITLE_H - 4, w - 2 * m, 26), text, textFont(13, True),
                     pal['muted'], Qt.AlignCenter)
            if hint:
                drawText(p, QRectF(m, self.TITLE_H + 22, w - 2 * m, 20), hint, textFont(12),
                         pal['muted'], Qt.AlignCenter)
            return

        note = self.note()
        if note and top < self.TITLE_H + self.NOTE_H:
            font = textFont(12)
            drawText(p, QRectF(m, self.TITLE_H - 8, w - 2 * m, 20),
                     QFontMetrics(font).elidedText(note, Qt.ElideRight, int(w - 2 * m)),
                     font, pal['muted'])

        cols = self.columns(w)
        rows = self.rows()
        first = max(bisect_left([r[0] + r[1] for r in rows], top) , 0)
        for top_, h, kind, row, champ in rows[first:]:
            if top_ >= bottom:
                break
            if kind == 'champ':
                self._cacheIds.add(row['championId'])
            elif kind == 'match':
                self._cacheIds.add(row['opponentId'])
            getattr(self, '_draw_' + kind)(p, pal, cols, QRectF(m, top_, w - 2 * m, h), row,
                                           champ)

    def __drawTitle(self, p, pal):
        m = self.MARGIN
        for rect, q, text, n in self.chips():
            selected = q == self.queueId
            if selected:
                p.setPen(Qt.NoPen)
                p.setBrush(pal['ink'])
            else:
                p.setPen(QPen(pal['hairline'], 1))
                p.setBrush(Qt.NoBrush)
            h = rect.height()
            p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), h / 2, h / 2)
            color = pal['sheet'] if selected else (pal['ink'] if n else tint(pal['muted'], 120))
            drawText(p, rect, text, textFont(12), color, Qt.AlignCenter)

        runs = self.titleRuns(pal)
        room = self._titleRoom()
        if runsWidth(runs) > room:
            title, font = runs[0][0], runs[0][1]
            runs = [(QFontMetrics(font).elidedText(title, Qt.ElideRight, int(room)), font,
                     pal['ink'])]
        drawRuns(p, m, 14, 22, runs)

    # ----- rows -----

    def _draw_header(self, p, pal, cols, rect, row, champ):
        titleFont, subFont = textFont(11, True), textFont(11)
        for key, x, w in cols:
            active = key == self.sortKey
            color = pal['ink'] if active else tint(pal['ink'], 190)
            sub = self.columnSub(key)
            align = (Qt.AlignLeft if key == 'champion' else Qt.AlignHCenter) | Qt.AlignVCenter
            left = x + (8 if key == 'champion' else 4)
            # every centred title sits a little left, leaving room for the sort mark
            shift = 0 if key == 'champion' else 5
            cell = QRectF(left - shift, rect.top() + (3 if sub else 0), w - 8,
                          18 if sub else rect.height())
            drawText(p, cell, self.columnTitle(key), titleFont, color, align)
            if active:
                self.__sortMark(p, pal['gold'], self.__sortMarkCenter(
                    key, QRectF(x, rect.top(), w, rect.height())), self.sortDesc)
            if key == 'multi':
                # one label per count, over the cells' four slots
                slot = (w - 4) / 4
                for i in range(4):
                    drawText(p, QRectF(x + 2 + i * slot, rect.top() + 21, slot, 15),
                             f"{i + 2}×", numberFont(11, 500), pal['muted'], Qt.AlignCenter)
            elif sub:
                drawText(p, QRectF(x + 2, rect.top() + 21, w - 4, 15),
                         QFontMetrics(subFont).elidedText(sub, Qt.ElideRight, int(w - 4)),
                         subFont, pal['muted'], Qt.AlignHCenter | Qt.AlignVCenter)
        p.setPen(QPen(pal['hairline'], 1))
        p.drawLine(QPointF(rect.left(), rect.bottom() - 0.5),
                   QPointF(rect.right(), rect.bottom() - 0.5))

    def _draw_total(self, p, pal, cols, rect, row, champ):
        p.setPen(Qt.NoPen)
        p.setBrush(tint(pal['gold'], 26))
        p.drawRoundedRect(rect.adjusted(0, 4, 0, -2), 8, 8)
        x, cw = cols[0][1], cols[0][2]
        drawRuns(p, x + 12, rect.top() + 9, 20, ((self.tr("All champions"), textFont(13, True),
                                                  pal['ink']),))
        drawText(p, QRectF(x + 12, rect.top() + 29, cw - 16, 15),
                 self.tr("%1 champions").replace("%1", str(row['champions'])), textFont(11),
                 tint(pal['ink'], 175))
        self.__cells(p, pal, cols, rect.adjusted(0, 2, 0, 0), row, False, 15)

    def _draw_champ(self, p, pal, cols, rect, row, champ):
        if rect.top() > self.rows()[2][0] + 1:  # hairline between champions
            p.setPen(QPen(pal['hairline'], 1))
            p.drawLine(QPointF(rect.left() + 8, rect.top() + 0.5),
                       QPointF(rect.right() - 8, rect.top() + 0.5))
        dim = row['games'] < SMALL
        x, cw = cols[0][1], cols[0][2]
        open_ = row['championId'] in self.expanded
        if row['matchups']:
            self.__chevron(p, pal['ink'] if open_ else pal['muted'],
                           QPointF(x + 12, rect.center().y()), open_)
        icon = QRectF(x + 24, rect.center().y() - 16, 32, 32)
        p.save()
        if dim:
            p.setOpacity(0.5)
        drawChampion(p, self.icons.get(row['championId']), icon, pal['gold'], 1.4)
        p.restore()
        nameFont = textFont(13, True)
        textX = icon.right() + 10
        room = x + cw - textX - 6
        name = self.championName(row['championId'])
        nameTop = rect.top() + 6 if row['matchups'] else rect.center().y() - 10
        drawText(p, QRectF(textX, nameTop, room, 20),
                 QFontMetrics(nameFont).elidedText(name, Qt.ElideRight, int(room)), nameFont,
                 faded(pal['ink'], dim))
        if row['matchups']:
            sub = self.tr("%1 matchups").replace("%1", str(len(row['matchups']))) \
                if len(row['matchups']) != 1 else self.tr("1 matchup")
            drawText(p, QRectF(textX, rect.top() + 26, room, 16), sub, textFont(11),
                     faded(pal['ink'] if open_ else tint(pal['ink'], 175), dim))
        self.__cells(p, pal, cols, rect, row, dim, 15)

    def _draw_match(self, p, pal, cols, rect, row, champ):
        p.setPen(Qt.NoPen)
        p.setBrush(tint(pal['hairline'], 90))
        p.drawRect(rect)
        dim = row['games'] < SMALL
        x, cw = cols[0][1], cols[0][2]
        p.setPen(QPen(tint(pal['gold'], 150), 2))
        p.drawLine(QPointF(x + 12, rect.top()), QPointF(x + 12, rect.bottom()))
        vs = textFont(11)
        vx = x + 30
        drawText(p, QRectF(vx, rect.top(), 20, rect.height()), self.tr("vs"), vs,
                 tint(pal['ink'], 175))
        vx += QFontMetrics(vs).horizontalAdvance(self.tr("vs")) + 6
        icon = QRectF(vx, rect.center().y() - 12, 24, 24)
        p.save()
        if dim:
            p.setOpacity(0.5)
        drawChampion(p, self.icons.get(row['opponentId']), icon, tint(pal['muted'], 200), 1.1)
        p.restore()
        font = textFont(12)
        room = x + cw - icon.right() - 14
        drawText(p, QRectF(icon.right() + 8, rect.top(), room, rect.height()),
                 QFontMetrics(font).elidedText(self.championName(row['opponentId']),
                                               Qt.ElideRight, int(room)), font,
                 faded(pal['ink'], dim))
        self.__cells(p, pal, cols, rect, row, dim, 13)

    def _draw_foot(self, p, pal, cols, rect, row, champ):
        p.setPen(Qt.NoPen)
        p.setBrush(tint(pal['hairline'], 90))
        p.drawRect(rect)
        x, cw = cols[0][1], cols[0][2]
        p.setPen(QPen(tint(pal['gold'], 150), 2))
        p.drawLine(QPointF(x + 12, rect.top()), QPointF(x + 12, rect.top() + rect.height() / 2))
        total = len(champ['matchups'])
        if total > self.MATCHUP_LIMIT:
            # the link: bold gold with a chevron, in the champion column only
            text = self.tr("Show fewer") if champ['championId'] in self.showAll else \
                self.tr("Show %1 more").replace("%1", str(total - self.MATCHUP_LIMIT))
            end = drawRuns(p, x + 30, rect.top(), rect.height(),
                           ((text, textFont(11, True), pal['gold']),))
            self.__chevron(p, pal['gold'], QPointF(end + 9, rect.center().y()),
                           champ['championId'] not in self.showAll, 3.0)
        if champ['unknownOpponent']:
            # a plain footnote at the far right, not part of the link
            note = self.tr("%1 without a known lane opponent, counted in the champion row") \
                .replace("%1", self.gamesText(champ['unknownOpponent']))
            right = cols[-1][1] + cols[-1][2] - 8
            left = x + cw
            small = textFont(11)
            drawText(p, QRectF(left, rect.top(), right - left, rect.height()),
                     QFontMetrics(small).elidedText(note, Qt.ElideLeft, int(right - left)),
                     small, pal['muted'], Qt.AlignRight | Qt.AlignVCenter)

    def __chevron(self, p, color, center, open_, s=4.0):
        """a small > that turns down when the row is open"""
        pts = [QPointF(-s / 2, -s), QPointF(s / 2, 0), QPointF(-s / 2, s)]
        if open_:
            pts = [QPointF(-s, -s / 2), QPointF(0, s / 2), QPointF(s, -s / 2)]
        p.setPen(QPen(color, 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        p.drawPolyline(QPolygonF([center + q for q in pts]))

    def mainColor(self, key, row, pal):
        """one colour rule per column, the same for every kind of row"""
        if key == 'winRate':
            return rateColor(row['winRate'])
        if key == 'laneLead' and row['laneLead'] is not None:
            return rateColor(row['laneLead'])
        if key == 'kda':
            return kdaColor(row['kda']) or pal['ink']
        if key == 'score' and row['score'] is not None:
            return opScoreColor(row['score'])
        return pal['ink']

    def __cells(self, p, pal, cols, rect, row, dim, mainSize):
        """every data column of one row: main figure, optional second line below"""
        mainFont = numberFont(mainSize, 600)
        subFont = numberFont(12 if mainSize >= 15 else 11, 500)
        subColor = faded(tint(pal['ink'], 175), dim)
        compact = rect.height() < self.ROW_H - 4
        for key, x, w in cols[1:]:
            cell = QRectF(x + 2, rect.top(), w - 4, rect.height())
            if key == 'multi':
                self.__multi(p, pal, cell, row, dim, mainSize)
                continue
            main, sub = self.cellTexts(key, row)
            color = tint(pal['muted'], 150) if main == "--" else \
                faded(self.mainColor(key, row, pal), dim)
            twoLines = bool(sub) or key == 'winRate'
            mainH = mainSize + 6
            top = rect.top() + (3 if compact else 6) if twoLines else \
                rect.center().y() - mainH / 2
            drawText(p, QRectF(cell.left(), top, cell.width(), mainH), main, mainFont, color,
                     Qt.AlignHCenter | Qt.AlignVCenter)
            below = top + mainH + (0 if compact else 1)
            if key == 'winRate':
                self.__winBar(p, QRectF(cell.left(), below, cell.width(), 12), row, dim)
            elif sub:
                drawText(p, QRectF(cell.left(), below, cell.width(), 15),
                         QFontMetrics(subFont).elidedText(sub, Qt.ElideRight, int(cell.width())),
                         subFont, subColor, Qt.AlignHCenter | Qt.AlignVCenter)

    def __winBar(self, p, rect, row, dim):
        """wins (left, win colour) against losses, centred under the win rate"""
        w = min(58, rect.width() - 8)
        bar = QRectF(rect.center().x() - w / 2, rect.center().y() - 2, w, 4)
        split = w * row['winRate']
        # two parts side by side (not stacked), so faded colours do not mix
        clip = QPainterPath()
        clip.addRoundedRect(bar, 2, 2)
        p.save()
        p.setClipPath(clip)
        p.fillRect(QRectF(bar.left(), bar.top(), split, 4), faded(resultColor('win'), dim))
        p.fillRect(QRectF(bar.left() + split, bar.top(), w - split, 4),
                   faded(resultColor('lose'), dim))
        p.restore()

    def __multi(self, p, pal, cell, row, dim, mainSize):
        """four small counts (2x 3x 4x 5x); zeros a faint dash, quadras / pentas in gold"""
        if row['doubleKills'] is None:
            drawText(p, cell, "--", numberFont(mainSize, 600), tint(pal['muted'], 150),
                     Qt.AlignCenter)
            return
        slot = cell.width() / 4
        num = numberFont(mainSize - 1, 600)
        for i, key in enumerate(champion_stats.MULTI_KILLS):
            v = row[key]
            if not v:
                text, color = "–", tint(pal['muted'], 110)
            else:
                text = str(v)
                color = faded(pal['gold'] if key in ('quadraKills', 'pentaKills')
                              else pal['ink'], dim)
            drawText(p, QRectF(cell.left() + i * slot, cell.top(), slot, cell.height()), text,
                     num, color, Qt.AlignCenter)
