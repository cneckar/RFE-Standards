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
#: Anonymous S3 bucket the cc-index parquet files live in.
CC_S3_BUCKET = "s3://commoncrawl"
#: The cc-index table has three subsets; only ``warc`` is the page-URL corpus.
#: ``crawldiagnostics`` and ``robotstxt`` are crawler bookkeeping, not usage.
DEFAULT_SUBSET = "warc"
#: The single column we read from the multi-column cc-index table.
URL_COLUMN = "url"
#: Row-group batch size for streaming reads (bounds peak memory).
_BATCH_ROWS = 65_536


def resolve_index_paths(
    manifest_text: str,
    *,
    subset: str | None = DEFAULT_SUBSET,
    limit: int | None = None,
    bucket: str = CC_S3_BUCKET,
) -> list[str]:
    """Turn a ``cc-index-table.paths`` listing into anonymous ``s3://`` paths.

    The manifest lists relative parquet keys across three subsets
    (``crawldiagnostics``, ``robotstxt``, ``warc``); ``subset`` keeps only one
    (``warc`` — the page-URL corpus — by default; ``None`` keeps all). Paths are
    prefixed with the anonymous S3 bucket, since that is what the reader streams
    (the HTTPS mirror is only used to fetch this small manifest). ``limit`` caps
    how many files to read.
    """
    paths = [line.strip() for line in manifest_text.splitlines() if line.strip()]
    paths = [p for p in paths if p.endswith(".parquet")]
    if subset is not None:
        needle = f"/subset={subset}/"
        paths = [p for p in paths if needle in p]
    if limit is not None:
        paths = paths[:limit]
    return [f"{bucket}/{p}" for p in paths]


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
        subset: str | None = DEFAULT_SUBSET,
        limit: int | None = None,
        sample_rate: float = 1.0,
        seed: int = 0,
        host: str = CC_HTTPS_HOST,
    ) -> CommonCrawlUrlIndex:
        """Build a connector for a whole crawl, streamed from anonymous S3.

        Fetches ``crawl-data/<crawl_id>/cc-index-table.paths.gz`` — the small
        manifest of Parquet keys — over the free public HTTPS mirror, then points
        the connector at the ``s3://commoncrawl/...`` parquet files (which is what
        the reader streams). ``subset`` selects the table subset (default
        ``warc``, the page-URL corpus); ``limit`` caps how many files to read.
        Network-backed; the path resolution is unit-tested via
        :func:`resolve_index_paths`.
        """
        import urllib.request

        manifest_url = f"{host}/crawl-data/{crawl_id}/cc-index-table.paths.gz"
        with urllib.request.urlopen(manifest_url) as resp:  # noqa: S310 (trusted host)
            manifest_text = gzip.decompress(resp.read()).decode()
        paths = resolve_index_paths(manifest_text, subset=subset, limit=limit)
        return cls(paths, crawl_id=crawl_id, sample_rate=sample_rate, seed=seed)
