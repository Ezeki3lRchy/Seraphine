"""connector: sessions, the retry decorator, error mapping, the Quietly variants and the
request log, driven against a fake game client (stdlib http.server on 127.0.0.1, ephemeral
port, one thread). Nothing under test is mocked except what cannot be reached on a test
machine: the https scheme of the local sessions (rewritten to plain http), the discovery of
the client's port/token, and asyncio.sleep (the wait between retries is recorded, not slept).

Determinism: no test waits on the wall clock. A request the server must hold is held on a
threading.Event (released in cleanup); a test that has to wait for the server to have seen
a request polls a predicate under a watchdog that fails the test with a clear message.
The only real durations are the aiohttp / requests timeouts the tests configure on purpose
(0.1 - 0.2 s) to prove that a slow answer ends in the fallback.

Behaviour first documented here as suspicious (closed connector, the wait between retries,
the session timeout, error answers of the summoner / game detail / gameflow lookups, the
request log on SummonerNotFound) has been fixed; those tests now pin the fixed behaviour.
"""
import asyncio
import base64
import json
import logging
import os
import threading
import unittest
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import aiohttp
import requests

from app.common.logger import logger
from app.common.signals import signalBus
from app.lol import connector as connector_module
from app.lol.connector import connector, isReported, REDACTED
from app.lol.exceptions import (SummonerGamesNotFound,
                                SummonerNotFound, SummonerRankInfoNotFound)
from tests import LOG_RECORDS

_real_sleep = asyncio.sleep  # the polling helper below must not use the patched sleep

LCU_TOKEN = "test-lcu-token-0001"
SGP_TOKEN = "test-sgp-token-0002"
GATE_LIMIT = 20  # seconds a held request waits for its gate before giving up (cleanup opens it)

NOT_FOUND = ("json", 404, {"errorCode": "RPC_ERROR", "httpStatus": 404})

# aiohttp sends an idempotent GET a second time when the server drops the connection
# without answering, so every attempt of a GET against a "drop" costs two requests
GET_RESEND = 2


class FakeClient:
    """The fake game client. plan: path -> list of actions, consumed one per request, the
    last one repeats. Actions: ("json", status, obj), ("raw", status, bytes, content_type),
    "drop" (read the request, close without answering), ("hold", status, obj) (answer only
    once `gate` is set)."""

    def __init__(self):
        self.plan = {}
        self.seen = []  # (method, path, query, headers, body)
        self.gate = threading.Event()
        self._lock = threading.Lock()
        self.in_flight = 0
        self.max_in_flight = 0

        handler = type("Handler", (_Handler,), {"fake": self})
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        # a short poll interval: shutdown() waits for one poll, the default is 0.5 s per test
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()

    def stop(self):
        self.gate.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)

    def paths(self):
        return [s[1] for s in self.seen]

    def next_action(self, path):
        with self._lock:
            actions = self.plan.get(path) or [NOT_FOUND]
            return actions.pop(0) if len(actions) > 1 else actions[0]


class _Handler(BaseHTTPRequestHandler):
    fake = None

    def log_message(self, *args):
        pass

    def _reply(self, status, raw, ctype):
        try:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        except OSError:  # the client gave up (timeout / cancel) while the request was held
            pass

    def _handle(self):
        fake = self.fake
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        path, _, query = self.path.partition("?")
        with fake._lock:
            fake.seen.append((self.command, path, query,
                              {k.lower(): v for k, v in self.headers.items()},
                              json.loads(raw) if raw else None))

        action = fake.next_action(path)
        if action == "drop":
            self.close_connection = True
            return
        if action[0] == "hold":
            with fake._lock:
                fake.in_flight += 1
                fake.max_in_flight = max(fake.max_in_flight, fake.in_flight)
            try:
                fake.gate.wait(GATE_LIMIT)
            finally:
                with fake._lock:
                    fake.in_flight -= 1
            return self._reply(action[1], json.dumps(action[2]).encode(), "application/json")
        if action[0] == "raw":
            return self._reply(action[1], action[2], action[3])
        return self._reply(action[1], json.dumps(action[2]).encode(), "application/json")

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _handle


def ok(obj):
    return ("json", 200, obj)


HTML = ("raw", 200, b"<html>not json</html>", "text/html")
SUMMONER = {"summonerId": 7, "puuid": "p1"}


