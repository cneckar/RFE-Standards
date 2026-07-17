"""Wikipedia ``externallinks`` SQL-dump connector (T6.3).

Wikipedia publishes an ``externallinks`` table dump
(``<wiki>-<date>-externallinks.sql.gz``) listing every external URL cited from
an article. These are human-curated and extremely scheme-diverse — a strong,
free diversity supplement to the crawl-derived strata (see
``docs/CORPUS-PLAN.md``).

The dump is a mysqldump: ``INSERT INTO `externallinks` VALUES (...),(...);``
statements, one long line each. This connector streams those lines, parses each
tuple respecting SQL quoting/escaping in bounded memory, and yields the URL
field. The classic schema is ``(el_id, el_from, el_to, el_index, el_index_60)``
— the full URL is ``el_to`` at column index 2; ``el_index`` holds a
reversed-host sort key that *also* looks URL-shaped, so this connector selects
the URL positionally rather than by shape. ``url_column`` overrides the index
for other layouts.
"""

from __future__ import annotations

import gzip
import io
import re
import urllib.request
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

from mvs_pipeline.collector.base import keep_sample

#: Wikimedia dumps host (free HTTPS, CC BY-SA / GFDL).
WIKIMEDIA_HOST = "https://dumps.wikimedia.org"
#: Column index of ``el_to`` (the full URL) in the classic externallinks schema.
DEFAULT_URL_COLUMN = 2
#: A field is treated as a URL only if it carries a scheme (``scheme:``).
_URL_SHAPE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")
#: Extract a dump date (``YYYYMMDD``) from a filename like ``enwiki-20240101-...``.
_DUMP_DATE = re.compile(r"-(\d{8})-")


def _iter_insert_tuples(line: str, table: str) -> Iterator[list[str | None]]:
    """Yield each ``VALUES`` tuple from one ``INSERT INTO `table``` line.

    Parses SQL tuples directly (quoted strings with ``\\`` escapes, ``NULL``,
    bare numeric/identifier tokens) so embedded commas/parens inside string
    literals don't split a field. Lines for other tables are ignored.
    """
    prefix = f"INSERT INTO `{table}` VALUES "
    if not line.startswith(prefix):
        return
    i = prefix.__len__()
    n = len(line)
    while i < n:
        if line[i] != "(":
            i += 1
            continue
        i += 1  # consume '('
        fields: list[str | None] = []
        while i < n and line[i] != ")":
            if line[i] == "'":
                # Quoted string: consume until the matching unescaped quote.
                i += 1
                buf: list[str] = []
                while i < n:
                    ch = line[i]
                    if ch == "\\" and i + 1 < n:
                        nxt = line[i + 1]
                        buf.append({"n": "\n", "t": "\t", "r": "\r"}.get(nxt, nxt))
                        i += 2
                        continue
                    if ch == "'":
                        i += 1
                        break
                    buf.append(ch)
                    i += 1
                fields.append("".join(buf))
            else:
                # Bare token: NULL / number / identifier up to ',' or ')'.
                start = i
                while i < n and line[i] not in ",)":
                    i += 1
                token = line[start:i].strip()
                fields.append(None if token.upper() == "NULL" else token)
            if i < n and line[i] == ",":
                i += 1  # field separator
        if i < n and line[i] == ")":
            i += 1  # consume ')'
        yield fields
        # Skip the ',' or ';' between/after tuples.
        while i < n and line[i] in ", ;":
            i += 1


def iter_external_urls(
    lines: Iterator[str],
    *,
    table: str = "externallinks",
    url_column: int = DEFAULT_URL_COLUMN,
) -> Iterator[str]:
    """Yield the URL field of every ``externallinks`` tuple across ``lines``.

    A selected field is emitted only if it carries a scheme, which guards
    against a mis-set ``url_column`` silently yielding integers or NULLs.
    """
    for line in lines:
        for fields in _iter_insert_tuples(line, table):
            if url_column < len(fields):
                value = fields[url_column]
                if value and _URL_SHAPE.match(value):
                    yield value


