"""Cancellation while a local engine has not yet sent response headers."""

import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from runtime_deck_core.evaluation import EvaluationController


class EvaluationCancellationTests(unittest.TestCase):
    def test_stop_interrupts_pending_response(self):
        received = threading.Event()
        release = threading.Event()

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                received.set()
                release.wait(5)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        controller = EvaluationController.__new__(EvaluationController)
        controller.cancel = threading.Event()
        controller._connection_lock = threading.Lock()
        controller._connection = None
        errors = []

        def request():
            try:
                controller._request_json(f"http://127.0.0.1:{server.server_port}", {})
            except Exception as exc:
                errors.append(exc)

        worker = threading.Thread(target=request, daemon=True)
        try:
            worker.start()
            self.assertTrue(received.wait(3))
            controller.stop()
            worker.join(2)
            self.assertFalse(worker.is_alive(), "Cancelled request remained blocked")
            self.assertTrue(errors)
            self.assertIsNone(controller._connection)
        finally:
            controller.stop()
            release.set()
            server.shutdown()
            server.server_close()
            worker.join(3)