class _ConnectorCase(unittest.TestCase):

    def setUp(self):
        self.fake = FakeClient()
        self.addCleanup(self.fake.stop)

        # the connector is a process-wide singleton: put back whatever the test changed
        saved = dict(connector.__dict__)
        self.addCleanup(self._restore, saved)
        connector.callStack = deque(maxlen=10)

        self.errors = []
        slot = lambda name, exc: self.errors.append((name, exc))  # noqa: E731
        signalBus.lcuApiExceptionRaised.connect(slot)
        self.addCleanup(signalBus.lcuApiExceptionRaised.disconnect, slot)

        # the wait between retries is recorded, not slept
        self.delays = []

        async def fake_sleep(delay, *a, **kw):
            self.delays.append(delay)
            await _real_sleep(0)

        patcher = mock.patch.object(asyncio, "sleep", fake_sleep)
        patcher.start()
        self.addCleanup(patcher.stop)

        del LOG_RECORDS[:]

    @staticmethod
    def _restore(saved):
        connector.__dict__.clear()
        connector.__dict__.update(saved)

    async def until(self, predicate, what, timeout=10):
        """Waits for predicate() under a watchdog; fails the test with `what` on expiry"""
        async def poll():
            while not predicate():
                await _real_sleep(0.005)

        try:
            await asyncio.wait_for(poll(), timeout)
        except asyncio.TimeoutError:
            self.fail(f"timed out after {timeout}s waiting for: {what}")

    def run_async(self, make_coro, *, session=True, concurrency=4, lcu_timeout=None,
                  watchdog=30):
        """Runs make_coro() with an LCU session pointed at the fake client. Returns
        (result, exception): the exception never leaves the loop (the app's own exceptions
        are BaseException subclasses). A call that hangs fails the test after `watchdog`s."""
        async def go():
            if session:
                connector.semaphore = asyncio.Semaphore(concurrency)
                kw = {}
                if lcu_timeout:
                    kw["timeout"] = aiohttp.ClientTimeout(total=lcu_timeout)
                connector.lcuSess = aiohttp.ClientSession(
                    base_url=self.fake.url, auth=aiohttp.BasicAuth("riot", LCU_TOKEN), **kw)
            else:
                connector.semaphore = None
                connector.lcuSess = None
            task = asyncio.ensure_future(make_coro())
            try:
                done, _ = await asyncio.wait({task}, timeout=watchdog)
                if not done:
                    self.fail(f"the call did not finish within {watchdog}s")
                try:
                    return task.result(), None
                except BaseException as e:
                    return None, e
            finally:
                if not task.done():
                    task.cancel()
                if connector.lcuSess is not None:
                    await connector.lcuSess.close()
                connector.lcuSess = None
                connector.semaphore = None

        return asyncio.run(go())

    def entries(self, func=None):
        return [e for e in connector.callStack if func is None or e.func == func]


