"""Tests for the resilient HTTP helpers (retry with backoff)."""

from __future__ import annotations

import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from mvs_pipeline.collector.http import USER_AGENT, fetch_bytes, open_stream


class _FlakyHandler(BaseHTTPRequestHandler):
    """Serve ``fail_times`` responses of ``fail_status``, then 200 with a body."""

    def log_message(self, *args: object) -> None:  # silence test output
        pass

    def _respond(self) -> None:
        srv = self.server
        with srv.lock:  # type: ignore[attr-defined]
            srv.hits += 1  # type: ignore[attr-defined]
            fail = srv.hits <= srv.fail_times  # type: ignore[attr-defined]
            status = srv.fail_status  # type: ignore[attr-defined]
        if fail:
            self.send_response(status)
            if srv.retry_after is not None:  # type: ignore[attr-defined]
                self.send_header("Retry-After", str(srv.retry_after))  # type: ignore[attr-defined]
            self.end_headers()
            return
        body = b"payload-ok"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = _respond
    do_HEAD = _respond


def _server(*, fail_times: int, fail_status: int = 503, retry_after: int | None = None):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _FlakyHandler)
    srv.hits = 0  # type: ignore[attr-defined]
    srv.fail_times = fail_times  # type: ignore[attr-defined]
    srv.fail_status = fail_status  # type: ignore[attr-defined]
    srv.retry_after = retry_after  # type: ignore[attr-defined]
    srv.lock = threading.Lock()  # type: ignore[attr-defined]
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/x"
    return srv, thread, url


def test_fetch_bytes_retries_then_succeeds() -> None:
    srv, thread, url = _server(fail_times=2, fail_status=503)
    delays: list[float] = []
    try:
        data = fetch_bytes(url, retries=5, backoff_base=0.01, sleep=delays.append)
        assert data == b"payload-ok"
        assert srv.hits == 3  # two 503s + one success  # type: ignore[attr-defined]
        assert delays == [0.01, 0.02]  # exponential backoff between the two retries
    finally:
        srv.shutdown()
        thread.join()


def test_open_stream_retries_then_succeeds() -> None:
    srv, thread, url = _server(fail_times=1, fail_status=500)
    try:
        resp = open_stream(url, retries=3, backoff_base=0.0, sleep=lambda _: None)
        assert resp.read() == b"payload-ok"
        resp.close()
    finally:
        srv.shutdown()
        thread.join()


def test_retry_after_header_is_honored() -> None:
    srv, thread, url = _server(fail_times=1, fail_status=429, retry_after=7)
    delays: list[float] = []
    try:
        fetch_bytes(url, retries=2, backoff_base=99.0, sleep=delays.append)
        assert delays == [7.0]  # header wins over the (much larger) backoff
    finally:
        srv.shutdown()
        thread.join()


def test_non_retryable_status_raises_immediately() -> None:
    srv, thread, url = _server(fail_times=99, fail_status=404)
    calls: list[float] = []
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            fetch_bytes(url, retries=5, backoff_base=0.0, sleep=calls.append)
        assert exc.value.code == 404
        assert calls == []  # a 404 is not transient; no retry, no sleep
        assert srv.hits == 1  # type: ignore[attr-defined]
    finally:
        srv.shutdown()
        thread.join()


def test_exhausting_retries_reraises_last_error() -> None:
    srv, thread, url = _server(fail_times=99, fail_status=503)
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            fetch_bytes(url, retries=3, backoff_base=0.0, sleep=lambda _: None)
        assert exc.value.code == 503
        assert srv.hits == 4  # 1 initial + 3 retries  # type: ignore[attr-defined]
    finally:
        srv.shutdown()
        thread.join()


class _UARecordingHandler(BaseHTTPRequestHandler):
    """Echo the request's User-Agent so a test can assert what we send."""

    def log_message(self, *args: object) -> None:  # silence test output
        pass

    def do_GET(self) -> None:
        ua = self.headers.get("User-Agent", "").encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(ua)))
        self.end_headers()
        self.wfile.write(ua)


def test_requests_send_descriptive_user_agent() -> None:
    # Wikimedia 403s the default urllib agent; every request must carry ours.
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _UARecordingHandler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{srv.server_address[1]}/x"
        assert fetch_bytes(url).decode() == USER_AGENT
        resp = open_stream(url)
        assert resp.read().decode() == USER_AGENT
        resp.close()
        assert "urllib" not in USER_AGENT.lower()  # not the blocked default agent
    finally:
        srv.shutdown()
        thread.join()
