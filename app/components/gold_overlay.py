"""
Small always-on-top overlay with the live item value difference (team totals and, when
the game reports lanes, one row per lane: ally value, difference, enemy value), meant to
sit over the game in borderless / windowed mode. It never takes focus, can be dragged
(unless locked: then it is click-through), and is shown only while live data exists and
the "Item value overlay" setting is on. Fed by signalBus.liveGameDataChanged.
"""
from PySide6.QtCore import Qt, QPoint, QRectF
from PySide6.QtGui import QColor, QFontMetrics, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QWidget

from app.common import platform
from app.common.config import cfg
from app.common.logger import logger
from app.common.signals import signalBus
from app.components.design_tokens import numberFont, textFont, resultColor
from app.components.item_gold_panel import goldText, diffText, diffColor
from app.components.paint import drawText
from app.lol.tools import translatePositionKey

TAG = "GoldOverlay"

PAD = 12
INNER_X = PAD + 2  # left / right edge of the text
TOP_H = 116      # header + headline + two total rows
LANE_H = 24
LANES_GAP = 6    # the hairline sits in the middle of it
LEGEND_MARK = 10        # rounded square next to "You" / "Enemy", and over the value columns
LEGEND_RADIUS = 2.5
LEGEND_TEXT_X = 16      # totals rows: label starts this far right of the square's left edge
TOTAL_GAP = 8           # least space between a total's label and its number
LANE_MARKS_H = 12       # band under the hairline with the squares over the value columns
LANES_TOP = TOP_H + LANES_GAP + LANE_MARKS_H  # first lane row

# lane row columns: label | ally value | difference | enemy value. Numbers are written in
# full when they fit their column, else in the compact form (see fitNumber). Sized with
# Bahnschrift / Segoe UI for the longest label (zh_CN, 2 CJK characters, 28 px), a
# five-digit value ("88,888", 38-39 px) and a four-digit signed difference ("−8,888",
# 62 px, as wide as the widest compact one, "−88.8k")
LANE_LABEL_W = 30
LANE_VALUE_W = 40
LANE_DIFF_W = 64
COL_GAP = 6
INNER_W = LANE_LABEL_W + 2 * LANE_VALUE_W + LANE_DIFF_W + 3 * COL_GAP
WIDTH = INNER_W + 2 * INNER_X

MISSING = "—"  # em dash for a lane side without a player (uneven teams)

# dark card regardless of the app theme: it sits over the game
COLORS = {
    'bg': QColor(15, 18, 23, 230),
    'border': QColor(255, 255, 255, 34),
    'ink': QColor('#F2F4F7'),
    'label': QColor('#E6E9EE'),
    'value': QColor('#D3D8E0'),  # lane item values: readable, quieter than the difference
    'muted': QColor('#A3AAB6'),
    'hairline': QColor(255, 255, 255, 30),
}


def overlayAllowed():
    """Never put a real window on screen from offscreen runs (tests / render scripts)"""
    return QGuiApplication.platformName() not in ('offscreen', 'minimal')


def overlayPosition(value):
    """Saved [x, y] -> QPoint, or None (default spot) when missing or corrupted"""
    if not isinstance(value, (list, tuple)):
        return None
    try:
        x, y = value
        return QPoint(int(x), int(y))
    except (TypeError, ValueError, OverflowError):
        return None


def overlayHeight(summary):
    if summary is None or not summary.get('hasLanes'):
        return TOP_H + PAD
    return LANES_TOP + len(summary['lanes']) * LANE_H + PAD


def topFonts():
    """Fonts of the headline difference and the totals rows"""
    return {'headline': numberFont(36, 700), 'label': textFont(14, True),
            'total': numberFont(17, 600)}


def laneFonts():
    """Font of each lane column"""
    value = numberFont(13, 500)
    return {'label': textFont(13, True), 'ally': value, 'diff': numberFont(19, 600),
            'enemy': value}


def textWidth(font, text):
    fm = QFontMetrics(font)
    return max(fm.horizontalAdvance(text), fm.boundingRect(text).width())


def fitNumber(full, compact, font, width):
    """full when it fits width with font, else compact: a number is never elided"""
    return full if textWidth(font, full) <= width else compact


def headlineText(diff):
    return fitNumber(diffText(diff), diffText(diff, True), topFonts()['headline'], INNER_W)


