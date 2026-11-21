"""
Cooldown ledger: a small always-on-top panel with one row per enemy on Summoner's Rift, in
the fixed order top, jungle, mid, bottom, support. Each row: lane icon, Q W E R, Flash (always
the left spell cell), the other summoner spell, up to three item actives and, on the top row
only, Teleport when the top laner does not carry it. A click starts a timer of that ability's
usual cooldown and a second click cancels it; nothing is detected. Q W E R count down in
seconds, the other cells show the game time they come back over the dimmed icon. A cell whose
cooldown cannot be estimated does nothing.

The panel never takes focus and never lets clicks through. The lock (the bottom-right corner)
is the item value overlay's lock, cfg.goldOverlayLocked: it only stops dragging here. The
background follows the overlay's opacity setting (read only). Shown only while the "Cooldown
ledger" setting is on and ledgerVisible(summary) holds. Fed by signalBus.liveGameDataChanged;
the timers live in app.lol.ledger_model.LedgerTimers.
"""
import asyncio
import math

from PySide6.QtCore import Qt, QPoint, QPointF, QRectF, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics, QGuiApplication, QImage, QPainter, \
    QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QWidget

from app.common import platform
from app.common.config import cfg
from app.common.logger import logger
from app.common.signals import signalBus
from app.components.design_tokens import numberFont, textFont
from app.components.gold_overlay import overlayAllowed, overlayPosition
from app.components.opgg_build_widgets import svgPixmap
from app.components.paint import drawRoundedPixmap, drawText
from app.lol.ledger_model import TELEPORT, LedgerTimers, cellText, championEntry, cooldown, \
    ledgerVisible, spellForCell

TAG = "LedgerPanel"

LANES = ("TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY")  # row order, fixed
LANE_ICONS = {'TOP': 'top', 'JUNGLE': 'jng', 'MIDDLE': 'mid', 'BOTTOM': 'bot', 'UTILITY': 'sup'}
ABILITY_CELLS = ('Q', 'W', 'E', 'R')

PAD = 4
LANE_W = 28           # lane icon column
ABILITY_W = 36        # Q W E R: clicked often in lane, larger; seconds left ("120")
SLOT_W = 30           # spells, items, TP: clicked rarely, smaller; return time ("14:10")
ROW_H = 28
ICON = 22             # spell / item icon inside a slot
LANE_ICON = 22
ITEM_SLOTS = 3
SLOT_COLUMNS = ('D', 'F') + tuple(f'item{i}' for i in range(ITEM_SLOTS)) + ('TP',)
LOCK_ROW, LOCK_COLUMN = len(LANES) - 1, 'TP'  # the TP column is used on the top row only
CLICK_SLOP = 5        # press -> release within this many px is a click, not a drag
TICK_MS = 500
BG_ALPHA = 230        # background alpha at 100 % overlay opacity


def _columns():
    """[(column id, x, width)] left to right; no gaps between clickable cells"""
    x, cols = PAD, [('lane', PAD, LANE_W)]
    x += LANE_W
    for key in ABILITY_CELLS:
        cols.append((key, x, ABILITY_W))
        x += ABILITY_W
    for key in SLOT_COLUMNS:
        cols.append((key, x, SLOT_W))
        x += SLOT_W
    return cols


COLUMNS = _columns()
WIDTH = COLUMNS[-1][1] + COLUMNS[-1][2] + PAD
HEIGHT = 2 * PAD + len(LANES) * ROW_H

# shown until a spell's icon has loaded (or when it cannot be loaded)
SPELL_LABELS = {
    'SummonerFlash': 'Fl', 'SummonerDot': 'Ig', 'SummonerTeleport': 'TP',
    'S12_SummonerTeleportUpgrade': 'TP', 'SummonerSmite': 'Sm',
    'S5_SummonerSmitePlayerGanker': 'Sm', 'SummonerHeal': 'He', 'SummonerBarrier': 'Ba',
    'SummonerExhaust': 'Ex', 'SummonerHaste': 'Gh', 'SummonerBoost': 'Cl',
    'SummonerMana': 'Cr',
}
ITEM_LABEL = 'It'

