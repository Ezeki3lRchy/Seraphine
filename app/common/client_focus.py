"""Send the League client back to the background when it comes to the front for a
ready check ("Auto accept" card, Windows only, off by default).

The client's native process brings its window to the front when a match is found; an
outside program cannot stop that. What can be done is to minimize the window right
after, so Windows activates the window that was in front before. The user sees the
client for a moment.

Rules (``ReadyCheckFocus``, no Qt, no Windows call: everything is passed in):
- while in queue the foreground window is sampled, so it is known whether the user was
  looking at the client before the match was found;
- at ``ReadyCheck``: the client in front for more than ``RECENT`` seconds means the user
  was using it, and nothing happens. The client in front for less, or coming to the
  front within ``WINDOW`` seconds, is the client's own request: it is minimized once;
- at ``ChampSelect`` a client this module minimized is shown again, and also when the
  automatic accept failed (the user must be able to accept by hand);
- a client found in front by the very first sample is never minimized (it is not known
  for how long it has been there).
"""
import time

from PySide6.QtCore import QObject, QTimer

from app.common import platform
from app.common.config import cfg
from app.common.logger import logger

TAG = "ClientFocus"

QUEUE_PHASES = ('Matchmaking', 'ReadyCheck')


class ReadyCheckFocus:
    # the client came to the front at most this long before the ReadyCheck event
    RECENT = 1.0
    # ... or comes to the front at most this long after it
    WINDOW = 2.5

    def __init__(self, enabled, isClientForeground, minimize, restore,
                 now=time.monotonic):
        self._enabled = enabled
        self._isClientForeground = isClientForeground
        self._minimize = minimize
        self._restore = restore
        self._now = now

        self.phase = None
        self.minimized = False  # this module minimized the client and has not shown it
        self._foreground = None  # last sample: True / False, None = unknown
        self._foregroundSince = None
        self._armedAt = None  # ReadyCheck began with the client in the background
        self._handled = False  # this ready check already minimized the client

    @property
    def armed(self):
        return self._armedAt is not None

    def sample(self):
        """Read the foreground window once; minimizes the client if it just came to the
        front of an armed ready check."""
        t = self._now()
        if self._armedAt is not None and t - self._armedAt > self.WINDOW:
            self._armedAt = None

        foreground = self._isClientForeground()
        if foreground is None:
            self._foreground = None
            self._foregroundSince = None
            return

        if foreground and not self._foreground:
            # in front at the first sample: since an unknown time, so "long ago"
            self._foregroundSince = t if self._foreground is False else float('-inf')
        self._foreground = foreground

        if foreground and self._armedAt is not None:
            self._hide()

    def onPhase(self, phase):
        self.phase = phase

        if phase == 'ReadyCheck':
            self._armedAt = None
            if self._handled or not self._enabled():
                return

            self.sample()
            if self._foreground is None:
                return

            if not self._foreground:
                self._armedAt = self._now()
            elif self._foregroundSince >= self._now() - self.RECENT:
                self._hide()
            return

        self._armedAt = None
        self._handled = False

        if phase != 'Matchmaking':
            # sampling stops outside the queue: the last sample would be stale
            self._foreground = None
            self._foregroundSince = None

            if phase == 'ChampSelect':
                self.showAgain()
            else:
                # left the queue, or the game started: the user opens it when needed
                self.minimized = False

    def showAgain(self):
        """Show a client this module minimized (champ select began, or the automatic
        accept failed)."""
        if not self.minimized:
            return

        self.minimized = False
        self._restore()

    def _hide(self):
        self._armedAt = None
        if self._handled:
            return

        self._handled = True
        if self._minimize():
            self.minimized = True

    def interval(self):
        """Milliseconds between samples; 0 = no sampling."""
        if self.phase not in QUEUE_PHASES or not self._enabled():
            return 0

        return ClientFocusKeeper.FAST_MS if self.armed else ClientFocusKeeper.SLOW_MS


def isEnabled():
    return (platform.IS_WINDOWS
            and bool(cfg.get(cfg.enableAutoAcceptMatching))
            and bool(cfg.get(cfg.hideClientOnReadyCheck)))


class ClientFocusKeeper(QObject):
    """Feeds ``ReadyCheckFocus`` with gameflow phases and timed samples.

    ``restore`` shows the client again (MainWindow passes the connector call)."""

    SLOW_MS = 250  # in queue
    FAST_MS = 30  # waiting for the client to come to the front

    def __init__(self, restore, parent=None):
        super().__init__(parent)

        self.logic = ReadyCheckFocus(
            isEnabled, platform.lol_client_is_foreground, self.__minimize, restore)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.__onTimeout)

    def onPhase(self, phase):
        self.logic.onPhase(phase)
        self.__syncTimer()

    def acceptFailed(self):
        self.logic.showAgain()

    def stop(self):
        self.timer.stop()

    def __onTimeout(self):
        self.logic.sample()
        self.__syncTimer()

    def __syncTimer(self):
        interval = self.logic.interval()
        if interval == 0:
            self.timer.stop()
        elif not self.timer.isActive() or self.timer.interval() != interval:
            self.timer.start(interval)

    def __minimize(self):
        done = platform.minimize_lol_client()
        logger.info(f"client came to the front for a ready check, minimized: {done}", TAG)
        return done
