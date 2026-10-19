"""
高刷新率平滑滚动.

qfluentwidgets 自带的平滑滚动在高刷屏上不顺:
- 默认引擎以固定 60fps 推进, 240Hz / 300Hz 屏上每 4 帧才动一次;
- 时间驱动的引擎把每帧的位移取整后再累加, 小位移会在 0 / 1 像素间跳;
- 滚动条本身的动画走 Qt 动画计时器 (约 60Hz).

这里换成: 按所在屏幕的刷新率开精确计时器, 每帧用经过的真实时间把 "当前位置" 向 "目标位置"
指数逼近 (浮点累计, 只在写入滚动条时取整), 直接设置滚动条的值. 每次滚轮把目标位置推远,
连续滚动会自然叠加. 通过替换 SmoothScroll.wheelEvent 全局生效, 所有 SmoothScrollArea /
ScrollArea 都受益.
"""
import math

from PySide6.QtCore import QObject, QTimer, QElapsedTimer, Qt, QEvent
from PySide6.QtWidgets import QAbstractScrollArea, QApplication

# 一格滚轮 (120) 滚动的像素数, 与 qfluentwidgets 默认 (stepRatio 1.5) 的手感接近
PIXELS_PER_NOTCH = 150
# 逼近时间常数 (ms): 越小越跟手, 越大越 "滑"
TIME_CONSTANT_MS = 55
# 连续快速滚动时的加速 (500ms 内滚了多少格)
ACCELERATION_WINDOW_MS = 500
MAX_ACCELERATION = 2.0


def refreshInterval(widget):
    """所在屏幕一帧的毫秒数 (取不到时按 60Hz)"""
    screen = widget.screen() if widget is not None else None
    if screen is None:
        screen = QApplication.primaryScreen()
    rate = screen.refreshRate() if screen is not None else 60
    rate = rate if rate and rate > 1 else 60
    return max(1, int(1000 / rate))


class HighRefreshScroller(QObject):
    """一个滚动方向的平滑滚动器"""

    def __init__(self, area: QAbstractScrollArea, orient=Qt.Vertical):
        super().__init__(area)
        self.area = area
        self.orient = orient
        self.current = None     # 浮点当前位置
        self.target = None      # 浮点目标位置
        self.lastWritten = None
        self.notchTimes = []

        self.clock = QElapsedTimer()
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.timeout.connect(self._step)

        self.bar().valueChanged.connect(self._onValueChanged)

    def bar(self):
        return self.area.verticalScrollBar() if self.orient == Qt.Vertical \
            else self.area.horizontalScrollBar()

    def _onValueChanged(self, value):
        # 拖动滚动条 / 程序设置位置时, 以实际位置为准, 中止未完成的平滑滚动
        if value != self.lastWritten:
            self.current = self.target = float(value)
            self.timer.stop()

    def wheel(self, e):
        bar = self.bar()
        angle = e.angleDelta()
        delta = angle.y() if angle.y() != 0 else angle.x()
        pixel = e.pixelDelta()
        pixelDelta = pixel.y() if pixel.y() != 0 else pixel.x()

        if self.current is None or not self.timer.isActive():
            self.current = self.target = float(bar.value())

        if pixelDelta and abs(delta) % 120 != 0:
            # 触控板 / 高精度滚轮: 已经是像素位移, 直接跟随
            distance = -pixelDelta
        else:
            now = self.clock.elapsed() if self.clock.isValid() else 0
            self.notchTimes = [t for t in self.notchTimes
                               if now - t < ACCELERATION_WINDOW_MS] + [now]
            boost = min(1 + (len(self.notchTimes) - 1) / 8, MAX_ACCELERATION)
            distance = -delta / 120 * PIXELS_PER_NOTCH * boost

        self.target = min(max(self.target + distance, bar.minimum()), bar.maximum())

        if not self.clock.isValid():
            self.clock.start()
        if not self.timer.isActive():
            self.clock.restart()
            self.timer.start(refreshInterval(self.area))

        e.accept()

    def _step(self):
        bar = self.bar()
        dt = max(self.clock.restart(), 1)

        # 指数逼近: 每帧走剩余距离的 1 - e^(-dt/τ), 与帧率无关
        remaining = self.target - self.current
        self.current += remaining * (1 - math.exp(-dt / TIME_CONSTANT_MS))

        if abs(self.target - self.current) < 0.5:
            self.current = self.target
            self.timer.stop()

        value = round(self.current)
        if value != bar.value():
            self.lastWritten = value
            bar.setValue(value)


def install():
    """
    让 qfluentwidgets 的所有平滑滚动改走 HighRefreshScroller. 需在创建任何滚动区之前调用.

    两条路径都要接管: SmoothScroll.wheelEvent (ScrollArea 等), 以及 SmoothScrollDelegate 的
    滚轮过滤 —— SmoothScrollArea 用 useAni=True 的委托, 滚轮原本交给滚动条的 QPropertyAnimation
    (约 60Hz), 根本不经过 SmoothScroll.
    """
    from qfluentwidgets.common.smooth_scroll import SmoothScroll, SmoothMode
    from qfluentwidgets.components.widgets.scroll_bar import SmoothScrollDelegate

    if getattr(SmoothScroll, '_seraphineHighRefresh', False):
        return

    def wheelEvent(self, e):
        if self.smoothMode == SmoothMode.NO_SMOOTH:
            QAbstractScrollArea.wheelEvent(self.widget, e)
            return

        scroller = getattr(self, '_highRefreshScroller', None)
        if scroller is None:
            scroller = HighRefreshScroller(self.widget, self.orient)
            self._highRefreshScroller = scroller

        scroller.wheel(e)

    originalFilter = SmoothScrollDelegate.eventFilter

    def eventFilter(self, obj, e):
        if e.type() != QEvent.Type.Wheel:
            return originalFilter(self, obj, e)

        angle = e.angleDelta()
        vertical = angle.y() != 0
        bar = self.vScrollBar if vertical else self.hScrollBar
        delta = angle.y() if vertical else angle.x()

        # 已经滚到头: 交还给外层 (与原实现一致, 让嵌套的滚动区能继续滚)
        if (delta < 0 and bar.value() == bar.maximum()) or \
                (delta > 0 and bar.value() == bar.minimum()):
            return False

        (self.verticalSmoothScroll if vertical else self.horizonSmoothScroll).wheelEvent(e)
        e.setAccepted(True)
        return True

    SmoothScroll.wheelEvent = wheelEvent
    SmoothScrollDelegate.eventFilter = eventFilter
    SmoothScroll._seraphineHighRefresh = True
