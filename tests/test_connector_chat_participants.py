"""connector.getChampSelectChatParticipants (reveal hidden teammates, task 2).

Drives the real method against a real local HTTP server (stdlib http.server)
bound to 127.0.0.1 — nothing under test is mocked. Each scenario flips the
handler's mode; the method must return [] on every failure shape.
"""
import asyncio
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import aiohttp

from app.lol.connector import connector


class _Handler(BaseHTTPRequestHandler):
    mode = "ok"
    seen = []

    def log_message(self, *args):  # keep unittest output clean
        pass

    def _send(self, status, body, ctype="application/json"):
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        _Handler.seen.append(self.path)
        mixed = {"participants": [
            {"puuid": "p1", "cid": "abc123@champ-select.na1.pvp.net",
             "game_name": "Alpha", "game_tag": "NA1"},
            {"puuid": "p2", "cid": "def456@sec.na1.pvp.net",
             "game_name": "LobbyOnly", "game_tag": "NA1"},
            "not-a-dict",
        ]}

        if self.mode == "ok" and self.path == "/chat/v5/participants/champ-select":
            return self._send(200, mixed)
        if self.mode == "fallback":
            if self.path == "/chat/v5/participants/champ-select":
                return self._send(404, {"errorCode": "NOT_FOUND"})
            if self.path == "/chat/v5/participants":
                return self._send(200, mixed)
        if self.mode == "empty200":
            # live Riot Client: filtered endpoint answers 200 with an empty list
            if self.path == "/chat/v5/participants/champ-select":
                return self._send(200, {"participants": []})
            if self.path == "/chat/v5/participants":
                return self._send(200, mixed)
        if self.mode == "garbage" and self.path == "/chat/v5/participants/champ-select":
            return self._send(200, b"<html>not json</html>", "text/html")
        if self.mode == "shape" and self.path == "/chat/v5/participants/champ-select":
            return self._send(200, {"participants": "nope"})
        return self._send(404, {"errorCode": "NOT_FOUND"})


class ChatParticipantsTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(
            target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        _Handler.seen = []

    def _call(self, mode):
        _Handler.mode = mode

        async def go():
            connector.rcSess = aiohttp.ClientSession(
                base_url=f"http://127.0.0.1:{self.port}")
            try:
                return await connector.getChampSelectChatParticipants()
            finally:
                await connector.rcSess.close()
                connector.rcSess = None

        return asyncio.run(go())

    def test_no_session_returns_empty(self):
        connector.rcSess = None
        self.assertEqual(asyncio.run(
            connector.getChampSelectChatParticipants()), [])

    def test_ok_filters_to_champ_select_cids(self):
        res = self._call("ok")
        self.assertEqual([p["puuid"] for p in res], ["p1"])
        self.assertEqual(_Handler.seen, ["/chat/v5/participants/champ-select"])

    def test_non_200_falls_back_to_full_list(self):
        res = self._call("fallback")
        self.assertEqual([p["puuid"] for p in res], ["p1"])
        self.assertEqual(_Handler.seen, ["/chat/v5/participants/champ-select",
                                         "/chat/v5/participants"])

    def test_empty_200_falls_back_to_full_list(self):
        res = self._call("empty200")
        self.assertEqual([p["puuid"] for p in res], ["p1"])
        self.assertEqual(_Handler.seen, ["/chat/v5/participants/champ-select",
                                         "/chat/v5/participants"])

    def test_both_endpoints_404_returns_empty(self):
        self.assertEqual(self._call("dead"), [])

    def test_non_json_body_returns_empty(self):
        self.assertEqual(self._call("garbage"), [])

    def test_wrong_shape_returns_empty(self):
        self.assertEqual(self._call("shape"), [])

    def test_connection_refused_returns_empty(self):
        async def go():
            # nothing listens on this port: request raises inside, must be swallowed
            connector.rcSess = aiohttp.ClientSession(base_url="http://127.0.0.1:9")
            try:
                return await connector.getChampSelectChatParticipants()
            finally:
                await connector.rcSess.close()
                connector.rcSess = None

        self.assertEqual(asyncio.run(go()), [])


if __name__ == "__main__":
    unittest.main()