class SessionTest(_ConnectorCase):
    """__initSessions / close(): how the sessions are built, reused and torn down.
    The https base URLs are recorded and then pointed at the fake client over http."""

    def _init(self, server="hn1", rc=(None, None), token_body=None):
        self.fake.plan["/entitlements/v1/token"] = [ok(token_body or {"accessToken": SGP_TOKEN})]
        real = aiohttp.ClientSession
        self.created = []
        self.request_kwargs = []
        orig_request = real._request

        def factory(*args, base_url=None, **kw):
            self.created.append(str(base_url))
            return real(*args, base_url=self.fake.url, **kw)

        async def spy(session, method, url, **kw):
            self.request_kwargs.append((method, kw.get("ssl")))
            return await orig_request(session, method, url, **kw)

        connector.port, connector.token, connector.server = 12345, LCU_TOKEN, server
        connector.inTencent = bool(server) and server.lower() in (
            "hn1", "hn10", "bgp2", "tj100")
        connector.semaphore = asyncio.Semaphore(4)

        patches = [mock.patch.object(aiohttp, "ClientSession", factory),
                   mock.patch.object(real, "_request", spy),
                   mock.patch.object(connector_module, "getRiotClientPortToken",
                                     return_value=rc)]
        return patches

    def _with_init(self, body, **kw):
        patches = self._init(**kw)

        async def go():
            for p in patches:
                p.start()
            try:
                await connector._LolClientConnector__initSessions()
                return await body()
            finally:
                for p in reversed(patches):
                    p.stop()
                await connector.close()

        return asyncio.run(go())

    def test_lcu_session_targets_the_local_https_port_with_a_capped_pool(self):
        async def body():
            return connector.lcuSess.connector.limit

        limit = self._with_init(body)
        self.assertEqual(self.created[0], "https://127.0.0.1:12345")
        self.assertEqual(limit, 8)

    def test_every_lcu_request_carries_basic_auth_for_the_login_token(self):
        self.fake.plan["/lol-summoner/v1/current-summoner"] = [ok(SUMMONER)]
        self.fake.plan["/lol-champ-select/v1/session/my-selection/reroll"] = [ok({})]

        async def body():
            await connector.getCurrentSummoner()
            await connector.getCurrentSummoner()
            await connector.reroll()

        self._with_init(body)
        wanted = "Basic " + base64.b64encode(f"riot:{LCU_TOKEN}".encode()).decode()
        lcu = [s for s in self.fake.seen if s[1] != "/entitlements/v1/token"]
        self.assertEqual(len(lcu), 3)
        self.assertEqual([s[3].get("authorization") for s in lcu], [wanted] * 3)

    def test_lcu_requests_skip_certificate_verification_for_every_verb(self):
        self.fake.plan["/lol-summoner/v1/current-summoner"] = [ok(SUMMONER)]
        self.fake.plan["/lol-champ-select/v1/session/my-selection/reroll"] = [ok({})]
        self.fake.plan["/lol-champ-select/v1/session/actions/3"] = [ok({})]

        async def body():
            await connector.getCurrentSummoner()  # GET
            await connector.reroll()  # POST
            await connector.selectChampion(3, 10, completed=False)  # PATCH

        self._with_init(body)
        verbs = {m: ssl for m, ssl in self.request_kwargs}
        self.assertEqual(verbs.get("GET"), False)
        self.assertEqual(verbs.get("POST"), False)
        self.assertEqual(verbs.get("PATCH"), False)

    def test_sessions_have_an_explicit_generous_timeout(self):
        # not aiohttp's 300 s default: a hung client ends after 60 s of silence, and the
        # wait for a pooled connection is not counted (no total)
        async def body():
            return [s.timeout for s in (connector.lcuSess, connector.rcSess, connector.sgpSess)]

        timeouts = self._with_init(body, rc=(4321, "test-rc-token"))
        for t in timeouts:
            self.assertIs(t, connector_module.CLIENT_REQUEST_TIMEOUT)
        self.assertIsNone(timeouts[0].total)
        self.assertEqual(timeouts[0].sock_read, 60)
        self.assertNotEqual(timeouts[0], aiohttp.client.DEFAULT_TIMEOUT)

    def test_sgp_session_url_per_region(self):
        cases = {"hn1": "https://hn1-k8s-sgp.lol.qq.com:21019",
                 "hn10": "https://hn10-k8s-sgp.lol.qq.com:21019",
                 "bgp2": "https://bgp2-k8s-sgp.lol.qq.com:21019",
                 "TJ100": "https://tj100-sgp.lol.qq.com:21019"}
        for server, url in cases.items():
            with self.subTest(server=server):
                self.created = []

                async def body():
                    return connector.sgpSess is not None

                self.assertTrue(self._with_init(body, server=server))
                self.assertEqual(self.created[1], url)

    def test_sgp_token_comes_through_the_lcu_and_is_redacted_in_the_log(self):
        async def body():
            # the entry for the call that fetched it never holds the token
            return connector.sgpToken, [e.response for e in self.entries("getSGPtoken")]

        self.assertEqual(self._with_init(body), (SGP_TOKEN, [REDACTED]))

    def test_sgp_request_sends_the_bearer_token_and_never_the_lcu_login(self):
        self.fake.plan["/leagues-ledge/v2/rankedStats/puuid/p1"] = [ok({"queues": []})]

        async def body():
            return await connector.getRankedStatsByPuuidViaSGP("p1")

        self.assertEqual(self._with_init(body), {"queues": []})
        sgp = [s for s in self.fake.seen if s[1].startswith("/leagues-ledge")]
        self.assertEqual(sgp[0][3].get("authorization"), f"Bearer {SGP_TOKEN}")
        self.assertNotIn(LCU_TOKEN, json.dumps(sgp[0][3]))

    def test_sgp_ranked_error_body_raises_rank_not_found(self):
        self.fake.plan["/leagues-ledge/v2/rankedStats/puuid/p1"] = [
            ("json", 404, {"errorCode": "NOT_FOUND"})]

        async def body():
            try:
                return await connector.getRankedStatsByPuuidViaSGP("p1")
            except SummonerRankInfoNotFound as e:
                return e

        self.assertIsInstance(self._with_init(body), SummonerRankInfoNotFound)

    def test_sgp_history_query_carries_start_index_and_count(self):
        path = "/match-history-query/v1/products/lol/player/p1/SUMMARY"
        self.fake.plan[path] = [ok({"games": []})]

        async def body():
            return await connector.getSummonerGamesByPuuidViaSGP("p1", 5, 9)

        self._with_init(body)
        query = [s[2] for s in self.fake.seen if s[1] == path]
        self.assertEqual(query, ["startIndex=5&count=5"])

    def test_sgp_request_outside_tencent_is_refused_before_any_request(self):
        async def body():
            connector.inTencent = False
            try:
                return await connector.getRankedStatsByPuuidViaSGP("p1")
            except AssertionError as e:
                return e

        self.assertIsInstance(self._with_init(body), AssertionError)
        self.assertEqual([p for p in self.fake.paths() if p.startswith("/leagues")], [])

    def test_no_server_means_no_sgp_session(self):
        async def body():
            return connector.sgpSess, connector.sgpToken

        self.assertEqual(self._with_init(body, server=None), (None, None))
        self.assertEqual(len(self.created), 1)  # the LCU session only

    def test_riot_client_session_only_when_discovered(self):
        async def body():
            s = connector.rcSess
            return s is not None and s.connector.limit

        self.assertEqual(self._with_init(body, rc=(4321, "rc-token")), 2)
        self.assertIn("https://127.0.0.1:4321", self.created)
        self.assertFalse(self._with_init(body, rc=(None, None)))

    def test_close_closes_every_session_and_resets_the_state(self):
        sessions = {}

        async def body():
            connector.matchSgpSess = aiohttp.ClientSession(base_url=self.fake.url)
            connector.sgpToken = SGP_TOKEN
            for name in ("lcuSess", "sgpSess", "rcSess", "matchSgpSess"):
                sessions[name] = getattr(connector, name)
            await connector.close()
            return {n: s.closed for n, s in sessions.items()}

        closed = self._with_init(body, rc=(4321, "rc-token"))
        self.assertEqual(closed, dict.fromkeys(("lcuSess", "sgpSess", "rcSess", "matchSgpSess"),
                                               True))
        for name in ("lcuSess", "sgpSess", "rcSess", "matchSgpSess", "semaphore",
                     "port", "token", "sgpToken", "server"):
            self.assertIsNone(getattr(connector, name), name)
        self.assertFalse(connector.inTencent)
        self.assertEqual(len(connector.callStack), 0)

    def test_close_resets_even_when_a_session_refuses_to_close(self):
        class Broken:
            async def close(self):
                raise RuntimeError("already half dead")

        async def body():
            good, real_lcu = connector.sgpSess, connector.lcuSess
            connector.lcuSess = Broken()
            await connector.close()
            await real_lcu.close()  # close() never saw it
            return good.closed

        self.assertTrue(self._with_init(body))
        self.assertIsNone(connector.lcuSess)
        self.assertIsNone(connector.semaphore)

    def test_a_second_start_after_close_works(self):
        self.fake.plan["/lol-summoner/v1/current-summoner"] = [ok(SUMMONER)]

        async def body():
            first = await connector.getCurrentSummoner()
            await connector.close()
            self.assertIsNone(connector.lcuSess)
            # the same steps start() takes after discovery
            connector.port, connector.token, connector.server = 12345, LCU_TOKEN, "hn1"
            connector.semaphore = asyncio.Semaphore(4)
            await connector._LolClientConnector__initSessions()
            return first, await connector.getCurrentSummoner(), connector.sgpToken

        first, second, token = self._with_init(body)
        self.assertEqual((first, second, token), (SUMMONER, SUMMONER, SGP_TOKEN))


