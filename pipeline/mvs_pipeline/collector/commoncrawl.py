"""Common Crawl columnar URL-index connector (T6.1).

Each monthly Common Crawl publishes a columnar (Parquet) URL index — the
``cc-index`` table — alongside the WARCs. Reading just the ``url`` column with
column/predicate pushdown yields billions of crawled page URLs while
downloading tens of GB instead of the ~300 GB full index. This is the bulk,
representative core of the corpus (see ``docs/CORPUS-PLAN.md``).

The connector streams the ``url`` column from one or more Parquet subset files
in bounded memory (row-group batches), optionally sampling deterministically. It
works against local files (tests, cached shards) and ``s3://commoncrawl/...``
anonymous access (production).
"""

from __future__ import annotations

import gzip
import urllib.request
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
from pyarrow import fs

from mvs_pipeline.collector.base import keep_sample
from mvs_pipeline.collector.http import fetch_bytes, open_stream

#: Public HTTPS mirror of the Common Crawl S3 bucket (free, no credentials).
CC_HTTPS_HOST = "https://data.commoncrawl.org"
#: Anonymous S3 bucket the cc-index parquet files live in.
CC_S3_BUCKET = "s3://commoncrawl"
#: The cc-index table has three subsets; only ``warc`` is the page-URL corpus.
#: ``crawldiagnostics`` and ``robotstxt`` are crawler bookkeeping, not usage.
DEFAULT_SUBSET = "warc"
#: Default transport for a whole-crawl read. HTTPS needs no AWS credentials at
#: all, so it works even where anonymous S3 is blocked (signed by stray creds).
DEFAULT_TRANSPORT = "https"
#: The single column we read from the multi-column cc-index table.
URL_COLUMN = "url"
#: Row-group batch size for streaming reads (bounds peak memory).
_BATCH_ROWS = 65_536


def resolve_index_paths(
    manifest_text: str,
    *,
    subset: str | None = DEFAULT_SUBSET,
    limit: int | None = None,
    prefix: str = CC_S3_BUCKET,
) -> list[str]:
    """Turn a ``cc-index-table.paths`` listing into fully-qualified paths.

    The manifest lists relative parquet keys across three subsets
    (``crawldiagnostics``, ``robotstxt``, ``warc``); ``subset`` keeps only one
    (``warc`` — the page-URL corpus — by default; ``None`` keeps all). Each key
    is joined onto ``prefix`` — the anonymous S3 bucket (``s3://commoncrawl``)
    or the HTTPS mirror (``https://data.commoncrawl.org``). ``limit`` caps how
    many files to read.
    """
    paths = [line.strip() for line in manifest_text.splitlines() if line.strip()]
    paths = [p for p in paths if p.endswith(".parquet")]
    if subset is not None:
        needle = f"/subset={subset}/"
        paths = [p for p in paths if needle in p]
    if limit is not None:
        paths = paths[:limit]
    return [f"{prefix}/{p}" for p in paths]


class _HttpRangeFile:
    """A minimal seekable, read-only file over HTTP(S) range requests.

    pyarrow reads a parquet footer then specific row groups, so it needs random
    access, not a full download. This satisfies pyarrow's file interface with
    ``Range`` requests, letting us stream Common Crawl parquet over the public
    HTTPS mirror with **no AWS credentials** — the transport that works where
    anonymous S3 is blocked by stray credentials in the environment.
    """

    def __init__(self, url: str) -> None:
        self._url = url
        self._pos = 0
        self._size = self._head_size(url)

    @staticmethod
    def _head_size(url: str) -> int:
        req = urllib.request.Request(url, method="HEAD")
        resp = open_stream(req)  # retried; the mirror 503s under load
        try:
            length = resp.headers.get("Content-Length")
        finally:
            resp.close()
        if length is None:
            raise OSError(f"no Content-Length for {url}; server must support HEAD")
        return int(length)

    # -- pyarrow file protocol --------------------------------------------
    def seekable(self) -> bool:
        return True

    def readable(self) -> bool:
        return True

    def writable(self) -> bool:
        return False

    @property
    def closed(self) -> bool:
        return False

    def tell(self) -> int:
        return self._pos

    def seek(self, offset: int, whence: int = 0) -> int:
        if whence == 0:
            self._pos = offset
        elif whence == 1:
            self._pos += offset
        elif whence == 2:
            self._pos = self._size + offset
        else:
            raise ValueError(f"invalid whence {whence}")
        return self._pos

    def read(self, nbytes: int | None = None) -> bytes:
        if nbytes is None or nbytes < 0:
            end = self._size
        else:
            end = min(self._pos + nbytes, self._size)
        if end <= self._pos:
            return b""
        req = urllib.request.Request(self._url, headers={"Range": f"bytes={self._pos}-{end - 1}"})
        # A bounded range body: retry the whole open+read so a mid-range 503 or
        # dropped connection during a multi-hour run doesn't abort the parse.
        data = fetch_bytes(req)
        self._pos += len(data)
        return data

    def flush(self) -> None:  # pragma: no cover - no-op for a read-only file
        pass

    def close(self) -> None:  # pragma: no cover - nothing to release
        pass


