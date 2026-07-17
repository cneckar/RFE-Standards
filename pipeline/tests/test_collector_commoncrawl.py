"""Tests for the Common Crawl URL-index connector and sampling helpers (T6.1)."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from mvs_pipeline.collector import (
    CommonCrawlUrlIndex,
    Source,
    keep_sample,
    resolve_index_paths,
    stable_fraction,
)

# A miniature cc-index-table.paths listing, one line per parquet key, across the
# three real subsets. from_crawl() resolves exactly this into s3:// paths.
_MANIFEST = "\n".join(
    [
        "cc-index/table/cc-main/warc/crawl=CC-MAIN-2024-10/subset=crawldiagnostics/part-00000.parquet",
        "cc-index/table/cc-main/warc/crawl=CC-MAIN-2024-10/subset=robotstxt/part-00000.parquet",
        "cc-index/table/cc-main/warc/crawl=CC-MAIN-2024-10/subset=warc/part-00000.parquet",
        "cc-index/table/cc-main/warc/crawl=CC-MAIN-2024-10/subset=warc/part-00001.parquet",
        "",  # blank line tolerated
    ]
)


def test_resolve_index_paths_keeps_only_warc_subset() -> None:
    paths = resolve_index_paths(_MANIFEST)
    assert paths == [
        "s3://commoncrawl/cc-index/table/cc-main/warc/crawl=CC-MAIN-2024-10/subset=warc/part-00000.parquet",
        "s3://commoncrawl/cc-index/table/cc-main/warc/crawl=CC-MAIN-2024-10/subset=warc/part-00001.parquet",
    ]


def test_resolve_index_paths_limit_and_subset() -> None:
    assert len(resolve_index_paths(_MANIFEST, limit=1)) == 1
    # A different subset selects other files; None keeps all parquet paths.
    assert len(resolve_index_paths(_MANIFEST, subset="robotstxt")) == 1
    assert len(resolve_index_paths(_MANIFEST, subset=None)) == 4


def test_resolve_index_paths_https_prefix() -> None:
    # transport="https" resolves against the mirror instead of the S3 bucket.
    paths = resolve_index_paths(_MANIFEST, limit=1, prefix="https://data.commoncrawl.org")
    assert paths == [
        "https://data.commoncrawl.org/cc-index/table/cc-main/warc/"
        "crawl=CC-MAIN-2024-10/subset=warc/part-00000.parquet"
    ]


def _write_cc_index(path: Path, urls: list[str]) -> None:
    """Write a tiny multi-column cc-index-shaped Parquet fixture."""
    table = pa.table(
        {
            # The real table has many columns; the connector must read only `url`.
            "url_surtkey": [u[::-1] for u in urls],
            "url": urls,
            "fetch_status": [200] * len(urls),
        }
    )
    pq.write_table(table, path, row_group_size=4)


@pytest.fixture
def cc_index(tmp_path: Path) -> tuple[Path, list[str]]:
    urls = [f"https://example{i}.test/path/{i}" for i in range(20)]
    path = tmp_path / "part-0.parquet"
    _write_cc_index(path, urls)
    return path, urls


def test_reads_only_url_column_in_order(cc_index: tuple[Path, list[str]]) -> None:
    path, urls = cc_index
    src = CommonCrawlUrlIndex([str(path)])
    assert list(src.iter_uris()) == urls


def test_connector_is_a_source(cc_index: tuple[Path, list[str]]) -> None:
    path, _ = cc_index
    assert isinstance(CommonCrawlUrlIndex([str(path)]), Source)


def test_provenance_records_files_and_counts(cc_index: tuple[Path, list[str]]) -> None:
    path, urls = cc_index
    src = CommonCrawlUrlIndex([str(path)], crawl_id="CC-MAIN-2024-10")
    list(src.iter_uris())
    prov = src.provenance()
    assert prov["source"] == "commoncrawl-index"
    assert prov["crawl_id"] == "CC-MAIN-2024-10"
    assert prov["files_read"] == [str(path)]
    assert prov["urls_read"] == len(urls)


def test_multiple_files_concatenate(tmp_path: Path) -> None:
    a, b = tmp_path / "a.parquet", tmp_path / "b.parquet"
    _write_cc_index(a, ["https://a.test/1", "https://a.test/2"])
    _write_cc_index(b, ["https://b.test/1"])
    src = CommonCrawlUrlIndex([str(a), str(b)])
    assert list(src.iter_uris()) == ["https://a.test/1", "https://a.test/2", "https://b.test/1"]
    assert src.provenance()["files_read"] == [str(a), str(b)]


class _RangeHandler(BaseHTTPRequestHandler):
    """Serve a single in-memory file with HEAD + Range support (like the CC mirror)."""

    def log_message(self, *args: object) -> None:  # silence test output
        pass

    def do_HEAD(self) -> None:
        data = self.server.data  # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()

    def do_GET(self) -> None:
        data: bytes = self.server.data  # type: ignore[attr-defined]
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes="):
            start_s, _, end_s = rng[len("bytes=") :].partition("-")
            start = int(start_s)
            end = int(end_s) if end_s else len(data) - 1
            chunk = data[start : end + 1]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
            self.send_header("Content-Length", str(len(chunk)))
            self.end_headers()
            self.wfile.write(chunk)
        else:
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)


def test_reads_parquet_over_http_range_requests(tmp_path: Path) -> None:
    # Serve a real parquet fixture over a local HTTP server that honors Range,
    # exactly as the Common Crawl mirror does, and read it via the https path.
    urls = [f"https://example{i}.test/p/{i}" for i in range(30)]
    parquet = tmp_path / "part.parquet"
    _write_cc_index(parquet, urls)

    server = ThreadingHTTPServer(("127.0.0.1", 0), _RangeHandler)
    server.data = parquet.read_bytes()  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/part.parquet"
        src = CommonCrawlUrlIndex([url], crawl_id="CC-MAIN-2024-10")
        assert list(src.iter_uris()) == urls
        assert src.provenance()["urls_read"] == len(urls)
    finally:
        server.shutdown()
        thread.join()


def test_sampling_is_deterministic_and_bounded(cc_index: tuple[Path, list[str]]) -> None:
    path, urls = cc_index
    first = list(CommonCrawlUrlIndex([str(path)], sample_rate=0.5, seed=7).iter_uris())
    second = list(CommonCrawlUrlIndex([str(path)], sample_rate=0.5, seed=7).iter_uris())
    assert first == second  # reproducible
    assert set(first).issubset(set(urls))  # a real subset
    assert first  # non-empty for this fixture/seed
    assert len(first) < len(urls)  # actually dropped some


def test_sampling_seed_changes_subset(cc_index: tuple[Path, list[str]]) -> None:
    path, _ = cc_index
    a = list(CommonCrawlUrlIndex([str(path)], sample_rate=0.5, seed=1).iter_uris())
    b = list(CommonCrawlUrlIndex([str(path)], sample_rate=0.5, seed=2).iter_uris())
    assert a != b


def test_sample_rate_one_keeps_all(cc_index: tuple[Path, list[str]]) -> None:
    path, urls = cc_index
    assert list(CommonCrawlUrlIndex([str(path)], sample_rate=1.0).iter_uris()) == urls


def test_sample_rate_zero_keeps_none(cc_index: tuple[Path, list[str]]) -> None:
    path, _ = cc_index
    assert list(CommonCrawlUrlIndex([str(path)], sample_rate=0.0).iter_uris()) == []


def test_stable_fraction_in_unit_interval() -> None:
    for i in range(100):
        f = stable_fraction(f"https://x.test/{i}", seed=0)
        assert 0.0 <= f < 1.0


def test_stable_fraction_depends_on_seed() -> None:
    assert stable_fraction("https://x.test/a", 1) != stable_fraction("https://x.test/a", 2)


def test_keep_sample_edges() -> None:
    assert keep_sample("anything", 1.0, 0) is True
    assert keep_sample("anything", 0.0, 0) is False


def test_keep_sample_fraction_is_roughly_rate() -> None:
    kept = sum(keep_sample(f"https://x.test/{i}", 0.3, seed=0) for i in range(10_000))
    assert 0.27 < kept / 10_000 < 0.33