# dark panel regardless of the app theme: it sits over the game (as the item value overlay)
COLORS = {
    'bg': QColor(15, 18, 23, BG_ALPHA),
    'border': QColor(255, 255, 255, 34),
    'cell': QColor(255, 255, 255, 12),
    'cellLine': QColor(255, 255, 255, 26),
    # running: Q W E R (seconds left) blue, spells / items / TP (return time) gold
    'runningAbility': QColor(90, 150, 230, 80),
    'runningAbilityLine': QColor(120, 170, 240, 200),
    'running': QColor(200, 170, 110, 70),
    'runningLine': QColor(200, 170, 110, 190),
    'ink': QColor('#F2F4F7'),
    'label': QColor('#D3D8E0'),
    'lane': QColor('#E6E9EE'),
    'muted': QColor('#8A92A2'),
    'inactive': QColor(255, 255, 255, 60),
    'corner': QColor('#B4BCCA'),  # the ability letter of a running Q W E R cell
    'dim': QColor(0, 0, 0, 120),  # over a running icon: still recognisable behind the time
    'outline': QColor(0, 0, 0, 230),
}
INACTIVE_OPACITY = 0.3   # an inestimable cell's icon, like its text colour


def abilityFont():
    return textFont(12, True)


def cornerFont():
    """The small ability letter in the corner of a running Q W E R cell"""
    return textFont(9, True)


def secondsFont():
    return numberFont(15, 600)


def returnFont():
    return numberFont(11, 600)


def slotLabelFont():
    return textFont(11, True)


def textWidth(font, text):
    fm = QFontMetrics(font)
    return max(fm.horizontalAdvance(text), fm.boundingRect(text).width())


def spellLabel(key):
    if key in SPELL_LABELS:
        return SPELL_LABELS[key]
    name = key.split('_')[-1]
    if name.startswith('Summoner'):
        name = name[len('Summoner'):]
    return name[:2] or '?'


def columnRect(column, row):
    for key, x, w in COLUMNS:
        if key == column:
            return QRectF(x, PAD + row * ROW_H, w, ROW_H)
    raise KeyError(column)


def abilityNumberRect(rect):
    """Where a running Q W E R cell draws its seconds: a little right of and below the
    corner letter"""
    return rect.adjusted(4, 8, 0, 0)


def cornerRect(rect):
    """The small ability letter of a running Q W E R cell, top-left"""
    return QRectF(rect.left() + 3, rect.top() + 1, 10, 10)


def rowsOf(summary):
    """{position: roster entry} of a summary (rows keyed by position)"""
    if not isinstance(summary, dict):
        return {}
    return {e.get('position'): e for e in summary.get('enemyRoster') or []
            if isinstance(e, dict) and e.get('position') in LANES}


def section(db, name):
    """A top-level part of the ledger database (the merged dict, or the LedgerDatabase holding
    it as .data); {} when missing"""
    data = getattr(db, 'data', db)
    value = data.get(name) if isinstance(data, dict) else None
    return value if isinstance(value, dict) else {}


def isItemCell(cell):
    return isinstance(cell, str) and cell.startswith('item:')


def itemId(cell):
    try:
        return int(cell[len('item:'):])
    except (TypeError, ValueError):
        return None


_tinted = {}
_greyed = {}


def tintedPixmap(path, size, dpr, color):
    """An svg icon drawn in one colour (the lane icons ship a faint grey)"""
    key = (path, size, dpr, color.rgba())
    pixmap = _tinted.get(key)
    if pixmap is None:
        pixmap = QPixmap(svgPixmap(path, size, dpr))
        p = QPainter(pixmap)
        p.setCompositionMode(QPainter.CompositionMode_SourceIn)
        p.fillRect(pixmap.rect(), color)
        p.end()
        _tinted[key] = pixmap
    return pixmap


def greyPixmap(pixmap):
    """A grey copy of an icon (an inestimable cell)"""
    key = pixmap.cacheKey()
    grey = _greyed.get(key)
    if grey is None:
        image = pixmap.toImage()
        alpha = image.convertToFormat(QImage.Format_Alpha8)
        grey = image.convertToFormat(QImage.Format_Grayscale8) \
            .convertToFormat(QImage.Format_ARGB32)
        grey.setAlphaChannel(alpha)
        grey = QPixmap.fromImage(grey)
        grey.setDevicePixelRatio(pixmap.devicePixelRatio())
        _greyed[key] = grey
    return grey