class RetryEndToEndTest(_ConnectorCase):

    def test_failed_answers_are_retried_until_the_call_succeeds(self):
        path = "/lol-match-history/v1/products/lol/p1/matches"
        self.fake.plan[path] = [NOT_FOUND, NOT_FOUND, ok({"games": {"games": []}})]
        res, exc = self.run_async(lambda: connector.getSummonerGamesByPuuid("p1"))

        self.assertIsNone(exc)
        self.assertEqual(res, {"games": []})
        self.assertEqual(len(self.fake.seen), 3)  # two failures, then the success
        self.assertEqual(self.errors, [])
        self.assertEqual(self.entries("getSummonerGamesByPuuid")[0].response, res)

    def test_transient_drops_are_retried_until_the_call_succeeds(self):
        # 4 dropped requests = 2 failed attempts (see GET_RESEND), the third attempt answers
        self.fake.plan["/lol-summoner/v1/current-summoner"] = ["drop"] * 4 + [ok(SUMMONER)]
        res, exc = self.run_async(connector.getCurrentSummoner)

        self.assertIsNone(exc)
        self.assertEqual(res, SUMMONER)
        self.assertEqual(len(self.fake.seen), 5)
        self.assertEqual(self.errors, [])

    def test_gives_up_after_five_attempts_and_reports_once(self):
        self.fake.plan["/lol-summoner/v1/current-summoner"] = ["drop"]
        _, exc = self.run_async(connector.getCurrentSummoner)

        self.assertIsInstance(exc, aiohttp.ClientError)
        self.assertEqual(len(self.fake.seen), 5 * GET_RESEND)
        self.assertEqual(len(self.errors), 1)
        name, emitted = self.errors[0]
        self.assertEqual(name, "getCurrentSummoner")
        self.assertIs(emitted, exc)
        self.assertTrue(isReported(exc))
        self.assertIs(self.entries("getCurrentSummoner")[0].response, exc)

    def test_error_body_without_summoner_id_raises_reference_error_without_a_popup(self):
        _, exc = self.run_async(connector.getCurrentSummoner)  # 404 error body

        self.assertIsInstance(exc, ReferenceError)
        self.assertEqual(len(self.fake.seen), 5)
        self.assertEqual(self.errors, [])  # ReferenceError is kept silent on purpose
        self.assertTrue(isReported(exc))

    def test_non_json_body_raises_content_type_error_and_reports(self):
        self.fake.plan["/lol-summoner/v1/current-summoner"] = [HTML]
        _, exc = self.run_async(connector.getCurrentSummoner)

        self.assertIsInstance(exc, aiohttp.ContentTypeError)
        self.assertEqual([n for n, _ in self.errors], ["getCurrentSummoner"])

    def test_missing_lcu_session_raises_reference_error_without_a_request_or_popup(self):
        async def scenario():
            session = connector.lcuSess
            connector.lcuSess = None  # semaphore still set: the client is not ready yet
            try:
                return await connector.getCurrentSummoner()
            finally:
                connector.lcuSess = session

        _, exc = self.run_async(scenario)

        self.assertIsInstance(exc, ReferenceError)
        self.assertEqual(self.fake.seen, [])
        self.assertEqual(self.errors, [])

    def test_closed_connector_raises_reference_error_without_a_popup(self):
        # after close() (or before start()) the semaphore is None: the same quiet
        # not-ready ReferenceError, at once, rather than a TypeError and a popup
        level = logger.logger.level
        logger.logger.setLevel(logging.DEBUG)  # every line is written: the log check must bite
        self.addCleanup(logger.logger.setLevel, level)
        _, exc = self.run_async(connector.getCurrentSummoner, session=False)

        self.assertIsInstance(exc, ReferenceError)
        self.assertEqual(self.errors, [])
        self.assertEqual(self.fake.seen, [])
        self.assertTrue(isReported(exc))
        self.assertEqual(self.delays, [])  # not retried
        self.assertIs(self.entries("getCurrentSummoner")[0].response, exc)
        # an expected state, not an error: one short line, no traceback
        self.assertEqual([r for r in LOG_RECORDS if "Traceback" in r], [])
        exits = [r for r in LOG_RECORDS if "exit getCurrentSummoner" in r]
        self.assertEqual(len(exits), 1)
        self.assertTrue(exits[0].startswith("WARNING"), exits[0])
        self.assertNotIn("\n", exits[0].rstrip())

    def test_close_during_a_request_ends_it_quietly_without_retry(self):
        self.fake.plan["/lol-summoner/v1/current-summoner"] = [("hold", 200, SUMMONER)]

        async def scenario():
            task = asyncio.ensure_future(connector.getCurrentSummoner())
            await self.until(lambda: self.fake.in_flight == 1, "the request held by the server")
            await connector.close()  # semaphore and sessions go away under the request
            try:
                return await task
            finally:
                self.fake.gate.set()

        _, exc = self.run_async(scenario)

        self.assertIsInstance(exc, ReferenceError)
        self.assertEqual(self.errors, [])
        self.assertEqual(len(self.fake.seen), 1)
        self.assertEqual([r for r in LOG_RECORDS if "Traceback" in r], [])

    def test_wait_between_attempts_is_the_configured_retry_sep(self):
        path = "/lol-match-history/v1/products/lol/p1/matches"
        self.fake.plan[path] = [ok({"nothing": True})]
        _, exc = self.run_async(lambda: connector.getSummonerGamesByPuuidSlowly("p1"))

        self.assertIsInstance(exc, SummonerGamesNotFound)
        self.assertEqual(len(self.fake.seen), 5)
        # only between attempts: not after the last one, so 4 waits for 5 attempts
        self.assertEqual([d for d in self.delays if d == 1], [1] * 4)

    def test_default_retry_does_not_wait_between_attempts(self):
        self.fake.plan["/lol-summoner/v1/current-summoner"] = ["drop"]
        self.run_async(connector.getCurrentSummoner)

        self.assertEqual([d for d in self.delays if d], [])

    def test_summoner_not_found_is_raised_at_once_without_retry_or_popup(self):
        # sendFriendRequest raises SummonerNotFound when the name lookup answers an error
        self.fake.plan["/lol-summoner/v1/summoners"] = [NOT_FOUND]
        _, exc = self.run_async(lambda: connector.sendFriendRequest("Nobody#NONE"))

        self.assertIsInstance(exc, SummonerNotFound)
        self.assertEqual(len(self.fake.seen), 1)  # a retry would be rate limited (429)
        self.assertEqual(self.errors, [])
        # recorded like the final-failure path, rendered through errorSummary
        entry = self.entries("sendFriendRequest")[0]
        self.assertIs(entry.response, exc)
        self.assertIn("response=SummonerNotFound", str(entry))

    def test_summoner_lookup_error_bodies_are_returned_like_the_sibling_lookups(self):
        # like getSummonerByName / ById: any error answer is the result, once, without a
        # popup; callers show "not found" for a body with errorCode
        for status, body in ((400, {"errorCode": "RPC_ERROR", "httpStatus": 400}),
                             (404, {"errorCode": "RPC_ERROR", "httpStatus": 404}),
                             (500, {"errorCode": "X"})):
            with self.subTest(status=status):
                self.fake.seen.clear()
                self.fake.plan["/lol-summoner/v2/summoners/puuid/p1"] = [("json", status, body)]
                res, exc = self.run_async(lambda: connector.getSummonerByPuuid("p1"))

                self.assertIsNone(exc)
                self.assertEqual(res, body)
                self.assertEqual(len(self.fake.seen), 1)
                self.assertEqual(self.errors, [])

    def test_semaphore_limits_parallel_requests(self):
        self.fake.plan["/lol-summoner/v1/current-summoner"] = [("hold", 200, SUMMONER)]

        async def scenario():
            tasks = [asyncio.ensure_future(connector.getCurrentSummoner()) for _ in range(5)]
            await self.until(lambda: self.fake.in_flight == 2,
                             "two requests held by the server")
            for _ in range(20):  # let the blocked calls run as far as they can
                await _real_sleep(0)
            blocked_peak = self.fake.max_in_flight
            self.fake.gate.set()
            return blocked_peak, await asyncio.gather(*tasks)

        (peak, results), exc = self.run_async(scenario, concurrency=2)

        self.assertIsNone(exc)
        self.assertEqual(peak, 2)
        self.assertEqual(self.fake.max_in_flight, 2)
        self.assertEqual(results, [SUMMONER] * 5)

    def test_retry_gives_the_semaphore_back_between_attempts(self):
        # one slot, a failing call: a second call can still get through afterwards
        self.fake.plan["/lol-summoner/v1/current-summoner"] = ["drop"]
        self.fake.plan["/lol-summoner/v1/summoners/9"] = [ok({"summonerId": 9})]

        async def scenario():
            with self.assertRaises(aiohttp.ClientError):
                await connector.getCurrentSummoner()
            return await connector.getSummonerById(9)

        res, exc = self.run_async(scenario, concurrency=1)
        self.assertIsNone(exc)
        self.assertEqual(res, {"summonerId": 9})

    def test_slow_answer_ends_in_a_timeout_after_every_attempt(self):
        self.fake.plan["/lol-summoner/v1/current-summoner"] = [("hold", 200, SUMMONER)]
        _, exc = self.run_async(connector.getCurrentSummoner, lcu_timeout=0.1)

        self.assertIsInstance(exc, asyncio.TimeoutError)
        self.assertEqual(len(self.fake.seen), 5)
        self.assertEqual([n for n, _ in self.errors], ["getCurrentSummoner"])


