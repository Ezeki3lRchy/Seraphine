"""connector: friend request, the retry decorator, and replays of state-changing calls.

Drives the real methods against a real local HTTP server (stdlib http.server)
bound to 127.0.0.1 on an ephemeral port.
"""
import asyncio
import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import aiohttp

from app.lol.connector import connector, retry
from app.lol.exceptions import SummonerGamesNotFound, SummonerNotFound


class _Handler(BaseHTTPRequestHandler):
    # path -> list of planned answers, consumed one per request; "drop" closes the
    # connection after reading the request, without answering
    plan = {}
    seen = []

    def log_message(self, *args):
        pass

    def _handle(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        body = json.loads(raw) if raw else None
        _Handler.seen.append((self.command, self.path, body))

        answers = _Handler.plan.get(self.path.split("?")[0]) or [(404, {"errorCode": "NOT_FOUND"})]
        answer = answers.pop(0) if len(answers) > 1 else answers[0]
        if answer == "drop":
            self.close_connection = True
            return

        status, payload = answer
        data = b"" if payload is None else json.dumps(payload).encode()
        self.send_response(status)
        if payload is not None:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _handle


class _LcuTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        _Handler.plan = {}
        _Handler.seen = []

    def _run(self, make_coro):
        """Runs make_coro() with an LCU session pointed at the fake server; returns
        (result, exception) so the exception never leaves the event loop."""
        async def go():
            connector.semaphore = asyncio.Semaphore(4)
            connector.lcuSess = aiohttp.ClientSession(base_url=f"http://127.0.0.1:{self.port}")
            try:
                return await make_coro(), None
            except BaseException as e:  # incl. the app's own BaseException subclasses
                return None, e
            finally:
                await connector.lcuSess.close()
                connector.lcuSess = None
                connector.semaphore = None

        return asyncio.run(go())


class FriendRequestTest(_LcuTestCase):

    def test_known_name_sends_the_request(self):
        _Handler.plan = {
            "/lol-summoner/v1/summoners": [(200, {"summonerId": 42, "puuid": "test-puuid"})],
            "/lol-chat/v1/friend-requests": [(204, None)],
        }
        _, exc = self._run(lambda: connector.sendFriendRequest("Tester#TEST"))

        self.assertIsNone(exc)
        posts = [s for s in _Handler.seen if s[0] == "POST"]
        self.assertEqual(posts, [("POST", "/lol-chat/v1/friend-requests", {"name": "Tester#TEST"})])
        self.assertEqual(_Handler.seen[0][0], "GET")

    def test_unknown_name_raises_summoner_not_found_without_posting(self):
        _Handler.plan = {
            "/lol-summoner/v1/summoners": [(404, {"errorCode": "RPC_ERROR", "httpStatus": 404})],
        }
        _, exc = self._run(lambda: connector.sendFriendRequest("Nobody#NONE"))

        self.assertIsInstance(exc, SummonerNotFound)
        self.assertEqual([s[0] for s in _Handler.seen], ["GET"])  # looked up once, not retried


class RetryDecoratorTest(_LcuTestCase):

    def _raiser(self, exc_type):
        calls = []

        @retry()
        async def failing():
            calls.append(1)
            raise exc_type()

        return failing, calls

    def test_interpreter_exits_propagate_on_first_attempt(self):
        for exc_type in (KeyboardInterrupt, SystemExit, GeneratorExit):
            with self.subTest(exc_type=exc_type.__name__):
                failing, calls = self._raiser(exc_type)
                _, exc = self._run(failing)
                self.assertIsInstance(exc, exc_type)
                self.assertEqual(len(calls), 1)

    def test_cancelled_error_is_re_raised_at_once(self):
        failing, calls = self._raiser(asyncio.CancelledError)
        _, exc = self._run(failing)
        self.assertIsInstance(exc, asyncio.CancelledError)
        self.assertEqual(len(calls), 1)

    def test_app_base_exception_subclass_is_still_retried(self):
        failing, calls = self._raiser(SummonerGamesNotFound)
        _, exc = self._run(failing)
        self.assertIsInstance(exc, SummonerGamesNotFound)
        self.assertEqual(len(calls), 5)


class StateChangeReplayTest(_LcuTestCase):

    def test_create_rune_page_is_not_replayed_after_the_request_was_sent(self):
        # the client got the POST, then the connection drops before an answer
        _Handler.plan = {"/lol-perks/v1/pages": ["drop", (200, {"id": 1})]}
        _, exc = self._run(lambda: connector.createRunePage("Test", 8000, 8100, [1, 2, 3]))

        self.assertIsNotNone(exc)
        self.assertEqual([s[:2] for s in _Handler.seen], [("POST", "/lol-perks/v1/pages")])

    def test_reroll_is_not_replayed_after_the_request_was_sent(self):
        path = "/lol-champ-select/v1/session/my-selection/reroll"
        _Handler.plan = {path: ["drop", (200, {})]}
        _, exc = self._run(connector.reroll)

        self.assertIsNotNone(exc)
        self.assertEqual([s[:2] for s in _Handler.seen], [("POST", path)])

    def test_select_champion_is_still_retried(self):
        path = "/lol-champ-select/v1/session/actions/7"
        _Handler.plan = {path: ["drop", (204, None)]}
        _, exc = self._run(lambda: connector.selectChampion(7, 103, completed=True))

        self.assertIsNone(exc)
        self.assertEqual([s[:2] for s in _Handler.seen], [("PATCH", path), ("PATCH", path)])

    def test_accept_ready_check_is_still_retried(self):
        path = "/lol-matchmaking/v1/ready-check/accept"
        _Handler.plan = {path: ["drop", (204, None)]}
        _, exc = self._run(connector.acceptMatchMaking)

        self.assertIsNone(exc)
        self.assertEqual([s[:2] for s in _Handler.seen], [("POST", path), ("POST", path)])


if __name__ == "__main__":
    unittest.main()
