#!/usr/bin/env python3
"""Fixed, bounded HTTP probes behind the existing TLS nginx virtual host."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socket
import threading
import time
from urllib.parse import urlsplit


LISTEN = ("127.0.0.1", 18089)
STREAM_BODY = b"chain-probe\n"
STREAM_SECONDS = 6.0
MAX_CONNECTIONS = 8
SOCKET_TIMEOUT = 10


class ProbeServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = MAX_CONNECTIONS

    def __init__(self, address=LISTEN):
        self.slots = threading.BoundedSemaphore(MAX_CONNECTIONS)
        super().__init__(address, ProbeHandler)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            try:
                request.settimeout(0.5)
                request.sendall(b"HTTP/1.1 503 Service Unavailable\r\n"
                                b"Content-Length: 0\r\nConnection: close\r\n\r\n")
            except OSError:
                pass
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


class ProbeHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "ChainProbe"
    sys_version = ""

    def setup(self):
        super().setup()
        self.connection.settimeout(SOCKET_TIMEOUT)
        self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    def log_message(self, format, *args):
        pass

    def respond(self, status, length):
        self.send_response(status)
        self.send_header("Content-Length", str(length))
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/chain-probe/health":
            self.respond(204, 0)
        elif path == "/chain-probe/stream":
            self.respond(200, len(STREAM_BODY))
            start = time.monotonic()
            try:
                # Absolute deadlines keep the last byte at six seconds, not 12 sleeps.
                for index, byte in enumerate(STREAM_BODY):
                    due = start + index * STREAM_SECONDS / (len(STREAM_BODY) - 1)
                    time.sleep(max(0.0, due - time.monotonic()))
                    self.wfile.write(bytes([byte]))
                    self.wfile.flush()
            except OSError:
                pass
        else:
            self.respond(404, 0)

    def denied(self):
        self.send_response(405)
        self.send_header("Allow", "GET")
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

    do_HEAD = do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_TRACE = denied


if __name__ == "__main__":
    with ProbeServer() as server:
        server.serve_forever()