class EndpointErrorMappingTest(_ConnectorCase):

    def test_ranked_stats_success_returns_the_body(self):
        self.fake.plan["/lol-ranked/v1/ranked-stats/p1"] = [ok({"queues": [1]})]
        res, exc = self.run_async(lambda: connector.getRankedStatsByPuuid("p1"))
        self.assertEqual((res, exc), ({"queues": [1]}, None))

    def test_ranked_stats_error_body_raises_rank_not_found_after_five_attempts(self):
        self.fake.plan["/lol-ranked/v1/ranked-stats/p1"] = [
            ("json", 404, {"errorCode": "RPC_ERROR"})]
        _, exc = self.run_async(lambda: connector.getRankedStatsByPuuid("p1"))

        self.assertIsInstance(exc, SummonerRankInfoNotFound)
        self.assertEqual(len(self.fake.seen), 5)
        self.assertEqual([n for n, _ in self.errors], ["getRankedStatsByPuuid"])

    def test_match_history_sends_the_index_window_and_returns_the_games_object(self):
        path = "/lol-match-history/v1/products/lol/p1/matches"
        self.fake.plan[path] = [ok({"games": {"games": [{"gameId": 1}]}})]
        res, exc = self.run_async(lambda: connector.getSummonerGamesByPuuid("p1", 2, 6))

        self.assertIsNone(exc)
        self.assertEqual(res, {"games": [{"gameId": 1}]})
        self.assertEqual(self.fake.seen[0][2], "begIndex=2&endIndex=6")

    def test_match_history_without_games_raises_games_not_found(self):
        _, exc = self.run_async(lambda: connector.getSummonerGamesByPuuid("p1"))  # 404 body

        self.assertIsInstance(exc, SummonerGamesNotFound)
        self.assertEqual(len(self.fake.seen), 5)
        self.assertEqual([n for n, _ in self.errors], ["getSummonerGamesByPuuid"])

    def test_match_history_dropped_connection_is_reported_after_five_attempts(self):
        self.fake.plan["/lol-match-history/v1/products/lol/p1/matches"] = ["drop"]
        _, exc = self.run_async(lambda: connector.getSummonerGamesByPuuid("p1"))

        self.assertIsInstance(exc, aiohttp.ClientError)
        self.assertEqual(len(self.fake.seen), 5 * GET_RESEND)
        self.assertEqual(len(self.errors), 1)

    def test_game_detail_error_body_raises_games_not_found(self):
        # an error body is not a game: it fails like the match-history list (retried,
        # then one "abnormal content" notice), never handed on as the detail
        _, exc = self.run_async(lambda: connector.getGameDetailByGameId(5))

        self.assertIsInstance(exc, SummonerGamesNotFound)
        self.assertEqual(len(self.fake.seen), 5)
        self.assertEqual([n for n, _ in self.errors], ["getGameDetailByGameId"])
        self.assertTrue(isReported(exc))

    def test_game_detail_success_returns_the_body(self):
        game = {"gameId": 5, "participants": [{"participantId": 1}]}
        self.fake.plan["/lol-match-history/v1/games/5"] = [ok(game)]
        res, exc = self.run_async(lambda: connector.getGameDetailByGameId(5))
        self.assertEqual((res, exc), (game, None))

    def test_game_detail_non_json_body_raises_content_type_error(self):
        self.fake.plan["/lol-match-history/v1/games/5"] = [HTML]
        _, exc = self.run_async(lambda: connector.getGameDetailByGameId(5))

        self.assertIsInstance(exc, aiohttp.ContentTypeError)
        self.assertEqual([n for n, _ in self.errors], ["getGameDetailByGameId"])

    def test_gameflow_phase_strips_the_json_quotes(self):
        self.fake.plan["/lol-gameflow/v1/gameflow-phase"] = [
            ("raw", 200, b'"ChampSelect"', "application/json")]
        res, exc = self.run_async(connector.getGameStatus)
        self.assertEqual((res, exc), ("ChampSelect", None))

    def test_gameflow_phase_error_answer_is_the_idle_phase(self):
        # never a phase name made of the error JSON: the idle phase "None", which every
        # caller handles (startup, the reconnect loop, the updater's not-in-game check)
        cases = (("json", 404, {"errorCode": "RPC_ERROR"}),
                 ("json", 200, {"errorCode": "RPC_ERROR"}),
                 ("raw", 500, b'"InProgress"', "application/json"))
        for answer in cases:
            with self.subTest(status=answer[1]):
                self.fake.plan["/lol-gameflow/v1/gameflow-phase"] = [answer]
                res, exc = self.run_async(connector.getGameStatus)
                self.assertEqual((res, exc), ("None", None))
                self.assertEqual(self.errors, [])

    def test_gameflow_phase_dropped_connection_raises_once_without_retry_or_popup(self):
        self.fake.plan["/lol-gameflow/v1/gameflow-phase"] = ["drop"]
        _, exc = self.run_async(connector.getGameStatus)  # not decorated with @retry

        self.assertIsInstance(exc, aiohttp.ClientError)
        self.assertEqual(len(self.fake.seen), GET_RESEND)  # not retried by the connector; aiohttp re-sent the GET once
        self.assertEqual(self.errors, [])

    def test_gameflow_phase_without_session_raises_reference_error(self):
        _, exc = self.run_async(connector.getGameStatus, session=False)
        self.assertIsInstance(exc, ReferenceError)