OUTLINE_W = 2.4
_labels = {}
LABEL_CACHE_MAX = 512  # return times change every game second: bound the cache


def outlinedLabel(text, font, color, outline, dpr):
    """Text with a dark outline as a pixmap, built once per (text, font, colours, dpr): a
    frame with many running cells only blits (building the outline path is the costly part)"""
    key = (text, font.key(), color.rgba(), outline.rgba(), dpr)
    pixmap = _labels.get(key)
    if pixmap is None:
        if len(_labels) >= LABEL_CACHE_MAX:
            _labels.clear()
        fm = QFontMetrics(font)
        margin = math.ceil(OUTLINE_W)
        w, h = fm.horizontalAdvance(text) + 2 * margin, fm.height() + 2 * margin
        pixmap = QPixmap(math.ceil(w * dpr), math.ceil(h * dpr))
        pixmap.setDevicePixelRatio(dpr)
        pixmap.fill(Qt.transparent)
        path = QPainterPath()
        path.addText(QPointF(margin, margin + fm.ascent()), font, text)
        p = QPainter(pixmap)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        p.setPen(QPen(outline, OUTLINE_W, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawPath(path)
        p.end()
        _labels[key] = pixmap
    return pixmap


def drawOutlinedText(p, rect, text, font, color, outline, dpr=1.0):
    """Centred text with a dark outline: readable over any icon"""
    pixmap = outlinedLabel(text, font, color, outline, dpr)
    w, h = pixmap.width() / dpr, pixmap.height() / dpr
    p.drawPixmap(QRectF(rect.center().x() - w / 2, rect.center().y() - h / 2, w, h), pixmap,
                 QRectF(pixmap.rect()))


class LedgerPanel(QWidget):

    # [{'championName', 'position'}] of the enemies (championName = the database's name when
    # the roster's rawChampionName is found there), emitted when a summary first passes the
    # gate for a new set of enemies while the setting is on (LedgerDatabase.refresh's input)
    enemiesSeen = Signal(list)

    def __init__(self, db=None, spellIcon=None, itemIcon=None, clock=None, spellKnown=None):
        """
        db: the ledger database (LedgerDatabase, or its merged dict), read on every use, so a
        refresh that replaces its data shows without a call here.
        spellIcon / itemIcon: async loaders id -> image path (connector.getSummonerSpellIcon /
        getItemIcon). spellKnown(id) -> bool: when given, spell icons are requested only for
        ids it accepts (the connector's spell list), so an unknown id never reaches the client
        """
        super().__init__(None)
        self.db = db
        self.summary = None
        self.rows = {}
        self.timers = LedgerTimers(db=db) if clock is None else LedgerTimers(clock=clock, db=db)
        self.wanted = False
        self.hiddenReason = "no game"  # why the panel is hidden; None while it shows
        self._spellIcon, self._itemIcon, self._spellKnown = spellIcon, itemIcon, spellKnown
        self.icons = {}            # ('spell' | 'item', id) -> QPixmap
        self._requested = set()    # icons loaded, loading, failed or skipped (not retried)
        self._tasks = set()
        self._closed = False
        self._press = None         # (global QPoint, window pos, (row, column)) of a left press
        self._dragging = False
        self._lastEnemies = None
        self._failed = set()       # (champion, cell) whose cooldown lookup raised (logged once)

        # macOS hides Qt.Tool windows when the app deactivates (the game gets focus) unless
        # this is set before the native window exists; no effect elsewhere
        self.setAttribute(Qt.WA_MacAlwaysShowToolWindow)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.NoFocus)
        self.setWindowTitle("Seraphine - " + self.tr("Cooldown ledger"))
        # set once: the lock only stops dragging, so the native window is never recreated
        # and the panel is never click-through
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                            | Qt.WindowDoesNotAcceptFocus | Qt.NoDropShadowWindowHint)
        self.setFixedSize(WIDTH, HEIGHT)

        self.ticker = QTimer(self)
        self.ticker.setInterval(TICK_MS)
        self.ticker.timeout.connect(self.__onTick)

        signalBus.liveGameDataChanged.connect(self.setSummary)
        cfg.enableCooldownLedger.valueChanged.connect(self.__onSettingChanged)
        cfg.goldOverlayLocked.valueChanged.connect(self.__onLockChanged)
        cfg.goldOverlayOpacity.valueChanged.connect(self.__onOpacityChanged)

    def disconnectSources(self):
        """Stop following the live data and the settings and cancel pending icon loads
        (tests, shutdown)"""
        self._closed = True
        for signal, slot in ((signalBus.liveGameDataChanged, self.setSummary),
                             (cfg.enableCooldownLedger.valueChanged, self.__onSettingChanged),
                             (cfg.goldOverlayLocked.valueChanged, self.__onLockChanged),
                             (cfg.goldOverlayOpacity.valueChanged, self.__onOpacityChanged)):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        for task in list(self._tasks):
            task.cancel()
        self.ticker.stop()

    # ---------------- state ----------------

    def setSummary(self, summary):
        try:
            self.summary = summary
            gate = ledgerVisible(summary)
            if gate:
                self.rows = rowsOf(summary)
                self.timers.sync(summary.get('gameTime') or 0, list(self.rows.values()))
            self.__updateVisible()
            if gate and self.wanted:
                self.__loadIcons()
                enemies = [{'championName': self.championOf(self.rows[lane]),
                            'position': lane} for lane in LANES]
                if enemies != self._lastEnemies:
                    self._lastEnemies = enemies
                    self.enemiesSeen.emit(enemies)
            self.__updateTicker()
            self.update()
        except Exception as e:
            logger.error(f"ledger panel update failed: {type(e).__name__}", TAG)

    def isLocked(self):
        return bool(cfg.get(cfg.goldOverlayLocked))

    def championOf(self, entry):
        """The champion's name for the database: the roster's rawChampionName (English, e.g.
        'MonkeyKing') when the database knows it, else the displayed championName"""
        raw = entry.get('rawChampionName')
        try:
            if raw and championEntry(self.db, raw) is not None:
                return raw
        except Exception:
            pass
        return entry.get('championName') or ""

    def __onSettingChanged(self, _):
        self.setSummary(self.summary)

    def __onLockChanged(self, _):
        # no setWindowFlags here: the panel stays clickable in both states
        if self._dragging:
            self.__endDrag()
        self.update()

    def __onOpacityChanged(self, _):
        self.update()

    def __reason(self):
        """None when the panel should show, else why not"""
        if not cfg.get(cfg.enableCooldownLedger):
            return "setting off"
        if self.summary is None:
            return "no game"
        if not ledgerVisible(self.summary):
            return "not Summoner's Rift with five lanes"
        return None

    def __updateVisible(self):
        try:
            reason = self.__reason()
            wanted = reason is None
            self.hiddenReason = reason
            if wanted != self.wanted:
                self.wanted = wanted
                if wanted:
                    logger.info("ledger panel shown", TAG)
                else:
                    logger.info(f"ledger panel hidden ({reason})", TAG)
            if wanted and overlayAllowed() and not self.isVisible():
                self.placeOnScreen()
                self.show()
                # as the item value overlay: on every show, in case the native window was
                # recreated
                platform.set_window_no_activate(self.winId())
            elif not wanted and self.isVisible():
                self.hide()
        except Exception as e:
            logger.error(f"ledger panel visibility failed: {type(e).__name__}", TAG)

    def placeOnScreen(self):
        """The saved spot (cooldownLedgerPos) kept inside its screen, or the default one"""
        point = overlayPosition(cfg.get(cfg.cooldownLedgerPos))
        screen = (QGuiApplication.screenAt(point) if point is not None else None) \
            or QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        maxX = area.x() + area.width() - self.width()
        maxY = area.y() + area.height() - self.height()
        if point is None:
            point = QPoint(area.x() + 24, area.y() + 160)
        self.move(min(max(point.x(), area.x()), maxX), min(max(point.y(), area.y()), maxY))

    # ---------------- timers ----------------

    def __updateTicker(self):
        if self.wanted and self.timers.active():
            if not self.ticker.isActive():
                self.ticker.start()
        elif self.ticker.isActive():
            self.ticker.stop()

    def __onTick(self):
        self.update()
        self.__updateTicker()

    def cellIdAt(self, row, column):
        """Timer cell id of a grid cell, 'lock', or None (lane icon, empty slot, no row)"""
        if (row, column) == (LOCK_ROW, LOCK_COLUMN):
            return 'lock'
        if column == 'lane' or not 0 <= row < len(LANES):
            return None
        entry = self.rows.get(LANES[row])
        if entry is None:
            return None
        cells = self.timers.cells(entry)
        if column.startswith('item'):
            # item slots in the model's order; a hole (an entry that is no item cell id)
            # keeps its slot empty
            slots = [c for c in cells if c not in ABILITY_CELLS + ('D', 'F', 'TP')]
            i = int(column[4:])
            cell = slots[i] if i < len(slots) else None
            return cell if isItemCell(cell) and itemId(cell) is not None else None
        return column if column in cells else None

    def duration(self, entry, cell):
        """Estimated cooldown of a cell in seconds, or None (the cell does nothing)"""
        champion = self.championOf(entry)
        try:
            return cooldown(self.db, champion, cell, entry.get('level') or 1,
                            entry.get('items') or [], spells=entry.get('spells'))
        except Exception as e:
            if (champion, cell) not in self._failed:  # paint asks every frame: log once
                self._failed.add((champion, cell))
                logger.warning(f"ledger cooldown failed: {type(e).__name__}", TAG)
            return None

    def clickCell(self, row, column):
        cell = self.cellIdAt(row, column)
        if cell is None:
            return
        if cell == 'lock':
            cfg.set(cfg.goldOverlayLocked, not self.isLocked())
            return
        entry = self.rows[LANES[row]]
        running = self.timers.state(entry['key'], cell)[0] == 'running'
        length = None if running else self.duration(entry, cell)
        if not running and length is None:
            return  # inestimable: the cell does nothing
        self.timers.toggle(entry['key'], cell, length)
        self.__updateTicker()
        self.update()

    # ---------------- mouse: click vs drag ----------------

    @staticmethod
    def gridAt(pos):
        """(row, column id) under a local point, or None outside the grid"""
        x, y = pos.x(), pos.y()
        row = math.floor((y - PAD) / ROW_H)
        if not 0 <= row < len(LANES):
            return None
        for key, cx, w in COLUMNS:
            if cx <= x < cx + w:
                return row, key
        return None

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._press = (e.globalPosition().toPoint(), self.pos(), self.gridAt(e.position()))
            self._dragging = False
        e.accept()

    def mouseDoubleClickEvent(self, e):
        # the double click's first press / release already toggled the cell; this second
        # press must neither cancel it again nor start a drag
        e.accept()

    def mouseMoveEvent(self, e):
        if self._press is not None and not self.isLocked():
            start, origin, _ = self._press
            delta = e.globalPosition().toPoint() - start
            if self._dragging or math.hypot(delta.x(), delta.y()) >= CLICK_SLOP:
                self._dragging = True
                self.move(origin + delta)
        e.accept()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self._press is not None:
            if self._dragging:
                self.__endDrag()
            else:
                start, _, grid = self._press
                self._press = None
                delta = e.globalPosition().toPoint() - start
                near = math.hypot(delta.x(), delta.y()) < CLICK_SLOP
                inside = QRectF(self.rect()).contains(e.position())
                if grid is not None and inside and (near or self.gridAt(e.position()) == grid):
                    self.clickCell(*grid)
        e.accept()

    def __endDrag(self):
        self._press = None
        self._dragging = False
        cfg.set(cfg.cooldownLedgerPos, [self.x(), self.y()])

    # ---------------- icons ----------------

    def __iconKeys(self):
        keys = set()
        spells = section(self.db, 'summonerSpells')
        for entry in self.rows.values():
            for key in list(entry.get('spells') or []) + [TELEPORT]:
                spellId = (spells.get(key) or {}).get('id')
                if spellId:
                    keys.add(('spell', spellId))
            for cell in self.timers.cells(entry):
                if isItemCell(cell) and itemId(cell) is not None:
                    keys.add(('item', itemId(cell)))
        return keys

    def __loadIcons(self):
        for key in self.__iconKeys() - self._requested:
            loader = self._spellIcon if key[0] == 'spell' else self._itemIcon
            if loader is None:
                continue
            if key[0] == 'spell' and self._spellKnown is not None:
                try:
                    known = bool(self._spellKnown(key[1]))
                except Exception:
                    known = False
                if not known:  # the client does not list it: keep the two-letter text
                    self._requested.add(key)
                    continue
            try:
                task = asyncio.get_running_loop().create_task(self.loadIcon(key))
            except RuntimeError:  # no event loop (offscreen scripts)
                continue
            self._requested.add(key)
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def loadIcon(self, key):
        """Load one icon; on any failure the cell keeps its two-letter text (not retried:
        e.g. a KeyError for a spell id the connector's spell list dropped)"""
        self._requested.add(key)
        kind, iconId = key
        loader = self._spellIcon if kind == 'spell' else self._itemIcon
        try:
            path = await loader(iconId)
        except Exception as e:
            logger.warning(f"ledger {kind} icon failed: {type(e).__name__}", TAG)
            return
        if self._closed:  # the panel went away while the icon loaded
            return
        try:
            pixmap = QPixmap(path)
            if pixmap.isNull():
                return
            self.icons[key] = pixmap
            self.update()
        except RuntimeError:  # the widget was deleted meanwhile
            return

    def __icon(self, kind, iconId):
        return self.icons.get((kind, iconId)) if iconId is not None else None

    # ---------------- painting ----------------

    def paintEvent(self, e):
        if not self.rows:
            return
        try:
            p = QPainter(self)
            p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing
                             | QPainter.SmoothPixmapTransform)
            self.drawContent(p)
            p.end()
        except Exception as e:
            logger.error(f"ledger panel paint failed: {type(e).__name__}", TAG)

    def cellView(self, row, column):
        """What a grid cell draws: dict(kind, text, running, active, icon) or None (blank).
        kind: 'lane', 'ability', 'slot', 'lock'"""
        if (row, column) == (LOCK_ROW, LOCK_COLUMN):
            return {'kind': 'lock', 'text': "", 'running': False, 'active': True, 'icon': None}
        entry = self.rows.get(LANES[row])
        if column == 'lane':
            return {'kind': 'lane', 'text': "", 'running': False, 'active': True,
                    'icon': LANE_ICONS[LANES[row]]}
        cell = self.cellIdAt(row, column)
        if entry is None or cell is None:
            return None
        state = self.timers.state(entry['key'], cell)
        running = state[0] == 'running'
        active = running or self.duration(entry, cell) is not None
        if cell in ABILITY_CELLS:
            text = cellText(cell, state) if running else cell
            return {'kind': 'ability', 'text': text, 'running': running, 'active': active,
                    'icon': None, 'cell': cell}
        spells = section(self.db, 'summonerSpells')
        if isItemCell(cell):
            icon, label = self.__icon('item', itemId(cell)), ITEM_LABEL
        else:
            key = TELEPORT if cell == 'TP' else spellForCell(entry, cell)
            if not key:
                return None
            icon = self.__icon('spell', (spells.get(key) or {}).get('id'))
            label = spellLabel(key)
        text = cellText(cell, state) if running else ("" if icon is not None else label)
        return {'kind': 'slot', 'text': text, 'running': running, 'active': active,
                'icon': icon, 'cell': cell}

    def backgroundColor(self):
        """The panel's background at the item value overlay's opacity setting"""
        bg = QColor(COLORS['bg'])
        bg.setAlpha(round(BG_ALPHA * cfg.get(cfg.goldOverlayOpacity) / 100))
        return bg

    def drawContent(self, p):
        p.setPen(QPen(COLORS['border'], 1))
        p.setBrush(self.backgroundColor())
        p.drawRoundedRect(QRectF(0, 0, WIDTH, HEIGHT).adjusted(0.5, 0.5, -0.5, -0.5), 6, 6)
        device = p.device()  # the painted device's ratio (a widget, or an image in tests)
        dpr = device.devicePixelRatioF() if device is not None else self.devicePixelRatioF()
        for row in range(len(LANES)):
            for column, _, _ in COLUMNS:
                view = self.cellView(row, column)
                if view is not None:
                    self.__drawCell(p, columnRect(column, row), view, dpr)

    def __drawCell(self, p, rect, view, dpr):
        c, kind = COLORS, view['kind']
        if kind == 'lane':
            icon = tintedPixmap(f"app/resource/images/icon-position-{view['icon']}.svg",
                                LANE_ICON, dpr, c['lane'])
            p.drawPixmap(QRectF(rect.center().x() - LANE_ICON / 2,
                                rect.center().y() - LANE_ICON / 2, LANE_ICON,
                                LANE_ICON).toRect(), icon)
            return
        if kind == 'lock':
            self.__drawLock(p, rect, self.isLocked())
            return

        inner = rect.adjusted(0.5, 0.5, -0.5, -0.5)
        running = view['running']
        ability = kind == 'ability'
        if running:
            line = c['runningAbilityLine'] if ability else c['runningLine']
            fill = c['runningAbility'] if ability else c['running']
        else:
            line, fill = c['cellLine'], c['cell']
        p.setPen(QPen(line, 1))
        p.setBrush(fill)
        p.drawRect(inner)

        if ability:
            if running:
                # the seconds, plus which ability they belong to in the corner
                drawText(p, cornerRect(rect), view['cell'], cornerFont(), c['corner'],
                         Qt.AlignLeft | Qt.AlignTop)
                drawText(p, abilityNumberRect(rect), view['text'], secondsFont(), c['ink'],
                         Qt.AlignCenter)
            else:
                color = c['label'] if view['active'] else c['inactive']
                drawText(p, rect, view['text'], abilityFont(), color, Qt.AlignCenter)
            return

        icon = view['icon']
        if icon is not None:
            target = QRectF(rect.center().x() - ICON / 2, rect.center().y() - ICON / 2, ICON,
                            ICON)
            p.save()
            if not view['active']:
                p.setOpacity(INACTIVE_OPACITY)
                icon = greyPixmap(icon)
            drawRoundedPixmap(p, icon, target, 3)
            p.restore()
            if running:
                p.setPen(Qt.NoPen)
                p.setBrush(c['dim'])
                p.drawRoundedRect(target, 3, 3)
        if view['text']:
            if running:
                drawOutlinedText(p, rect, view['text'], returnFont(), c['ink'], c['outline'],
                                 dpr)
            else:
                color = c['label'] if view['active'] else c['inactive']
                drawText(p, rect, view['text'], slotLabelFont(), color, Qt.AlignCenter)

    def __drawLock(self, p, rect, locked):
        """No cell chrome: a thin separator, then a padlock, closed and grey when locked,
        open and gold when unlocked (the panel can be dragged)"""
        c = COLORS
        p.setPen(QPen(c['cellLine'], 1))
        p.drawLine(QPointF(rect.left() + 2.5, rect.top() + 5),
                   QPointF(rect.left() + 2.5, rect.bottom() - 5))
        color = c['label'] if locked else c['runningLine']
        cx, cy = rect.center().x() + 1.5, rect.center().y() + 1
        body = QRectF(cx - 5.5, cy - 1, 11, 8)
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawRoundedRect(body, 2, 2)
        shackle = QPainterPath()
        lift = 0 if locked else 3
        left, right, top = cx - 3.25, cx + 3.25, cy - 7.5 - lift
        shackle.moveTo(QPointF(right, body.top() - (3 if not locked else 0)))
        shackle.lineTo(QPointF(right, top + 3.25))
        shackle.arcTo(QRectF(left, top, 6.5, 6.5), 0, 180)
        shackle.lineTo(QPointF(left, body.top()))
        p.setPen(QPen(color, 1.6))
        p.setBrush(Qt.NoBrush)
        p.drawPath(shackle)
