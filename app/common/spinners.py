"""
qfluentwidgets' indeterminate progress ring and bar, animated only while visible. The originals
start on construction and loop until stop(), visible or not: every running one keeps Qt's shared
animation timer ticking about 60 times a second and calls two Python property setters per tick.
Seraphine builds several (career, search, OP.GG waiting pages) that sit hidden for hours, which
cost 4 to 8 ms of main-thread CPU per second with the window hidden (measured offscreen on macOS).

The ring's running animation is paused on every hide event (the widget or a parent hidden, or
the window minimized), and one started while hidden or minimized is paused at once; the next show
event resumes it. What is on screen does not change.

The bar also animates at FRAME_RATE frames a second instead of Qt's 60 animation ticks. It is
translucent, so each of its frames repaints the window behind it (the window background and every
page above it), about 0.45 ms per frame: on the start page, which shows it until the client is
found, it was the largest CPU cost left. Its Qt animations are never run: the bar's own frame
timer, which runs only while the bar is started and shown, sets both bar positions to the values
those animations (durations, start and end values, easing, the long bar's pause) have at the
current animation time, so the bar's shape and timing are those of the original.

On Windows a coarse Qt timer of 33 ms fires on the 15.6 ms system tick, every 47 ms (about 21
frames a second, measured with the app idle), which is visibly uneven on a bar the user watches.
SmoothIndeterminateProgressBar is the same bar on a precise 60 frames a second timer, for a bar
that is shown for a short time only (the update card while verifying / unpacking); a precise
timer raises the system timer resolution while it runs, so the long-lived bars keep the coarse one.

Neither changes the public state: start() / stop() and the bar's isStarted() / isPaused() report
what the caller asked for, whatever the visibility. app.common.qfluentwidgets exports these
classes under the original names, so every user of that module gets them.
"""
import time

from PySide6.QtCore import QAbstractAnimation, Qt, QTimer

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


def _valueAt(animation, t):
    """A QPropertyAnimation's value t ms after it starts (its end value once it has ended)"""
    progress = min(max(t / animation.duration(), 0.0), 1.0)
    start, end = animation.startValue(), animation.endValue()
    return start + (end - start) * animation.easingCurve().valueForProgress(progress)


class IndeterminateProgressBar(_FluentBar):
    """The bar's state is _state ('stopped', 'running' or 'paused', as asked by the caller).
    While it runs and is shown, _onFrame advances the animation time _time (ms) and sets the
    positions it has then (barPositions), FRAME_RATE times a second"""

    FRAME_RATE = 30
    PRECISE_FRAMES = False  # True: a Qt.PreciseTimer (frames really FRAME_RATE apart)

    _state = 'stopped'
    _time = 0
    # time.perf_counter() of the last frame, while the frame timer runs (not time.monotonic():
    # on Windows before Python 3.13 it advances in 15.6 ms steps, uneven at 60 frames a second)
    _lastFrame = None
    _carry = 0.0        # the part of a millisecond _time has not counted yet
    _frames = None      # the frame timer, made on first use (the base __init__ calls start())

    def _frameTimer(self):
        if self._frames is None:
            self._frames = QTimer(self)
            if self.PRECISE_FRAMES:
                self._frames.setTimerType(Qt.PreciseTimer)
            self._frames.setInterval(round(1000 / self.FRAME_RATE))
            self._frames.timeout.connect(self._onFrame)
        return self._frames

    def _syncFrames(self):
        """frame timer on exactly while running and shown (not hidden, not minimized)"""
        timer = self._frameTimer()
        wanted = (self._state == 'running' and self.isVisible()
                  and not self.window().isMinimized())
        if wanted and not timer.isActive():
            self._lastFrame = time.perf_counter()
            timer.start()
        elif not wanted and timer.isActive():
            timer.stop()

    def _onFrame(self):
        now = time.perf_counter()
        elapsed = round((now - self._lastFrame) * 1000 + self._carry, 6)
        self._lastFrame = now
        self._time += int(elapsed)
        self._carry = elapsed - int(elapsed)
        self.shortPos, self.longPos = self.barPositions(self._time)

    def barPositions(self, t):
        """(shortPos, longPos) of the original's looping animation group at t ms after start():
        the short bar's animation from the start of every loop, the long bar's after its pause
        (during the pause it stays where the last loop left it, 0 in the first)"""
        loop, at = divmod(t, self.aniGroup.duration())
        pause = self.longBarAniGroup.duration() - self.longBarAni.duration()
        short = _valueAt(self.shortBarAni, at)
        if at >= pause:
            long = _valueAt(self.longBarAni, at - pause)
        else:
            long = self.longBarAni.endValue() if loop else self.longBarAni.startValue()
        return short, long

    def hideEvent(self, e):
        super().hideEvent(e)
        self._syncFrames()

    def showEvent(self, e):
        super().showEvent(e)
        self._syncFrames()

    def start(self):
        self.shortPos = 0
        self.longPos = 0
        self._time = 0
        self._carry = 0.0
        self._state = 'running'
        self._syncFrames()
        self.update()

    def stop(self):
        self._state = 'stopped'
        self._syncFrames()
        super().stop()

    def isStarted(self):
        return self._state == 'running'

    def isPaused(self):
        return self._state == 'paused'

    def pause(self):
        if self._state == 'running':
            self._state = 'paused'
            self._syncFrames()
            self.update()

    def resume(self):
        if self._state == 'paused':
            self._state = 'running'
            self._syncFrames()
            self.update()

    def setPaused(self, isPaused: bool):
        if isPaused:
            self.pause()
        else:
            self.resume()

    def error(self):
        self._state = 'stopped'
        self._syncFrames()
        super().error()


class SmoothIndeterminateProgressBar(IndeterminateProgressBar):
    """The bar at the original's 60 frames a second, on a precise timer: for a bar the user
    watches for a short time (see the module docstring). Same shape and timing"""

    FRAME_RATE = 60
    PRECISE_FRAMES = True
