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
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
from pyarrow import fs

from mvs_pipeline.collector.base import keep_sample

#: Public HTTPS mirror of the Common Crawl S3 bucket (free, no credentials).
CC_HTTPS_HOST = "https://data.commoncrawl.org"
#: The single column we read from the multi-column cc-index table.
URL_COLUMN = "url"
#: Row-group batch size for streaming reads (bounds peak memory).
_BATCH_ROWS = 65_536


def _open_source(path: str) -> tuple[Any, str]:
    """Resolve ``path`` to a ``(filesystem, path)`` pair pyarrow can open.

    ``s3://`` paths use anonymous access against the public ``commoncrawl``
    bucket; everything else is treated as a local file.
    """
    if path.startswith("s3://"):
        s3 = fs.S3FileSystem(anonymous=True, region="us-east-1")
        return s3, path[len("s3://") :]
    return fs.LocalFileSystem(), str(Path(path))


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
            filesystem, resolved = _open_source(path)
            parquet = pq.ParquetFile(resolved, filesystem=filesystem)
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
        limit: int | None = None,
        sample_rate: float = 1.0,
        seed: int = 0,
        host: str = CC_HTTPS_HOST,
    ) -> CommonCrawlUrlIndex:
        """Build a connector for a whole crawl by resolving its subset file list.

        Fetches ``crawl-data/<crawl_id>/cc-index-table.paths.gz`` — the manifest
        of Parquet subset paths — and points the connector at them over the free
        public HTTPS mirror. ``limit`` caps how many subset files to read (handy
        for a partial run). Network-backed; exercised in integration, not unit
        tests.
        """
        import urllib.request

        manifest_url = f"{host}/crawl-data/{crawl_id}/cc-index-table.paths.gz"
        with urllib.request.urlopen(manifest_url) as resp:  # noqa: S310 (trusted host)
            raw = gzip.decompress(resp.read()).decode()
        subset_paths = [line.strip() for line in raw.splitlines() if line.strip()]
        # Keep only the columnar table subsets (defensive against manifest drift).
        subset_paths = [p for p in subset_paths if p.endswith(".parquet")]
        if limit is not None:
            subset_paths = subset_paths[:limit]
        urls = [f"{host}/{p}" for p in subset_paths]
        return cls(urls, crawl_id=crawl_id, sample_rate=sample_rate, seed=seed)
