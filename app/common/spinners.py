"""
qfluentwidgets' indeterminate progress ring and bar, with their animation paused while the
widget is hidden. The originals start on construction and loop until stop(), visible or not:
every running one keeps Qt's shared animation timer ticking about 60 times a second and calls
two Python property setters per tick. Seraphine builds several (career, search, OP.GG waiting
pages) that sit hidden for hours, which cost 4 to 8 ms of main-thread CPU per second with the
window hidden (measured offscreen on macOS).

Here a running animation is paused on every hide event (the widget or a parent hidden, or the
window minimized), and one started or resumed while hidden or minimized is paused at once; the
next show event resumes it. What is on screen does not change, and neither does the public
state: start() / stop() and the bar's isStarted() / isPaused() report what the caller asked for,
whatever the visibility. app.common.qfluentwidgets exports these classes under the original
names, so every user of that module gets them.
"""
from PySide6.QtCore import QAbstractAnimation

from qfluentwidgets import IndeterminateProgressBar as _FluentBar
from qfluentwidgets import IndeterminateProgressRing as _FluentRing


class _PausedWhileHidden:
    """Mixin for a qfluentwidgets spinner with an `aniGroup` it starts and stops"""

    # True while the animation is paused only because the widget is hidden
    _hiddenPause = False

    def _pauseIfHidden(self):
        hidden = not self.isVisible() or self.window().isMinimized()
        if hidden and self.aniGroup.state() == QAbstractAnimation.Running:
            self.aniGroup.pause()
            self._hiddenPause = True

    def hideEvent(self, e):
        super().hideEvent(e)
        if self.aniGroup.state() == QAbstractAnimation.Running:
            self.aniGroup.pause()
            self._hiddenPause = True

    def showEvent(self, e):
        super().showEvent(e)
        if self._hiddenPause:
            self._hiddenPause = False
            self.aniGroup.resume()

    def start(self):
        self._hiddenPause = False
        super().start()
        self._pauseIfHidden()

    def stop(self):
        self._hiddenPause = False
        super().stop()


class IndeterminateProgressRing(_PausedWhileHidden, _FluentRing):
    pass


class IndeterminateProgressBar(_PausedWhileHidden, _FluentBar):

    def isStarted(self):
        return super().isStarted() or self._hiddenPause

    def isPaused(self):
        return super().isPaused() and not self._hiddenPause

    def pause(self):
        self._hiddenPause = False
        super().pause()

    def resume(self):
        self._hiddenPause = False
        super().resume()
        self._pauseIfHidden()

    def setPaused(self, isPaused: bool):
        if isPaused:
            self.pause()
        else:
            self.resume()

    def error(self):
        self._hiddenPause = False
        super().error()