QUIETLY = {
    # name: (call, path, ok body, expected, fallback)
    "game detail": (lambda: connector.getGameDetailByGameIdQuietly(5),
                    "/lol-match-history/v1/games/5",
                    {"participants": [{"x": 1}]}, {"participants": [{"x": 1}]}, None),
    "ranked stats": (lambda: connector.getRankedStatsByPuuidQuietly("p1"),
                     "/lol-ranked/v1/ranked-stats/p1",
                     {"queues": [1]}, {"queues": [1]}, None),
    "match history": (lambda: connector.getSummonerGamesByPuuidQuietly("p1"),
                      "/lol-match-history/v1/products/lol/p1/matches",
                      {"games": {"games": [{"gameId": 1}]}}, [{"gameId": 1}], None),
    "champion mastery": (lambda: connector.getChampionMasteryQuietly("p1"),
                         "/lol-champion-mastery/v1/p1/champion-mastery",
                         [{"championId": 3, "championLevel": 5, "championPoints": 900}],
                         {3: {"level": 5, "points": 900}}, {}),
}


class QuietlyVariantsTest(_ConnectorCase):

    def each(self):
        for label, spec in QUIETLY.items():
            with self.subTest(variant=label):
                self.fake.plan = {}
                del self.fake.seen[:]
                connector.callStack.clear()
                yield spec

    def test_success_returns_the_parsed_value(self):
        for call, path, body, expected, _ in self.each():
            self.fake.plan[path] = [ok(body)]
            res, exc = self.run_async(call)
            self.assertEqual((res, exc), (expected, None))

    def test_error_status_returns_the_fallback(self):
        for call, path, _, _, fallback in self.each():
            self.fake.plan[path] = [("json", 500, {"errorCode": "RPC_ERROR"})]
            res, exc = self.run_async(call)
            self.assertEqual((res, exc), (fallback, None))

    def test_non_json_body_returns_the_fallback(self):
        for call, path, _, _, fallback in self.each():
            self.fake.plan[path] = [HTML]
            res, exc = self.run_async(call)
            self.assertEqual((res, exc), (fallback, None))

    def test_dropped_connection_returns_the_fallback(self):
        for call, path, _, _, fallback in self.each():
            self.fake.plan[path] = ["drop"]
            res, exc = self.run_async(call)
            self.assertEqual((res, exc), (fallback, None))

    def test_slow_answer_returns_the_fallback(self):
        for call, path, _, _, fallback in self.each():
            self.fake.plan[path] = [("hold", 200, {})]
            res, exc = self.run_async(call, lcu_timeout=0.1)
            self.assertEqual((res, exc), (fallback, None))

    def test_closed_connector_returns_the_fallback(self):
        for call, _, _, _, fallback in self.each():
            res, exc = self.run_async(call, session=False)
            self.assertEqual((res, exc), (fallback, None))
            self.assertEqual(self.fake.seen, [])

    def test_failures_send_one_request_emit_no_signal_and_leave_no_log_entry(self):
        for call, path, _, _, _ in self.each():
            for action in (["drop"], [HTML], [("json", 500, {})], [("hold", 200, {})]):
                self.fake.plan[path] = action
                del self.fake.seen[:]
                self.run_async(call, lcu_timeout=0.1)
                self.assertEqual(len(self.fake.seen), GET_RESEND if action == ["drop"] else 1, (path, action))
            self.assertEqual(self.errors, [])
            self.assertEqual(len(connector.callStack), 0)

    def test_game_detail_without_participants_is_the_fallback(self):
        self.fake.plan["/lol-match-history/v1/games/5"] = [ok({"gameId": 5})]
        res, exc = self.run_async(lambda: connector.getGameDetailByGameIdQuietly(5))
        self.assertEqual((res, exc), (None, None))

    def test_ranked_stats_with_error_code_is_the_fallback(self):
        self.fake.plan["/lol-ranked/v1/ranked-stats/p1"] = [
            ok({"errorCode": "RPC_ERROR", "httpStatus": 404})]
        res, exc = self.run_async(lambda: connector.getRankedStatsByPuuidQuietly("p1"))
        self.assertEqual((res, exc), (None, None))

    def test_champion_mastery_skips_malformed_items(self):
        self.fake.plan["/lol-champion-mastery/v1/p1/champion-mastery"] = [
            ok([{"championId": 3}, {"noId": 1}, "junk"])]
        res, exc = self.run_async(lambda: connector.getChampionMasteryQuietly("p1"))
        self.assertEqual((res, exc), ({3: {"level": 0, "points": 0}}, None))

    def test_cancellation_is_not_swallowed(self):
        self.fake.plan["/lol-match-history/v1/games/5"] = [("hold", 200, {})]

        async def scenario():
            task = asyncio.ensure_future(connector.getGameDetailByGameIdQuietly(5))
            await self.until(lambda: self.fake.in_flight == 1, "the request is being held")
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                return "cancelled"
            return "finished"

        res, exc = self.run_async(scenario)
        self.assertEqual((res, exc), ("cancelled", None))


