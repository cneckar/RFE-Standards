"""URI corpus preparation (Task 2.3).

The Python side of the URI corpus ingestor: turn Common Crawl-style inputs into
a clean, newline-delimited corpus file that the native ``mvs-telemetry`` binary
consumes to produce ``hits.json``. Per ADR 0001 the language boundary is the
corpus file — Python prepares it, Rust parses it.

Supported input formats:

- ``"list"``  — one URL per line (``#`` comments and blank lines ignored).
- ``"warc"``  — WARC/WAT headers; the ``WARC-Target-URI`` of each record is taken.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from pathlib import Path

_WARC_TARGET = re.compile(r"^WARC-Target-URI:\s*(\S+)\s*$", re.IGNORECASE | re.MULTILINE)


def iter_uris_from_list(text: str) -> Iterator[str]:
    """Yield URIs from a plain list, skipping blank lines and ``#`` comments."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            yield stripped


def iter_uris_from_warc(text: str) -> Iterator[str]:
    """Yield the ``WARC-Target-URI`` of each record in a WARC/WAT text stream."""
    for match in _WARC_TARGET.finditer(text):
        yield match.group(1).strip()


def iter_uris(text: str, fmt: str = "list") -> Iterator[str]:
    """Extract URIs from ``text`` according to ``fmt`` (``"list"`` or ``"warc"``)."""
    if fmt == "list":
        yield from iter_uris_from_list(text)
    elif fmt == "warc":
        yield from iter_uris_from_warc(text)
    else:
        raise ValueError(f"unknown corpus format {fmt!r}; expected 'list' or 'warc'")


def dedupe(uris: Iterable[str]) -> Iterator[str]:
    """Yield URIs with later duplicates removed, preserving first-seen order."""
    seen: set[str] = set()
    for uri in uris:
        if uri not in seen:
            seen.add(uri)
            yield uri


def write_corpus(uris: Iterable[str], path: str | Path, *, deduplicate: bool = True) -> int:
    """Write a newline-delimited corpus file; return the number of URIs written."""
    items = list(dedupe(uris) if deduplicate else uris)
    text = "\n".join(items)
    if items:
        text += "\n"
    Path(path).write_text(text)
    return len(items)


def telemetry_argv(
    binary: str | Path,
    ast_path: str | Path,
    corpus_path: str | Path,
    out_path: str | Path,
) -> list[str]:
    """Build the argv to run the native ``mvs-telemetry`` binary over a corpus.

    Kept as data (not executed here) so the Python front-end stays free of a
    Rust build dependency; callers run it where the binary is available.
    """
    return [
        str(binary),
        "--ast",
        str(ast_path),
        "--corpus",
        str(corpus_path),
        "--out",
        str(out_path),
    ]
