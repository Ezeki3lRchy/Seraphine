"""connector.getLoginSummonerByPid and the change-client dialog built from it.

The lookup is synchronous and runs on the UI thread, so it must give up quickly on a
client that never answers, and the dialog must still list a client that answers with an
error. Servers are bound to 127.0.0.1 on ephemeral ports; the dialog is built offscreen
and never shown.
"""
import base64
import json
import os
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget

from app.lol import connector as connector_module
from app.lol.connector import connector


def _patchedPortToken(port):
    return mock.patch.object(connector_module, "getPortTokenServerByPid",
                             return_value=(port, "test-token", None))


class SilentClientTest(unittest.TestCase):
    """A client that accepts the connection and never answers."""

    def setUp(self):
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen()
        self.port = self.listener.getsockname()[1]
        self.accepted = []
        self.stop = threading.Event()

        def hold():
            self.listener.settimeout(0.1)
            while not self.stop.is_set():
                try:
                    conn, _ = self.listener.accept()
                except OSError:
                    continue
                self.accepted.append(conn)

        self.holder = threading.Thread(target=hold, daemon=True)
        self.holder.start()

    def tearDown(self):
        self.stop.set()
        self.holder.join(2)
        for conn in self.accepted:
            conn.close()
        self.listener.close()

    def test_returns_none_within_the_timeout(self):
        done = threading.Event()
        result = {}

        def lookup():
            with _patchedPortToken(self.port):
                result["value"] = connector.getLoginSummonerByPid(1234)
            done.set()

        start = time.monotonic()
        threading.Thread(target=lookup, daemon=True).start()
        # connect + read timeouts, with slack for a slow runner
        limit = 2 * getattr(connector_module, "LOGIN_SUMMONER_TIMEOUT", 1.5) + 3

        self.assertTrue(done.wait(limit), "lookup hung on a client that never answers")
        self.assertIsNone(result["value"])
        self.assertLess(time.monotonic() - start, limit)

    def test_closed_port_returns_none(self):
        free = socket.socket()
        free.bind(("127.0.0.1", 0))
        port = free.getsockname()[1]
        free.close()  # nothing listens here any more

        with _patchedPortToken(port):
            self.assertIsNone(connector.getLoginSummonerByPid(1234))

    def test_vanished_process_returns_none(self):
        with mock.patch.object(connector_module, "getPortTokenServerByPid",
                               side_effect=ProcessLookupError()):
            self.assertIsNone(connector.getLoginSummonerByPid(1234))


class _Handler(BaseHTTPRequestHandler):
    answer = (200, {})
    # Authorization header of every request, checked (never printed) by the tests
    auth = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        _Handler.auth.append(self.headers.get("Authorization"))
        status, body = _Handler.answer
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@unittest.skipIf(shutil.which("openssl") is None, "openssl is needed for a throwaway TLS cert")
class ErrorBodyTest(unittest.TestCase):
    """The lookup talks https (like the real client), so the fake server uses a
    self-signed certificate generated for this run only."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cert, key = os.path.join(cls.tmp, "cert.pem"), os.path.join(cls.tmp, "key.pem")
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                        "-keyout", key, "-out", cert, "-days", "1", "-subj", "/CN=127.0.0.1"],
                       check=True, capture_output=True, timeout=60)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.server.socket = context.wrap_socket(cls.server.socket, server_side=True)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _lookup(self, answer):
        _Handler.answer = answer
        with _patchedPortToken(self.port):
            return connector.getLoginSummonerByPid(1234)

    def test_summoner_is_returned(self):
        self.assertEqual(self._lookup((200, {"gameName": "Tester"})), {"gameName": "Tester"})

    def test_request_carries_the_client_login(self):
        # the LCU authenticates as riot:<remoting token> (HTTP Basic); the header is
        # compared, never printed
        _Handler.auth = []
        self._lookup((200, {"gameName": "Tester"}))
        expected = "Basic " + base64.b64encode(b"riot:test-token").decode()
        self.assertEqual(len(_Handler.auth), 1)
        self.assertTrue(_Handler.auth[0] == expected, "missing or wrong Authorization header")

    def test_error_body_returns_none(self):
        self.assertIsNone(self._lookup((404, {"errorCode": "RPC_ERROR", "httpStatus": 404})))

    def test_non_json_body_returns_none(self):
        self.assertIsNone(self._lookup((200, b"<html>starting</html>")))


class ChangeClientDialogTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from app.components.message_box import ChangeClientMessageBox
        self.Box = ChangeClientMessageBox
        self.parent = QWidget()  # never shown
        self.savedPid = getattr(connector, "pid", None)

    def tearDown(self):
        connector.pid = self.savedPid
        self.parent.deleteLater()

    def _items(self, box):
        return [box.comboBox.itemText(i) for i in range(box.comboBox.count())]

    def test_unanswering_client_is_listed_by_pid(self):
        answers = {101: None, 202: {"gameName": "Tester"}}
        connector.pid = 202
        with mock.patch.object(connector, "getLoginSummonerByPid", side_effect=answers.get):
            box = self.Box(pids=[101, 202], parent=self.parent)

        items = self._items(box)
        self.assertEqual(len(items), 2)
        self.assertIn("101", items[0])
        self.assertNotIn("None", items[0])
        self.assertTrue(items[1].startswith("Tester"))
        self.assertEqual(box.comboBox.currentIndex(), 1)

    def test_current_client_missing_from_the_list(self):
        connector.pid = 999
        with mock.patch.object(connector, "getLoginSummonerByPid", return_value=None):
            box = self.Box(pids=[101], parent=self.parent)

        self.assertEqual(box.comboBox.count(), 1)
        self.assertEqual(box.comboBox.currentIndex(), 0)


if __name__ == "__main__":
    unittest.main()