def _open_parquet(path: str) -> pq.ParquetFile:
    """Open ``path`` as a ParquetFile over the right transport.

    ``http(s)://`` streams via range requests (no credentials); ``s3://`` uses
    anonymous access against the public ``commoncrawl`` bucket; anything else is
    a local file.
    """
    if path.startswith(("http://", "https://")):
        return pq.ParquetFile(_HttpRangeFile(path))
    if path.startswith("s3://"):
        s3 = fs.S3FileSystem(anonymous=True, region="us-east-1")
        return pq.ParquetFile(path[len("s3://") :], filesystem=s3)
    return pq.ParquetFile(str(Path(path)), filesystem=fs.LocalFileSystem())


class CommonCrawlUrlIndex:
    """Stream URLs from Common Crawl ``cc-index`` Parquet subset files.

    Parameters
    ----------
    paths:
        Parquet subset files to read, in order. Local paths or ``s3://`` URIs.
    crawl_id:
        The crawl these files belong to (e.g. ``"CC-MAIN-2024-10"``), recorded
        in provenance. Optional for ad-hoc/local reads.
    sample_rate:
        Fraction in ``[0, 1]`` of URLs to keep, sampled deterministically by
        ``seed`` so shards are reproducible. ``1.0`` keeps everything.
    seed:
        Sampling seed; the same seed reproduces the same subset.
    """

    name = "commoncrawl-index"

    def __init__(
        self,
        paths: Sequence[str],
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
        """Yield URLs from each subset file, streaming and (optionally) sampled.

        Reads only the ``url`` column in row-group batches, so peak memory is a
        single batch regardless of file size. Progress is tracked in
        ``provenance`` (files read, URLs yielded) as a side effect.
        """
        for path in self.paths:
            parquet = _open_parquet(path)
            for batch in parquet.iter_batches(batch_size=_BATCH_ROWS, columns=[URL_COLUMN]):
                for url in batch.column(0).to_pylist():
                    if url is None:
                        continue
                    if keep_sample(url, self.sample_rate, self.seed):
                        self._urls_read += 1
                        yield url
            self._files_read.append(path)

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
        subset: str | None = DEFAULT_SUBSET,
        limit: int | None = None,
        sample_rate: float = 1.0,
        seed: int = 0,
        transport: str = DEFAULT_TRANSPORT,
        host: str = CC_HTTPS_HOST,
    ) -> CommonCrawlUrlIndex:
        """Build a connector for a whole crawl and point it at its parquet files.

        Fetches ``crawl-data/<crawl_id>/cc-index-table.paths.gz`` — the small
        manifest of Parquet keys — over the free public HTTPS mirror, then points
        the connector at the parquet files. ``transport`` picks how they are read:

        - ``"https"`` (default) streams from the ``data.commoncrawl.org`` mirror
          with range requests — **no AWS credentials**, so it works even where
          anonymous S3 is denied because stray credentials sign the request;
        - ``"s3"`` streams from ``s3://commoncrawl`` anonymously (good on EC2 /
          where anonymous S3 is unencumbered).

        ``subset`` selects the table subset (default ``warc``, the page-URL
        corpus); ``limit`` caps how many files to read. Network-backed; path
        resolution is unit-tested via :func:`resolve_index_paths`.
        """
        if transport == "https":
            prefix = host
        elif transport == "s3":
            prefix = CC_S3_BUCKET
        else:
            raise ValueError(f"transport must be 'https' or 's3', got {transport!r}")

        manifest_url = f"{host}/crawl-data/{crawl_id}/cc-index-table.paths.gz"
        manifest_text = gzip.decompress(fetch_bytes(manifest_url)).decode()
        paths = resolve_index_paths(manifest_text, subset=subset, limit=limit, prefix=prefix)
        return cls(paths, crawl_id=crawl_id, sample_rate=sample_rate, seed=seed)
