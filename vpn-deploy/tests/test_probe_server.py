import http.client
import importlib.util
from pathlib import Path
import socket
import threading
import time
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    "probe_server", Path(__file__).resolve().parents[1] / "probe_server.py")
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class ProbeServerTests(unittest.TestCase):
    def setUp(self):
        self.server = probe.ProbeServer(("127.0.0.1", 0))
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()
        self.connection = http.client.HTTPConnection(*self.server.server_address, timeout=2)

    def tearDown(self):
        self.connection.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_health_is_empty_204_with_query_ignored(self):
        self.connection.request("GET", "/chain-probe/health?probe=nonce")
        response = self.connection.getresponse()
        self.assertEqual(response.status, 204)
        self.assertEqual(response.read(), b"")
        self.assertEqual(response.getheader("Cache-Control"), "no-store")
        self.assertEqual(response.getheader("Connection"), "close")

    def test_stream_has_exact_body_and_is_server_paced(self):
        with patch.object(probe, "STREAM_SECONDS", 0.22):
            self.connection.request("GET", "/chain-probe/stream?duration=0&numbytes=90000")
            response = self.connection.getresponse()
            started = time.monotonic()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.getheader("Content-Length"), "12")
            first = response.read(1)
            first_elapsed = time.monotonic() - started
            body = first + response.read()
            elapsed = time.monotonic() - started
        self.assertEqual(body, probe.STREAM_BODY)
        self.assertEqual(len(body), 12)
        self.assertLess(first_elapsed, 0.15)
        self.assertGreaterEqual(elapsed, 0.18)
        self.assertEqual(response.getheader("X-Accel-Buffering"), "no")

    def test_unknown_path_is_empty_404(self):
        self.connection.request("GET", "/chain-probe/unknown")
        response = self.connection.getresponse()
        self.assertEqual(response.status, 404)
        self.assertEqual(response.read(), b"")

    def test_non_get_methods_rejected(self):
        for method in ["HEAD", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "TRACE"]:
            with self.subTest(method=method):
                self.connection.request(method, "/chain-probe/stream")
                response = self.connection.getresponse()
                self.assertEqual(response.status, 405)
                self.assertEqual(response.getheader("Allow"), "GET")
                self.assertEqual(response.read(), b"")

    def test_connection_cap_rejects_and_then_recovers(self):
        for _ in range(probe.MAX_CONNECTIONS):
            self.assertTrue(self.server.slots.acquire(blocking=False))
        try:
            with socket.create_connection(self.server.server_address, timeout=2) as connection:
                self.assertIn(b"503 Service Unavailable", connection.recv(256))
        finally:
            for _ in range(probe.MAX_CONNECTIONS):
                self.server.slots.release()
        self.connection.request("GET", "/chain-probe/health")
        response = self.connection.getresponse()
        self.assertEqual(response.status, 204)
        self.assertEqual(response.read(), b"")

    def test_default_stream_duration_and_binding(self):
        self.assertEqual(probe.STREAM_SECONDS, 6.0)
        self.assertEqual(probe.LISTEN, ("127.0.0.1", 18089))


if __name__ == "__main__":
    unittest.main()
