"""Tests for the Common Crawl WAT outlink extractor (T6.2)."""

from __future__ import annotations

import gzip
import io
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from mvs_pipeline.collector import Source, resolve_wat_paths
from mvs_pipeline.collector.wat import (
    CommonCrawlWat,
    iter_links_from_wat,
)


def _warc_record(warc_type: str, body: bytes) -> bytes:
    """Assemble a minimal WARC record with a Content-Length-delimited body."""
    headers = (
        f"WARC/1.0\r\nWARC-Type: {warc_type}\r\nContent-Length: {len(body)}\r\n\r\n"
    ).encode()
    return headers + body + b"\r\n\r\n"


def _wat_metadata(target: str, links: list[str]) -> bytes:
    payload = {
        "Envelope": {
            "Payload-Metadata": {
                "HTTP-Response-Metadata": {
                    "HTML-Metadata": {"Links": [{"url": u, "path": "A@/href"} for u in links]}
                }
            }
        }
    }
    return _warc_record("metadata", json.dumps(payload).encode())


def _sample_wat() -> bytes:
    return b"".join(
        [
            _warc_record("warcinfo", b"software: test\r\n"),
            _wat_metadata(
                "https://page1.test/",
                ["mailto:a@b.test", "https://x.test/1", "ftp://f.test/file"],
            ),
            _warc_record("request", b"GET / HTTP/1.1\r\n"),
            _wat_metadata("https://page2.test/", ["tel:+15551234", "irc://irc.test/chan"]),
        ]
    )


def test_extracts_scheme_diverse_links() -> None:
    got = list(iter_links_from_wat(io.BytesIO(_sample_wat())))
    assert got == [
        "mailto:a@b.test",
        "https://x.test/1",
        "ftp://f.test/file",
        "tel:+15551234",
        "irc://irc.test/chan",
    ]


def test_ignores_non_metadata_and_bad_json() -> None:
    stream = io.BytesIO(
        _warc_record("metadata", b"not json{{")
        + _wat_metadata("https://ok.test/", ["https://y.test/2"])
    )
    assert list(iter_links_from_wat(stream)) == ["https://y.test/2"]


def test_connector_reads_plain_and_is_source(tmp_path: Path) -> None:
    path = tmp_path / "sample.wat"
    path.write_bytes(_sample_wat())
    src = CommonCrawlWat([path], crawl_id="CC-MAIN-2024-10")
    assert isinstance(src, Source)
    urls = list(src.iter_uris())
    assert "mailto:a@b.test" in urls and "irc://irc.test/chan" in urls
    prov = src.provenance()
    assert prov["source"] == "commoncrawl-outlinks"
    assert prov["crawl_id"] == "CC-MAIN-2024-10"
    assert prov["files_read"] == [str(path)]
    assert prov["urls_read"] == 5


def test_connector_reads_gzip(tmp_path: Path) -> None:
    path = tmp_path / "sample.wat.gz"
    with gzip.open(path, "wb") as fh:
        fh.write(_sample_wat())
    urls = list(CommonCrawlWat([path]).iter_uris())
    assert len(urls) == 5


def test_sampling_is_deterministic_and_bounded(tmp_path: Path) -> None:
    # Many links so a 0.5 sample is a strict, reproducible subset.
    links = [f"https://host{i}.test/{i}" for i in range(200)]
    path = tmp_path / "big.wat"
    path.write_bytes(_wat_metadata("https://p.test/", links))
    a = list(CommonCrawlWat([path], sample_rate=0.5, seed=3).iter_uris())
    b = list(CommonCrawlWat([path], sample_rate=0.5, seed=3).iter_uris())
    assert a == b
    assert 0 < len(a) < len(links)
    assert set(a).issubset(set(links))


def test_resolve_wat_paths_filters_and_joins() -> None:
    manifest = "\n".join(
        [
            "crawl-data/CC-MAIN-2024-10/segments/1/wat/CC-MAIN-0.warc.wat.gz",
            "crawl-data/CC-MAIN-2024-10/segments/1/wat/CC-MAIN-1.warc.wat.gz",
            "crawl-data/CC-MAIN-2024-10/segments/1/wet/CC-MAIN-0.warc.wet.gz",  # not WAT
            "",  # blank tolerated
        ]
    )
    paths = resolve_wat_paths(manifest)
    assert paths == [
        "https://data.commoncrawl.org/crawl-data/CC-MAIN-2024-10/segments/1/wat/CC-MAIN-0.warc.wat.gz",
        "https://data.commoncrawl.org/crawl-data/CC-MAIN-2024-10/segments/1/wat/CC-MAIN-1.warc.wat.gz",
    ]
    assert len(resolve_wat_paths(manifest, limit=1)) == 1
    assert resolve_wat_paths(manifest, prefix="s3://commoncrawl")[0].startswith("s3://commoncrawl/")


class _GzipFileHandler(BaseHTTPRequestHandler):
    """Serve a single in-memory gzip body sequentially (like the CC WAT mirror)."""

    def log_message(self, *args: object) -> None:  # silence test output
        pass

    def do_GET(self) -> None:
        data: bytes = self.server.data  # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _serve_bytes(data: bytes) -> tuple[ThreadingHTTPServer, threading.Thread, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _GzipFileHandler)
    server.data = data  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}/sample.wat.gz"
    return server, thread, url


def test_connector_streams_gzip_over_http() -> None:
    # The HTTPS path gunzips a sequential body on the fly (no range requests).
    server, thread, url = _serve_bytes(gzip.compress(_sample_wat()))
    try:
        urls = list(CommonCrawlWat([url]).iter_uris())
        assert urls == [
            "mailto:a@b.test",
            "https://x.test/1",
            "ftp://f.test/file",
            "tel:+15551234",
            "irc://irc.test/chan",
        ]
    finally:
        server.shutdown()
        thread.join()
