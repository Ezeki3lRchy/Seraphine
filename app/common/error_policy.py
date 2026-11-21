"""What to do with an exception nothing else caught.

Uncaught exceptions arrive through sys.excepthook (Qt slots, and @asyncSlot handlers via
qasync) and through the asyncio loop's exception handler (fire-and-forget tasks, and the
app's own BaseException subclasses raised from an @asyncSlot). ErrorPolicy decides what
happens; it builds no widget, so it can be tested without a MainWindow:

- KeyboardInterrupt / SystemExit end the process (EXIT)
- before the main window has finished starting, a failure is fatal (FATAL: dialog + exit)
- afterwards a failure never ends the session: the first one shows a non-blocking notice
  (NOTIFY); expected failures (the client went away, the app's own LCU errors), failures
  the connector already showed as "LCU request error" and failures while that notice is
  open are only logged (LOG). A failure first logged while another notice was open gets
  its own notice once that one closed; only a failure that was shown (or logged quietly)
  is reduced to one line when it repeats within a short window (BRIEF), and those lines
  are rate-limited per traceback (SILENT in between, the count goes into the next line),
  so a failing timer or event storm cannot flood the log
"""
import asyncio
import collections
import sys
import threading
import time
import traceback

from aiohttp.client_exceptions import ClientConnectorError

from app.lol.exceptions import (SummonerNotFound, SummonerGamesNotFound,
                                SummonerRankInfoNotFound, SummonerNotInGame,
                                RetryMaximumAttempts)

TAG = "ErrorPolicy"

EXIT = 'exit'
FATAL = 'fatal'
NOTIFY = 'notify'
LOG = 'log'
BRIEF = 'brief'
SILENT = 'silent'  # counted only, written with the next one-line entry

# Not bugs: the client closed / is not reachable, or the app's own LCU errors, which the
# connector's @retry already reported (lcuApiExceptionRaised) or deliberately keeps silent
EXPECTED = (ConnectionRefusedError, ClientConnectorError,
            SummonerNotFound, SummonerGamesNotFound, SummonerRankInfoNotFound,
            SummonerNotInGame, RetryMaximumAttempts)

Decision = collections.namedtuple('Decision', 'action reason text')


def formatException(ty, value, tb):
    return "".join(traceback.format_exception(ty, value, tb))


def briefLine(ty, tb):
    """Type and innermost frame only: never the exception message, which for aiohttp
    errors can carry request details"""
    frames = traceback.extract_tb(tb) if tb is not None else []
    where = f" at {frames[-1].filename}:{frames[-1].lineno} in {frames[-1].name}" if frames else ""
    return f"{ty.__name__}{where}"


def signature(ty, tb):
    """Same type raised from the same code path; the message is ignored (a KeyError for
    another key at the same line is the same bug)"""
    frames = traceback.extract_tb(tb) if tb is not None else []
    return (ty.__module__, ty.__qualname__,
            tuple((f.filename, f.lineno, f.name) for f in frames))


class ErrorPolicy:
    # the same traceback within this many seconds is only logged as one line
    REPEAT_WINDOW = 60.0
    # a notice is treated as closed after this long even if its close was never reported
    NOTICE_TIMEOUT = 60.0

    def __init__(self, alreadyReported=None, clock=time.monotonic):
        """
        @param alreadyReported: (exception) -> bool, True when the user was already told
            about this very exception (e.g. the connector's "LCU request error" bar)
        """
        self.started = False
        self._alreadyReported = alreadyReported
        self._clock = clock
        self._lock = threading.Lock()  # sys.excepthook may run on a worker thread
        # signature -> when it last got a notice: only these are reduced to one line
        self._shown = {}
        # signature -> when its full traceback was last logged without a notice
        self._logged = {}
        # signature -> [when its last one-line entry was written, how many since]
        self._brief = {}
        self._noticeSince = None

    def finishStartup(self):
        self.started = True

    def noticeClosed(self):
        with self._lock:
            self._noticeSince = None

    def decide(self, ty, value, tb) -> Decision:
        if issubclass(ty, (KeyboardInterrupt, SystemExit)):
            return Decision(EXIT, 'interrupt', formatException(ty, value, tb))

        expected = issubclass(ty, EXPECTED)
        cancelled = issubclass(ty, asyncio.CancelledError)

        if not self.started and not expected and not cancelled:
            return Decision(FATAL, 'startup', formatException(ty, value, tb))

        now = self._clock()
        key = signature(ty, tb)
        with self._lock:
            self.__prune(now)

            if cancelled:
                # a cancelled @asyncSlot task (qasync re-raises it in a done callback)
                return self.__brief(key, now, ty, tb, 'cancelled')

            if self.__recent(self._shown, key, now):
                self._shown[key] = now  # sliding: a steady storm stays quiet
                return self.__brief(key, now, ty, tb, 'repeated')

            quiet = None
            if expected:
                quiet = 'expected'
            elif self.__reported(value):
                quiet = 'reported'
            elif (self._noticeSince is not None
                    and now - self._noticeSince < self.NOTICE_TIMEOUT):
                # not marked as shown: once that notice closed, this one gets its own
                quiet = 'notice open'

            if quiet is not None:
                if self.__recent(self._logged, key, now):
                    self._logged[key] = now
                    return self.__brief(key, now, ty, tb, quiet)
                self._logged[key] = now
                return Decision(LOG, quiet, formatException(ty, value, tb))

            self._shown[key] = now
            self._noticeSince = now
            return Decision(NOTIFY, 'first', formatException(ty, value, tb))

    def __recent(self, table, key, now):
        last = table.get(key)
        return last is not None and now - last < self.REPEAT_WINDOW

    def __brief(self, key, now, ty, tb, reason):
        """One line for the first repeat, then at most one per window carrying the count
        (a broken timer can raise thousands of times a second); SILENT in between"""
        entry = self._brief.get(key)
        if entry is not None and now - entry[0] < self.REPEAT_WINDOW:
            entry[1] += 1
            return Decision(SILENT, reason, '')

        text = briefLine(ty, tb)
        if entry is not None and entry[1]:
            text += f" (+{entry[1]} more not logged)"
        self._brief[key] = [now, 0]
        return Decision(BRIEF, reason, text)

    def __prune(self, now):
        for table in (self._shown, self._logged, self._brief):
            if len(table) > 200:
                for k in [k for k, v in table.items()
                          if now - (v[0] if isinstance(v, list) else v) >= self.REPEAT_WINDOW]:
                    del table[k]

    def __reported(self, value):
        if self._alreadyReported is None:
            return False
        try:
            return bool(self._alreadyReported(value))
        except Exception:
            return False


def handleLoopException(loop, context):
    """asyncio loop exception handler: an exception goes through sys.excepthook (the same
    policy as every other uncaught exception); a context without one is only logged"""
    exc = context.get('exception')
    if exc is None:
        from app.common.logger import logger

        # only the message: the other context values (handle / transport reprs) can
        # carry request data
        logger.warning(f"asyncio: {context.get('message')}", TAG)
        return

    sys.excepthook(type(exc), exc, exc.__traceback__)