def totalSpace(label):
    """Width left for the number of a totals row after its square and label"""
    fonts = topFonts()
    return INNER_W - LEGEND_TEXT_X - textWidth(fonts['label'], label) - TOTAL_GAP


def totalText(value, label):
    return fitNumber(goldText(value), goldText(value, True), topFonts()['total'],
                     totalSpace(label))


def laneMarks():
    """Squares over the ally / enemy value columns, under the hairline: {'ally', 'enemy'}"""
    cols = laneColumns(0)
    y = TOP_H + LANES_GAP + (LANE_MARKS_H - LEGEND_MARK) / 2
    return {key: QRectF(cols[key].center().x() - LEGEND_MARK / 2, y, LEGEND_MARK, LEGEND_MARK)
            for key in ('ally', 'enemy')}


def laneColumns(i):
    """Rects of lane row i: {'label', 'ally', 'diff', 'enemy'}, left to right"""
    y = LANES_TOP + i * LANE_H
    x, rects = INNER_X, {}
    for key, w in (('label', LANE_LABEL_W), ('ally', LANE_VALUE_W),
                   ('diff', LANE_DIFF_W), ('enemy', LANE_VALUE_W)):
        rects[key] = QRectF(x, y, w, LANE_H)
        x += w + COL_GAP
    return rects


def laneCells(lane):
    """(text, colour) of each lane column. A side without a player shows a muted dash,
    and so does the difference then (it would only repeat the other side's value)"""
    fonts, c = laneFonts(), COLORS

    def value(key):
        player = lane.get(key)
        if player is None:
            return MISSING, c['muted']
        v = player.get('value')
        return fitNumber(goldText(v), goldText(v, True), fonts[key], LANE_VALUE_W), c['value']

    if lane.get('ally') is None or lane.get('enemy') is None:
        diff = MISSING, c['muted']
    else:
        d = lane['diff']
        diff = (fitNumber(diffText(d), diffText(d, True), fonts['diff'], LANE_DIFF_W),
                diffColor(d, c['ink']))
    return {'label': (translatePositionKey(lane['position']) or "", c['label']),
            'ally': value('ally'), 'diff': diff, 'enemy': value('enemy')}


