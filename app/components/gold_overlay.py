"""
Small always-on-top overlay with the live item value difference (team totals and, when
the game reports lanes, the five lane differences), meant to sit over the game in
borderless / windowed mode. It never takes focus, can be dragged (unless locked: then it
is click-through), and is shown only while live data exists and the "Item value overlay"
setting is on. Fed by signalBus.liveGameDataChanged.
"""
from PySide6.QtCore import Qt, QPoint, QRectF
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QWidget

from app.common import platform
from app.common.config import cfg
from app.common.logger import logger
from app.common.signals import signalBus
from app.components.design_tokens import numberFont, textFont, resultColor
from app.components.item_gold_panel import goldText, diffText, diffColor
from app.components.scoreboard import drawText
from app.lol.tools import translatePositionKey

TAG = "GoldOverlay"

WIDTH = 196
PAD = 12
TOP_H = 116      # header + headline + two total rows
LANE_H = 24
LANES_GAP = 6

# dark card regardless of the app theme: it sits over the game
COLORS = {
    'bg': QColor(15, 18, 23, 230),
    'border': QColor(255, 255, 255, 34),
    'ink': QColor('#F2F4F7'),
    'label': QColor('#E6E9EE'),
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
    return TOP_H + LANES_GAP + len(summary['lanes']) * LANE_H + PAD


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
            logger.error(f"item value overlay update failed: {e!r}", TAG)

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
            logger.error(f"item value overlay visibility failed: {e!r}", TAG)

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
            logger.error(f"item value overlay lock failed: {e!r}", TAG)

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
            logger.error(f"item value overlay paint failed: {e!r}", TAG)

    def drawContent(self, p):
        s, c = self.summary, COLORS
        rect = QRectF(0, 0, self.width(), overlayHeight(s)).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(c['border'], 1))
        p.setBrush(c['bg'])
        p.drawRoundedRect(rect, 10, 10)

        x, w = PAD + 2, WIDTH - 2 * PAD - 4
        win, lose = resultColor('win'), resultColor('lose')

        drawText(p, QRectF(x, PAD - 2, w, 16), self.tr("Item value").upper(),
                 textFont(11, True), c['muted'])
        minutes, secs = divmod(int(s['gameTime']), 60)
        drawText(p, QRectF(x, PAD - 2, w, 16), f"{minutes}:{secs:02d}", numberFont(12, 500),
                 c['muted'], Qt.AlignRight | Qt.AlignVCenter)

        headline = self.tr("Even") if s['even'] else diffText(s['diff'], True)
        drawText(p, QRectF(x, PAD + 14, w, 40), headline, numberFont(36, 700),
                 diffColor(s['diff'], s['even'], c['ink']))

        if s['localKnown']:
            labels = (self.tr("You"), self.tr("Enemy"))
        else:
            labels = (self.tr("Blue"), self.tr("Red"))
        for j, (label, value, color) in enumerate(((labels[0], s['allyTotal'], win),
                                                   (labels[1], s['enemyTotal'], lose))):
            r = QRectF(x, PAD + 60 + j * 22, w, 20)
            p.setPen(Qt.NoPen)
            p.setBrush(color)
            p.drawRoundedRect(QRectF(r.left(), r.center().y() - 5, 10, 10), 2.5, 2.5)
            drawText(p, r.adjusted(16, 0, 0, 0), label, textFont(14, True), color)
            drawText(p, r, goldText(value, True), numberFont(17, 600), c['ink'],
                     Qt.AlignRight | Qt.AlignVCenter)

        if not s['hasLanes']:
            return

        y = TOP_H + LANES_GAP / 2
        p.setPen(QPen(c['hairline'], 1))
        p.drawLine(int(x), int(y), int(x + w), int(y))
        for i, lane in enumerate(s['lanes']):
            row = QRectF(x, TOP_H + LANES_GAP + i * LANE_H, w, LANE_H)
            drawText(p, row, translatePositionKey(lane['position']) or "", textFont(13, True),
                     c['label'])
            text = self.tr("Even") if lane['even'] else diffText(lane['diff'], True)
            drawText(p, row, text, numberFont(19, 600),
                     diffColor(lane['diff'], lane['even'], c['ink']),
                     Qt.AlignRight | Qt.AlignVCenter)
