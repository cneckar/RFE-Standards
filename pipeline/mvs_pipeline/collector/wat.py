"""Common Crawl WAT outlink extractor (T6.2).

WAT files hold JSON metadata for every WARC record in a crawl — including, for
HTML responses, the list of links the page points at. Those *outlinks* are far
more scheme-diverse than the page URLs in the columnar index: ``mailto:``,
``tel:``, ``ftp:``, ``irc:``, IPv6 hosts, and userinfo show up as link targets
that the crawl's own fetched URLs (almost all http/https) never contain. This is
the "link diversity" stratum of the corpus (see ``docs/CORPUS-PLAN.md``).

The extractor streams WARC ``metadata`` records from a ``.wat`` / ``.wat.gz``
file in bounded memory, parses each JSON payload, and yields the ``url`` of every
extracted link, optionally sampled deterministically.
"""

from __future__ import annotations

import gzip
import json
import urllib.request
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any, BinaryIO

from mvs_pipeline.collector.base import keep_sample

#: Public HTTPS mirror of the Common Crawl bucket (free, no credentials).
CC_HTTPS_HOST = "https://data.commoncrawl.org"


def resolve_wat_paths(
    manifest_text: str,
    *,
    limit: int | None = None,
    prefix: str = CC_HTTPS_HOST,
) -> list[str]:
    """Turn a crawl's ``wat.paths`` listing into fully-qualified URLs.

    Each line is a ``.warc.wat.gz`` key relative to the bucket root; join it onto
    ``prefix`` (the HTTPS mirror by default). ``limit`` caps how many to read.
    """
    keys = [line.strip() for line in manifest_text.splitlines() if line.strip()]
    keys = [k for k in keys if k.endswith(".wat.gz")]
    if limit is not None:
        keys = keys[:limit]
    return [f"{prefix}/{k}" for k in keys]


def _iter_warc_records(stream: BinaryIO) -> Iterator[tuple[dict[str, str], bytes]]:
    """Yield ``(headers, body)`` for each WARC record in ``stream``.

    Uses ``Content-Length`` to delimit bodies (not a line heuristic), so JSON
    payloads that happen to contain ``WARC/`` are handled correctly.
    """
    while True:
        line = stream.readline()
        if not line:
            return
        if not line.strip().startswith(b"WARC/"):
            continue  # skip inter-record whitespace / stray bytes
        headers: dict[str, str] = {}
        while True:
            hline = stream.readline()
            if not hline or hline in (b"\r\n", b"\n"):
                break
            key, sep, value = hline.partition(b":")
            if sep:
                headers[key.strip().decode("latin-1").lower()] = value.strip().decode("latin-1")
        length = int(headers.get("content-length", "0"))
        body = stream.read(length)
        yield headers, body


def _links_from_wat_payload(payload: dict[str, Any]) -> Iterator[str]:
    """Yield link URLs from a single WAT metadata JSON payload."""
    response = (
        payload.get("Envelope", {}).get("Payload-Metadata", {}).get("HTTP-Response-Metadata", {})
    )
    html = response.get("HTML-Metadata", {})
    for link in html.get("Links", []) or []:
        url = link.get("url") if isinstance(link, dict) else None
        if url:
            yield url


def iter_links_from_wat(stream: BinaryIO) -> Iterator[str]:
    """Yield every extracted link URL from a WAT byte stream.

    Non-``metadata`` records and payloads that don't parse as JSON are skipped,
    so a truncated or mixed stream degrades gracefully rather than raising.
    """
    for headers, body in _iter_warc_records(stream):
        if headers.get("warc-type") != "metadata":
            continue
        try:
            payload = json.loads(body)
        except ValueError:
            continue
        if isinstance(payload, dict):
            yield from _links_from_wat_payload(payload)


def _open_stream(path: str | Path) -> BinaryIO:
    """Open ``path`` for sequential binary reading, gunzipping ``.gz``.

    Local paths and ``http(s)://`` URLs are both supported; WAT is read as a
    sequential WARC stream, so the HTTPS case just streams the gzip body (no
    range requests, no credentials).
    """
    text = str(path)
    if text.startswith(("http://", "https://")):
        resp = urllib.request.urlopen(text)  # noqa: S310 (trusted CC mirror)
        return gzip.GzipFile(fileobj=resp) if text.endswith(".gz") else resp
    p = Path(path)
    if p.suffix == ".gz":
        return gzip.open(p, "rb")
    return p.open("rb")


class CommonCrawlWat:
    """Stream scheme-diverse outlink URIs from Common Crawl WAT files.

    Parameters
    ----------
    paths:
        ``.wat`` or ``.wat.gz`` files to read, in order.
    crawl_id:
        The crawl these files belong to, recorded in provenance.
    sample_rate:
        Fraction in ``[0, 1]`` of links to keep, sampled deterministically by
        ``seed``. ``1.0`` keeps everything.
    seed:
        Sampling seed; the same seed reproduces the same subset.
    """

    name = "commoncrawl-outlinks"

    def __init__(
        self,
        paths: Sequence[str | Path],
        *,
        crawl_id: str | None = None,
        sample_rate: float = 1.0,
        seed: int = 0,
    ) -> None:
        self.paths = list(paths)
        self.crawl_id = crawl_id
        self.sample_rate = sample_rate
        self.seed = seed
        self._files_read: list[str] = []
        self._urls_read = 0

    def iter_uris(self) -> Iterator[str]:
        """Yield outlink URLs from each WAT file, streamed and (optionally) sampled."""
        for path in self.paths:
            with _open_stream(path) as stream:
                for url in iter_links_from_wat(stream):
                    if keep_sample(url, self.sample_rate, self.seed):
                        self._urls_read += 1
                        yield url
            self._files_read.append(str(path))

    def provenance(self) -> dict[str, Any]:
        """Record what was read: crawl id, files, sampling, URL count."""
        return {
            "source": self.name,
            "crawl_id": self.crawl_id,
            "sample_rate": self.sample_rate,
            "seed": self.seed,
            "files_read": list(self._files_read),
            "urls_read": self._urls_read,
        }

    @classmethod
    def from_crawl(
        cls,
        crawl_id: str,
        *,
        limit: int | None = None,
        sample_rate: float = 1.0,
        seed: int = 0,
        host: str = CC_HTTPS_HOST,
    ) -> CommonCrawlWat:
        """Build a connector for a crawl's WAT files, streamed over HTTPS.

        Fetches ``crawl-data/<crawl_id>/wat.paths.gz`` — the manifest of WAT file
        keys — over the free public mirror and points the connector at them.
        ``limit`` caps how many WAT files to read (each is large). Network-backed;
        path resolution is unit-tested via :func:`resolve_wat_paths`.
        """
        manifest_url = f"{host}/crawl-data/{crawl_id}/wat.paths.gz"
        with urllib.request.urlopen(manifest_url) as resp:  # noqa: S310 (trusted host)
            manifest_text = gzip.decompress(resp.read()).decode()
        paths = resolve_wat_paths(manifest_text, limit=limit, prefix=host)
        return cls(paths, crawl_id=crawl_id, sample_rate=sample_rate, seed=seed)