class GoldOverlay(QWidget):

    def __init__(self):
        super().__init__(None)
        self.summary = None
        self._dragOffset = None

        # macOS hides Qt.Tool windows when the app deactivates (the game gets focus)
        # unless this is set before the native window exists; no effect elsewhere
        self.setAttribute(Qt.WA_MacAlwaysShowToolWindow)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.NoFocus)
        self.setWindowTitle("Seraphine - " + self.tr("Item value"))
        self.__applyFlags()
        self.setWindowOpacity(cfg.get(cfg.goldOverlayOpacity) / 100)
        self.resize(WIDTH, overlayHeight(None))

        signalBus.liveGameDataChanged.connect(self.setSummary)
        cfg.enableGoldOverlay.valueChanged.connect(lambda _: self.__updateVisible())
        cfg.goldOverlayOpacity.valueChanged.connect(
            lambda v: self.setWindowOpacity(v / 100))
        cfg.goldOverlayLocked.valueChanged.connect(lambda _: self.__onLockChanged())

    # ---------------- state ----------------

    def setSummary(self, summary):
        try:
            self.summary = summary
            if summary is not None:
                self.resize(WIDTH, overlayHeight(summary))
            self.__updateVisible()
            self.update()
        except Exception as e:
            logger.error(f"item value overlay update failed: {type(e).__name__}", TAG)

    def __updateVisible(self):
        try:
            wanted = (self.summary is not None and cfg.get(cfg.enableGoldOverlay)
                      and overlayAllowed())
            if wanted and not self.isVisible():
                self.__placeOnScreen()
                self.show()
                platform.set_window_no_activate(self.winId())
            elif not wanted and self.isVisible():
                self.hide()
        except Exception as e:
            logger.error(f"item value overlay visibility failed: {type(e).__name__}", TAG)

    def __applyFlags(self):
        flags = (Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                 | Qt.WindowDoesNotAcceptFocus | Qt.NoDropShadowWindowHint)
        locked = bool(cfg.get(cfg.goldOverlayLocked))
        if locked:
            flags |= Qt.WindowTransparentForInput
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, locked)

    def __onLockChanged(self):
        try:
            visible = self.isVisible()
            self.__applyFlags()  # recreates the native window and hides it
            if visible:
                self.show()
                platform.set_window_no_activate(self.winId())
        except Exception as e:
            logger.error(f"item value overlay lock failed: {type(e).__name__}", TAG)

    def __placeOnScreen(self):
        point = overlayPosition(cfg.get(cfg.goldOverlayPos))
        screen = (QGuiApplication.screenAt(point) if point is not None else None) \
            or QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        maxX = area.x() + area.width() - self.width()
        maxY = area.y() + area.height() - self.height()
        if point is None:
            point = QPoint(maxX - 24, area.y() + 160)
        self.move(min(max(point.x(), area.x()), maxX), min(max(point.y(), area.y()), maxY))

    # ---------------- dragging ----------------

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._dragOffset = e.globalPosition().toPoint() - self.pos()
        e.accept()

    def mouseMoveEvent(self, e):
        if self._dragOffset is not None:
            self.move(e.globalPosition().toPoint() - self._dragOffset)
        e.accept()

    def mouseReleaseEvent(self, e):
        if self._dragOffset is not None:
            self._dragOffset = None
            cfg.set(cfg.goldOverlayPos, [self.x(), self.y()])
        e.accept()

    # ---------------- painting ----------------

    def paintEvent(self, e):
        if self.summary is None:
            return
        try:
            p = QPainter(self)
            p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
            self.drawContent(p)
            p.end()
        except Exception as e:
            logger.error(f"item value overlay paint failed: {type(e).__name__}", TAG)

    def drawContent(self, p):
        s, c = self.summary, COLORS
        rect = QRectF(0, 0, self.width(), overlayHeight(s)).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(c['border'], 1))
        p.setBrush(c['bg'])
        p.drawRoundedRect(rect, 10, 10)

        x, w = INNER_X, INNER_W
        win, lose = resultColor('win'), resultColor('lose')

        drawText(p, QRectF(x, PAD - 2, w, 16), self.tr("Item value").upper(),
                 textFont(11, True), c['muted'])
        minutes, secs = divmod(int(s['gameTime']), 60)
        drawText(p, QRectF(x, PAD - 2, w, 16), f"{minutes}:{secs:02d}", numberFont(12, 500),
                 c['muted'], Qt.AlignRight | Qt.AlignVCenter)

        top = topFonts()
        drawText(p, QRectF(x, PAD + 14, w, 40), headlineText(s['diff']), top['headline'],
                 diffColor(s['diff'], c['ink']))

        if s['localKnown']:
            labels = (self.tr("You"), self.tr("Enemy"))
        else:
            labels = (self.tr("Blue"), self.tr("Red"))
        for j, (label, value, color) in enumerate(((labels[0], s['allyTotal'], win),
                                                   (labels[1], s['enemyTotal'], lose))):
            r = QRectF(x, PAD + 60 + j * 22, w, 20)
            p.setPen(Qt.NoPen)
            p.setBrush(color)
            p.drawRoundedRect(QRectF(r.left(), r.center().y() - LEGEND_MARK / 2, LEGEND_MARK,
                                     LEGEND_MARK), LEGEND_RADIUS, LEGEND_RADIUS)
            drawText(p, r.adjusted(LEGEND_TEXT_X, 0, 0, 0), label, top['label'], color)
            drawText(p, r, totalText(value, label), top['total'], c['ink'],
                     Qt.AlignRight | Qt.AlignVCenter)

        if not s['hasLanes']:
            return

        y = TOP_H + LANES_GAP / 2
        p.setPen(QPen(c['hairline'], 1))
        p.drawLine(int(x), int(y), int(x + w), int(y))
        # which value column is whose: the legend's squares again, over each column
        p.setPen(Qt.NoPen)
        for key, color in (('ally', win), ('enemy', lose)):
            p.setBrush(color)
            p.drawRoundedRect(laneMarks()[key], LEGEND_RADIUS, LEGEND_RADIUS)

        fonts = laneFonts()
        right = Qt.AlignRight | Qt.AlignVCenter
        align = {'label': Qt.AlignLeft | Qt.AlignVCenter, 'ally': right,
                 'diff': Qt.AlignCenter, 'enemy': right}
        for i, lane in enumerate(s['lanes']):
            cols = laneColumns(i)
            for key, (text, color) in laneCells(lane).items():
                drawText(p, cols[key], text, fonts[key], color, align[key])
