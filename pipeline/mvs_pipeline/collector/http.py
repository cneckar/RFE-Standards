"""Resilient HTTP helpers for the streaming connectors.

A 10⁸ run streams hundreds of Common Crawl parquet files, tens of WAT files, and
a multi-GB Wikipedia dump over hours. Over that many requests a transient blip —
an HTTP 503 from the mirror, a dropped connection, a stalled socket — is
essentially certain, and a single unhandled one aborts the whole job. These
helpers wrap ``urllib`` opens with bounded exponential-backoff retries (honoring
``Retry-After``) and a socket timeout, so a run survives the inevitable hiccups.

Two entry points:

- :func:`fetch_bytes` retries the whole open+read for a bounded body (a parquet
  byte range, a small ``*.paths.gz`` manifest) — a mid-read drop is retried too.
- :func:`open_stream` retries only the open for a body streamed sequentially (a
  WAT or SQL-dump gzip). A mid-stream drop can't be resumed on a sequential gzip,
  but the open — where a 503 surfaces — is covered.
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import IO

#: Descriptive User-Agent. Wikimedia's User-Agent policy returns 403 to the
#: default ``Python-urllib/x.y`` agent, so every request must identify the tool
#: and a contact URL. (Common Crawl doesn't require it but accepts it.)
USER_AGENT = "mvs-rfe-collector/1.0 (+https://github.com/cneckar/RFE-Standards)"
#: HTTP statuses worth retrying: rate-limit and transient server/gateway errors.
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
#: Default attempt budget (1 initial + this many retries).
DEFAULT_RETRIES = 5
#: Backoff schedule: ``min(cap, base * 2**attempt)`` seconds between attempts.
DEFAULT_BACKOFF_BASE = 1.0
DEFAULT_BACKOFF_CAP = 30.0
#: Per-request socket timeout so a stalled connection fails (and retries) instead
#: of hanging a multi-hour run forever.
DEFAULT_TIMEOUT = 60.0


def _with_user_agent(req_or_url: urllib.request.Request | str) -> urllib.request.Request:
    """Return a ``Request`` for ``req_or_url`` carrying our ``User-Agent``.

    A bare URL becomes a ``Request``; an existing ``Request`` (e.g. a Range or
    HEAD probe) keeps its headers and only gains ours if it lacks one. Without
    this, Wikimedia rejects the default urllib agent with 403.
    """
    if isinstance(req_or_url, urllib.request.Request):
        req = req_or_url
    else:
        req = urllib.request.Request(req_or_url)
    if not req.has_header("User-agent"):
        req.add_header("User-Agent", USER_AGENT)
    return req


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in RETRYABLE_STATUS
    # URLError wraps connection resets / DNS blips; TimeoutError is a stalled read.
    return isinstance(exc, (urllib.error.URLError, TimeoutError))


def _retry_after(exc: BaseException) -> float | None:
    """Seconds requested by a ``Retry-After`` header on ``exc``, if integer-valued."""
    if isinstance(exc, urllib.error.HTTPError):
        value = exc.headers.get("Retry-After") if exc.headers else None
        if value and value.strip().isdigit():
            return float(value.strip())
    return None


def _backoff_seconds(attempt: int, base: float, cap: float) -> float:
    return min(cap, base * (2**attempt))


def _run_with_retries(
    attempt_fn: Callable[[], object],
    *,
    what: str,
    retries: int,
    backoff_base: float,
    backoff_cap: float,
    sleep: Callable[[float], None],
) -> object:
    """Call ``attempt_fn`` up to ``retries + 1`` times, backing off on retryables.

    Re-raises the last error once the budget is spent, or immediately on an error
    that is not transient (e.g. a 404 — retrying would not help).
    """
    last: BaseException | None = None
    for attempt in range(retries + 1):
        try:
            return attempt_fn()
        except Exception as exc:  # noqa: BLE001 - re-raised below if not retryable
            if not _is_retryable(exc) or attempt == retries:
                raise
            last = exc
            delay = _retry_after(exc)
            if delay is None:
                delay = _backoff_seconds(attempt, backoff_base, backoff_cap)
            sleep(delay)
    raise AssertionError(f"unreachable retry loop for {what}") from last  # pragma: no cover


def fetch_bytes(
    req_or_url: urllib.request.Request | str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
    backoff_base: float = DEFAULT_BACKOFF_BASE,
    backoff_cap: float = DEFAULT_BACKOFF_CAP,
    sleep: Callable[[float], None] = time.sleep,
) -> bytes:
    """Open ``req_or_url`` and return the full body, retrying transient failures.

    The open *and* the read are inside the retried unit, so a connection dropped
    mid-body is retried from the start — safe because the caller wants the whole
    (bounded) body anyway.
    """
    req = _with_user_agent(req_or_url)

    def attempt() -> bytes:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return resp.read()

    return _run_with_retries(  # type: ignore[return-value]
        attempt,
        what=req.full_url,
        retries=retries,
        backoff_base=backoff_base,
        backoff_cap=backoff_cap,
        sleep=sleep,
    )


def open_stream(
    req_or_url: urllib.request.Request | str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
    backoff_base: float = DEFAULT_BACKOFF_BASE,
    backoff_cap: float = DEFAULT_BACKOFF_CAP,
    sleep: Callable[[float], None] = time.sleep,
) -> IO[bytes]:
    """Open ``req_or_url`` and return the live response, retrying the open.

    The response is handed back open so the caller can stream it (wrap it in
    ``gzip.GzipFile``) or read a header (a ``HEAD`` probe's ``Content-Length``).
    Only the open is retried — a sequential gzip body can't be resumed once torn —
    but the open is where a 503 / connection-refused surfaces.
    """
    req = _with_user_agent(req_or_url)

    def attempt() -> IO[bytes]:
        return urllib.request.urlopen(req, timeout=timeout)  # noqa: S310

    return _run_with_retries(  # type: ignore[return-value]
        attempt,
        what=req.full_url,
        retries=retries,
        backoff_base=backoff_base,
        backoff_cap=backoff_cap,
        sleep=sleep,
    )