class RequestLogTest(_ConnectorCase):

    def test_entry_holds_function_name_arguments_and_result(self):
        self.fake.plan["/lol-match-history/v1/products/lol/p1/matches"] = [
            ok({"games": {"games": []}})]
        res, _ = self.run_async(lambda: connector.getSummonerGamesByPuuid("p1", endIndex=3))

        entry = self.entries()[0]
        self.assertEqual(entry.func, "getSummonerGamesByPuuid")
        self.assertEqual(entry.params_dict, {"puuid": "p1"})
        self.assertEqual(entry.kwargs, {"endIndex": 3})
        self.assertEqual(entry.response, res)

    def test_failed_entry_holds_the_exception_and_no_token(self):
        self.fake.plan["/lol-summoner/v1/current-summoner"] = ["drop"]
        _, exc = self.run_async(connector.getCurrentSummoner)

        rendered = " ".join(str(e) for e in connector.callStack)
        self.assertIn(type(exc).__name__, rendered)
        basic = base64.b64encode(f"riot:{LCU_TOKEN}".encode()).decode()
        for secret in (LCU_TOKEN, basic, "Authorization"):
            self.assertNotIn(secret, rendered)

    def test_only_the_ten_newest_entries_are_kept(self):
        self.fake.plan["/lol-summoner/v1/summoners/1"] = [ok({"summonerId": 1})]

        async def scenario():
            for _ in range(12):
                await connector.getSummonerById(1)

        self.run_async(scenario)
        self.assertEqual(len(connector.callStack), 10)

    def test_secret_result_is_never_kept_or_logged(self):
        self.fake.plan["/entitlements/v1/token"] = [ok({"accessToken": SGP_TOKEN})]
        level = logger.logger.level
        logger.logger.setLevel(logging.DEBUG)  # debug lines carry results: the check must bite
        self.addCleanup(logger.logger.setLevel, level)

        res, exc = self.run_async(connector.getSGPtoken)

        self.assertEqual((res, exc), (SGP_TOKEN, None))
        self.assertEqual(self.entries("getSGPtoken")[0].response, REDACTED)
        self.assertNotIn(SGP_TOKEN, " ".join(str(e) for e in connector.callStack))
        self.assertTrue(any("result = " in r for r in LOG_RECORDS), "debug logging was not on")
        self.assertEqual([r for r in LOG_RECORDS if SGP_TOKEN in r], [])

    def test_match_server_token_is_fetched_once_cached_and_never_recorded(self):
        self.fake.plan["/entitlements/v1/token"] = [ok({"accessToken": SGP_TOKEN})]
        level = logger.logger.level
        logger.logger.setLevel(logging.DEBUG)
        self.addCleanup(logger.logger.setLevel, level)

        async def scenario():
            return (await connector.getSGPtokenQuietly(),
                    await connector.getSGPtokenQuietly())

        res, exc = self.run_async(scenario)

        self.assertEqual((res, exc), ((SGP_TOKEN, SGP_TOKEN), None))
        self.assertEqual(self.fake.paths(), ["/entitlements/v1/token"])
        self.assertEqual(len(connector.callStack), 0)
        self.assertEqual([r for r in LOG_RECORDS if SGP_TOKEN in r], [])

    def test_match_server_token_fresh_refetches(self):
        self.fake.plan["/entitlements/v1/token"] = [ok({"accessToken": "first-token"}),
                                                    ok({"accessToken": "second-token"})]

        async def scenario():
            return (await connector.getSGPtokenQuietly(),
                    await connector.getSGPtokenQuietly(fresh=True))

        res, _ = self.run_async(scenario)
        self.assertEqual(res, ("first-token", "second-token"))

    def test_match_server_token_failures_return_none_without_a_signal(self):
        for label, action in (("error body", [("json", 404, {"errorCode": "X"})]),
                              ("empty token", [ok({"accessToken": ""})]),
                              ("dropped", ["drop"])):
            with self.subTest(label):
                connector.matchSgpToken = None
                self.fake.plan["/entitlements/v1/token"] = action
                res, exc = self.run_async(connector.getSGPtokenQuietly)
                self.assertEqual((res, exc), (None, None))
        self.assertEqual(self.errors, [])


