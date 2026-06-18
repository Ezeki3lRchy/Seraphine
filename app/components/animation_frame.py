from PySide6.QtWidgets import QFrame
from PySide6.QtCore import Qt, Signal, Property
from PySide6.QtGui import QColor, QPainter, QPainterPath
from app.common.qfluentwidgets import BackgroundAnimationWidget, isDarkTheme

from app.common.style_sheet import ColorChangeable


class CardWidget(BackgroundAnimationWidget, QFrame):
    clicked = Signal()
    pressed = Signal()

    def __init__(self, parent=None, type=None):
        # qfluentwidgets' BackgroundAnimationWidget is a cooperative mixin whose
        # __init__ does `super().__init__(*args, **kwargs)`, i.e. it initialises
        # the QFrame C++ part itself via the MRO. Under PySide6 we must therefore
        # initialise the widget exactly ONCE through the cooperative super()
        # chain — calling QFrame.__init__ separately AND BackgroundAnimationWidget
        # .__init__ double-inits the same C++ object ("can't initialize ... twice")
        # and the `type=` kwarg would also leak into QFrame. PyQt5's fork tolerated
        # both. `type` is the app's colour-group key, consumed by ColorChangeable
        # subclasses (see ColorAnimationFrame), not by Qt — keep it out of super().
        super().__init__(parent)
        self.type = type
        self._isClickEnabled = False
        self._borderRadius = 4

    def mousePressEvent(self, e):
        super().mousePressEvent(e)
        self.pressed.emit()

    def mouseReleaseEvent(self, e):
        super().mouseReleaseEvent(e)
        self.clicked.emit()

    def setClickEnabled(self, isEnabled: bool):
        self._isClickEnabled = isEnabled
        self.update()

    def isClickEnabled(self):
        return self._isClickEnabled

    def _normalBackgroundColor(self):
        return QColor(233, 233, 233, 13 if isDarkTheme() else 170)

    def _hoverBackgroundColor(self):
        return QColor(243, 243, 243, 21 if isDarkTheme() else 127)

    def _pressedBackgroundColor(self):
        return QColor(255, 255, 255, 8 if isDarkTheme() else 64)

    def getBorderRadius(self):
        return self._borderRadius

    def setBorderRadius(self, radius: int):
        self._borderRadius = radius
        self.update()

    def paintEvent(self, e):
        painter = QPainter(self)
        painter.setRenderHints(QPainter.Antialiasing)

        r = self.borderRadius

        # draw background
        painter.setPen(Qt.NoPen)
        rect = self.rect().adjusted(1, 1, -1, -1)
        painter.setBrush(self.backgroundColor)
        painter.drawRoundedRect(rect, r, r)

    borderRadius = Property(int, getBorderRadius, setBorderRadius)


class ColorAnimationFrame(CardWidget, ColorChangeable):
    def __init__(self, type: str = None, parent=None):
        # Seed the colour attributes BEFORE CardWidget.__init__: the cooperative
        # super() chain inside it constructs a BackgroundColorObject that calls
        # our _normalBackgroundColor() before setColor() has run. Real values are
        # filled in immediately after by ColorChangeable.__init__ -> setColor.
        self.normalBackgroundColor = QColor(0, 0, 0, 0)
        self.hoverBackgroundColor = QColor(0, 0, 0, 0)
        self.pressedBackgroundColor = QColor(0, 0, 0, 0)
        # CardWidget initialises the QFrame/BackgroundAnimationWidget half via a
        # single cooperative super() chain. Then register this widget's colour
        # group with the colour manager via ColorChangeable.__init__ (which calls
        # our setColor with the current theme colours). ColorChangeable is a plain
        # mixin (not a QObject) so this does not re-init any Qt C++ object.
        CardWidget.__init__(self, parent=parent, type=type)
        ColorChangeable.__init__(self, type)

    def setColor(self, c1: QColor, c2: QColor, c3: QColor, c4: QColor):
        self.normalBackgroundColor = c1
        self.hoverBackgroundColor = c2
        self.pressedBackgroundColor = c3

        # 只负责颜色，不负责宽度等
        self.setStyleSheet(
            f"ColorAnimationFrame {{border-color: {c4.name(QColor.HexArgb)};}}")

        try:
            # 由于同样原因，这里不 try 就报错咯 ^^_
            self._updateBackgroundColor()
        except:
            return

    def _normalBackgroundColor(self):
        return self.normalBackgroundColor

    def _hoverBackgroundColor(self):
        return self.hoverBackgroundColor

    def _pressedBackgroundColor(self):
        return self.pressedBackgroundColor


class NoBorderColorAnimationFrame(CardWidget, ColorChangeable):
    def __init__(self, type: str = None, parent=None):
        # Seed colour attrs before CardWidget.__init__ (see ColorAnimationFrame).
        self.normalBackgroundColor = QColor(0, 0, 0, 0)
        self.hoverBackgroundColor = QColor(0, 0, 0, 0)
        self.pressedBackgroundColor = QColor(0, 0, 0, 0)
        # See ColorAnimationFrame: single cooperative init via CardWidget, then
        # register the colour group via the plain ColorChangeable mixin.
        CardWidget.__init__(self, parent=parent, type=type)
        ColorChangeable.__init__(self, type)

    def setColor(self, c1: QColor, c2: QColor, c3: QColor, c4: QColor):
        self.normalBackgroundColor = c1
        self.hoverBackgroundColor = c2
        self.pressedBackgroundColor = c3

        try:
            # 由于同样原因，这里不 try 就报错咯 ^^_
            self._updateBackgroundColor()
        except:
            return

    def _normalBackgroundColor(self):
        return self.normalBackgroundColor

    def _hoverBackgroundColor(self):
        return self.hoverBackgroundColor

    def _pressedBackgroundColor(self):
        return self.pressedBackgroundColor
