"""Exceptions never reach a log line or message through their repr (an aiohttp error's
repr carries the request headers, i.e. the LCU login token), and connector's @retry marks
the exception it already showed as "LCU request error" so the error policy does not show
it again, however many calls ran since.

No request is made: the failing calls raise fake exceptions.
"""
import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.common import error_policy as ep
from app.lol import connector as connector_module
from app.lol.connector import connector, retry, PastRequest, errorSummary, isReported
from tests import LOG_RECORDS

MARKER = "Authorization-MARKER-should-never-be-logged"


class LeakyError(Exception):
    """Like aiohttp's ContentTypeError: the repr carries the request (headers)"""
    status = 200

    def __repr__(self):
        return f"LeakyError(request_info=RequestInfo(headers={{'Authorization': '{MARKER}'}}))"

    def __str__(self):
        return "200, message='Attempt to decode JSON with unexpected mimetype'"


class SlotsError(Exception):
    """An exception type that refuses new attributes"""
    __slots__ = ()

    def __setattr__(self, name, value):
        raise AttributeError(name)


def leaked():
    return [r for r in LOG_RECORDS if MARKER in r]


class RedactionTest(unittest.TestCase):

    def setUp(self):
        del LOG_RECORDS[:]

    def test_error_summary_is_type_and_status_only(self):
        self.assertEqual(errorSummary(LeakyError()), "LeakyError(status=200)")
        self.assertEqual(errorSummary(KeyError("x")), "KeyError")

    def test_past_request_never_renders_the_exception_repr(self):
        call = PastRequest("getSomething", {"a": 1}, {})
        call.response = LeakyError()
        text = str(call)
        self.assertNotIn(MARKER, text)
        self.assertIn("response=LeakyError(status=200)", text)
        self.assertIn("func='getSomething'", text)  # other fields unchanged

    def test_crash_diagnostics_do_not_leak_a_failed_response(self):
        from app.view import main_window as mw
        call = PastRequest("getSomething", {}, {})
        call.response = LeakyError()
        fake = SimpleNamespace(searchInterface='s', gameInfoInterface='g', careerInterface='c',
                               auxiliaryFuncInterface='a', settingInterface='t')
        with mock.patch.object(connector, 'callStack', [call]):
            mw.MainWindow._MainWindow__logExceptionDiagnostics(fake, "Traceback ...")
        self.assertTrue(any('getSomething' in r for r in LOG_RECORDS))
        self.assertEqual(leaked(), [])

    def test_delete_rune_page_failure_is_logged_without_the_repr(self):
        async def go():
            connector.semaphore = asyncio.Semaphore(4)
            try:
                with mock.patch.object(connector, 'getCurrentRunePage',
                                       mock.AsyncMock(side_effect=LeakyError())):
                    await connector.deleteCurrentRunePage()
            finally:
                connector.semaphore = None

        asyncio.run(go())
        self.assertTrue(any('deleteCurrentRunePage error LeakyError' in r for r in LOG_RECORDS))
        self.assertEqual(leaked(), [])

    def test_quiet_lookups_do_not_log_the_puuid(self):
        import logging
        seraphine = logging.getLogger("Seraphine")
        level = seraphine.level
        seraphine.setLevel(logging.WARNING)
        self.addCleanup(seraphine.setLevel, level)

        async def go():
            # no session: the request fails inside the try and is only logged
            with mock.patch.object(connector, 'semaphore', None):
                return (await connector.getChampionMasteryQuietly("puuid-MARKER"),
                        await connector.getRankedStatsByPuuidQuietly("puuid-MARKER"))

        self.assertEqual(asyncio.run(go()), ({}, None))
        self.assertFalse(any('puuid-MARKER' in r for r in LOG_RECORDS))
        lines = [r for r in LOG_RECORDS if 'Quietly' in r and 'failed' in r]
        self.assertEqual(len(lines), 2)  # still logged, by method name

    def test_lcu_error_bar_shows_no_repr(self):
        from app.view import main_window as mw
        fake = SimpleNamespace(tr=lambda t: t, lastTipsTime=0, lastTipsType=None)

        async def go():
            with mock.patch.object(mw, 'InfoBar') as infoBar:
                await mw.MainWindow._MainWindow__onShowLcuConnectError(fake, "getX", LeakyError())
            return infoBar

        infoBar = asyncio.run(go())
        content = infoBar.error.call_args.args[1]
        self.assertNotIn(MARKER, content)
        self.assertIn("LeakyError(status=200)", content)


class ReportedMarkTest(unittest.TestCase):

    def run12(self, makeExc):
        """twelve failing @retry calls interleaved (more than callStack keeps)"""
        @retry(count=2)
        async def failing(i):
            await asyncio.sleep(0)
            raise makeExc(i)

        async def one(i):
            try:
                await failing(i)
            except BaseException as e:  # incl. the app's own BaseException subclasses
                return e

        async def go():
            connector.semaphore = asyncio.Semaphore(4)
            try:
                return await asyncio.gather(*(one(i) for i in range(12)))
            finally:
                connector.semaphore = None

        with mock.patch.object(connector_module.signalBus, 'lcuApiExceptionRaised') as signal:
            errors = asyncio.run(go())
        return errors, signal

    def test_every_reported_exception_is_marked_even_after_eviction(self):
        errors, signal = self.run12(lambda i: LeakyError(i))
        self.assertEqual(signal.emit.call_count, 12)
        # the first failed call is no longer in callStack: scanning it would miss it
        with connector.dqLock:
            self.assertFalse(any(c.response is errors[0] for c in connector.callStack))
        self.assertTrue(all(isReported(e) for e in errors))

        policy = ep.ErrorPolicy(alreadyReported=isReported)
        policy.finishStartup()
        e = errors[0]
        self.assertEqual(policy.decide(type(e), e, e.__traceback__).reason, 'reported')

    def test_unmarkable_exception_is_still_raised(self):
        errors, signal = self.run12(lambda i: SlotsError())
        self.assertTrue(all(isinstance(e, SlotsError) for e in errors))
        self.assertFalse(any(isReported(e) for e in errors))

    def test_unrelated_exception_is_not_marked(self):
        self.assertFalse(isReported(KeyError("x")))


if __name__ == '__main__':
    unittest.main()