class LoginSummonerByPidTest(_ConnectorCase):
    """getLoginSummonerByPid: a synchronous requests call with a short timeout, used on the
    UI thread. The https URL is rewritten to the fake client; everything else is real."""

    PORT = 55555

    def _call(self, timeout=None):
        sent = {}

        def get(url, **kw):
            sent.update(kw, url=url)
            http = requests.Session()
            http.trust_env = False  # no proxy may sit between the test and 127.0.0.1
            try:
                return http.get(url.replace(f"https://127.0.0.1:{self.PORT}", self.fake.url), **kw)
            finally:
                http.close()

        patches = [mock.patch.object(connector_module, "getPortTokenServerByPid",
                                     return_value=(self.PORT, LCU_TOKEN, "NA1")),
                   mock.patch.object(requests, "get", get)]
        if timeout is not None:
            patches.append(mock.patch.object(connector_module, "LOGIN_SUMMONER_TIMEOUT", timeout))
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return connector.getLoginSummonerByPid(1234), sent

    def test_returns_the_summoner_and_sends_basic_auth_without_verification(self):
        self.fake.plan["/lol-summoner/v1/current-summoner"] = [ok(SUMMONER)]
        res, sent = self._call()

        self.assertEqual(res, SUMMONER)
        self.assertEqual(sent["auth"], ("riot", LCU_TOKEN))
        self.assertIs(sent["verify"], False)
        self.assertEqual(sent["timeout"], connector_module.LOGIN_SUMMONER_TIMEOUT)
        self.assertTrue(sent["url"].startswith(f"https://127.0.0.1:{self.PORT}/"))

    def test_error_status_returns_none(self):
        self.assertIsNone(self._call()[0])  # 404

    def test_non_json_body_returns_none(self):
        self.fake.plan["/lol-summoner/v1/current-summoner"] = [HTML]
        self.assertIsNone(self._call()[0])

    def test_non_object_body_returns_none(self):
        self.fake.plan["/lol-summoner/v1/current-summoner"] = [ok([1, 2])]
        self.assertIsNone(self._call()[0])

    def test_dropped_connection_returns_none(self):
        self.fake.plan["/lol-summoner/v1/current-summoner"] = ["drop"]
        self.assertIsNone(self._call()[0])

    def test_slow_client_returns_none_after_the_timeout(self):
        self.fake.plan["/lol-summoner/v1/current-summoner"] = [("hold", 200, SUMMONER)]
        res, sent = self._call(timeout=0.2)

        self.assertIsNone(res)
        self.assertEqual(sent["timeout"], 0.2)

    def test_discovery_failure_returns_none_without_a_request(self):
        with mock.patch.object(connector_module, "getPortTokenServerByPid",
                               side_effect=RuntimeError("no client")):
            self.assertIsNone(connector.getLoginSummonerByPid(1234))
        self.assertEqual(self.fake.seen, [])


if __name__ == "__main__":
    unittest.main()