def _dump_date_from_name(name: str) -> str | None:
    match = _DUMP_DATE.search(name)
    return match.group(1) if match else None


def _iter_lines(path: str | Path) -> Iterator[str]:
    """Iterate decoded lines of a ``.sql`` / ``.sql.gz`` dump (local or HTTPS).

    HTTPS dumps stream sequentially (gunzipped on the fly) — the file is read
    front to back, so no range requests or credentials are needed.
    """
    text = str(path)
    if text.startswith(("http://", "https://")):
        resp = urllib.request.urlopen(text)  # noqa: S310 (trusted dumps host)
        raw = gzip.GzipFile(fileobj=resp) if text.endswith(".gz") else resp
        yield from io.TextIOWrapper(raw, encoding="utf-8", errors="replace")
        return
    p = Path(path)
    opener = gzip.open if p.suffix == ".gz" else open
    with opener(p, "rt", encoding="utf-8", errors="replace") as fh:
        yield from fh


class WikipediaExternalLinks:
    """Stream external URLs from a Wikipedia ``externallinks`` SQL dump.

    Parameters
    ----------
    paths:
        ``.sql`` or ``.sql.gz`` dump files to read, in order.
    url_column:
        Column index of the URL field (default matches the classic schema).
    sample_rate:
        Fraction in ``[0, 1]`` of URLs to keep, sampled deterministically by
        ``seed``. ``1.0`` keeps everything.
    seed:
        Sampling seed; the same seed reproduces the same subset.
    """

    name = "wikipedia-externallinks"

    def __init__(
        self,
        paths: Sequence[str | Path],
        *,
        url_column: int = DEFAULT_URL_COLUMN,
        sample_rate: float = 1.0,
        seed: int = 0,
    ) -> None:
        # Keep raw so http(s) URLs survive (Path would mangle "https://").
        self.paths = [str(p) for p in paths]
        self.url_column = url_column
        self.sample_rate = sample_rate
        self.seed = seed
        self._files_read: list[str] = []
        self._dump_dates: list[str] = []
        self._urls_read = 0

    def iter_uris(self) -> Iterator[str]:
        """Yield external URLs from each dump, streamed and (optionally) sampled."""
        for path in self.paths:
            for url in iter_external_urls(_iter_lines(path), url_column=self.url_column):
                if keep_sample(url, self.sample_rate, self.seed):
                    self._urls_read += 1
                    yield url
            self._files_read.append(path)
            date = _dump_date_from_name(path)
            if date is not None:
                self._dump_dates.append(date)

    def provenance(self) -> dict[str, Any]:
        """Record what was read: dump dates, files, sampling, URL count."""
        return {
            "source": self.name,
            "dump_dates": list(self._dump_dates),
            "sample_rate": self.sample_rate,
            "seed": self.seed,
            "files_read": list(self._files_read),
            "urls_read": self._urls_read,
        }

    @classmethod
    def from_dump(
        cls,
        wiki: str = "enwiki",
        *,
        date: str = "latest",
        host: str = WIKIMEDIA_HOST,
        sample_rate: float = 1.0,
        seed: int = 0,
        url_column: int = DEFAULT_URL_COLUMN,
    ) -> WikipediaExternalLinks:
        """Build a connector for a wiki's externallinks dump, streamed over HTTPS.

        Points at ``<host>/<wiki>/<date>/<wiki>-<date>-externallinks.sql.gz`` —
        e.g. ``enwiki`` / ``latest``. The dump is a single large gzip stream read
        sequentially; no download step. Network-backed; the URL shape is
        unit-tested via :func:`dump_url`.
        """
        return cls(
            [dump_url(wiki, date, host=host)],
            sample_rate=sample_rate,
            seed=seed,
            url_column=url_column,
        )


def dump_url(wiki: str, date: str = "latest", *, host: str = WIKIMEDIA_HOST) -> str:
    """URL of a wiki's ``externallinks`` SQL dump for ``date`` (``latest`` ok)."""
    return f"{host}/{wiki}/{date}/{wiki}-{date}-externallinks.sql.gz"
