"""The desktop launcher reuses a running server only when it runs the same code."""
import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'token-graph' / 'scripts'))
import explorer_server  # noqa: E402


def fake_server(state_file: Path, code):
    """A stand-in for an already running Explorer server answering ping and shutdown."""
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _reply(self, payload):
            body = json.dumps(payload).encode()
            self.send_response(200); self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)

        def do_GET(self):
            self._reply({'ok': True, **({'code': code} if code else {})})

        def do_POST(self):
            self.server.stopped = True
            self._reply({'ok': True})
            state_file.unlink(missing_ok=True)   # what the old process does as it exits
            threading.Thread(target=self.server.shutdown, daemon=True).start()

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.stopped = False
    state_file.write_text(json.dumps({'port': server.server_address[1], 'token': 't', 'pid': 1}))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class ServerRestartTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / '.explorer-server.json'
        self.saved, explorer_server.STATE = explorer_server.STATE, self.state

    def tearDown(self):
        explorer_server.STATE = self.saved
        self.tmp.cleanup()

    def test_same_code_is_reused(self):
        server = fake_server(self.state, explorer_server.CODE_VERSION)
        try:
            self.assertTrue(explorer_server.existing_server().startswith('http://127.0.0.1:'))
            self.assertFalse(server.stopped)
        finally:
            server.shutdown()

    def test_outdated_server_is_stopped_and_replaced(self):
        server = fake_server(self.state, None)   # started before code versions existed
        self.assertIsNone(explorer_server.existing_server())
        self.assertTrue(server.stopped)
        self.assertFalse(self.state.exists())


if __name__ == '__main__':
    unittest.main()
