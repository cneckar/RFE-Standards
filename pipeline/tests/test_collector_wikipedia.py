"""Tests for the Wikipedia externallinks SQL-dump connector (T6.3)."""

from __future__ import annotations

import gzip
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from mvs_pipeline.collector import dump_url
from mvs_pipeline.collector.wikipedia import (
    WikipediaExternalLinks,
    iter_external_urls,
)

# Classic schema: (el_id, el_from, el_to, el_index, el_index_60).
# el_index holds a reversed-host sort key that is *also* URL-shaped — the
# connector must pick el_to (column 2) positionally, not by shape.
_LINE = (
    "INSERT INTO `externallinks` VALUES "
    "(1,10,'https://example.com/a','https://com.example./a','https://com.example./a'),"
    "(2,11,'mailto:hi@example.org','mailto:hi@example.org','mailto:hi@example.org'),"
    "(3,12,'ftp://files.test/x','ftp://test.files./x','ftp://test.files./x'),"
    "(4,13,'https://a.test/it\\'s escaped','https://test.a./x','https://test.a./x');"
)


def test_extracts_el_to_not_reversed_index() -> None:
    urls = list(iter_external_urls(iter([_LINE])))
    assert urls == [
        "https://example.com/a",
        "mailto:hi@example.org",
        "ftp://files.test/x",
        "https://a.test/it's escaped",  # backslash-escaped quote decoded
    ]


def test_ignores_other_tables() -> None:
    other = "INSERT INTO `page` VALUES (1,0,'Main');"
    assert list(iter_external_urls(iter([other, _LINE]))) == [
        "https://example.com/a",
        "mailto:hi@example.org",
        "ftp://files.test/x",
        "https://a.test/it's escaped",
    ]


def test_connector_reads_gzip_and_records_dump_date(tmp_path: Path) -> None:
    path = tmp_path / "enwiki-20240101-externallinks.sql.gz"
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        fh.write(_LINE + "\n")
    src = WikipediaExternalLinks([path])
    urls = list(src.iter_uris())
    assert len(urls) == 4
    prov = src.provenance()
    assert prov["source"] == "wikipedia-externallinks"
    assert prov["dump_dates"] == ["20240101"]
    assert prov["files_read"] == [str(path)]
    assert prov["urls_read"] == 4


def test_connector_reads_plain_sql(tmp_path: Path) -> None:
    path = tmp_path / "dump.sql"
    path.write_text(_LINE + "\n", encoding="utf-8")
    assert list(WikipediaExternalLinks([path]).iter_uris())[0] == "https://example.com/a"


def test_sampling_is_deterministic_and_bounded(tmp_path: Path) -> None:
    tuples = ",".join(f"({i},{i},'https://h{i}.test/{i}','x','y')" for i in range(200))
    path = tmp_path / "big.sql"
    path.write_text(f"INSERT INTO `externallinks` VALUES {tuples};\n", encoding="utf-8")
    a = list(WikipediaExternalLinks([path], sample_rate=0.5, seed=9).iter_uris())
    b = list(WikipediaExternalLinks([path], sample_rate=0.5, seed=9).iter_uris())
    assert a == b
    assert 0 < len(a) < 200


def test_null_and_numeric_fields_are_not_urls() -> None:
    line = "INSERT INTO `externallinks` VALUES (1,2,NULL,'x','y'),(3,4,'https://ok.test/','x','y');"
    assert list(iter_external_urls(iter([line]))) == ["https://ok.test/"]


def test_dump_url_shape() -> None:
    assert dump_url("enwiki") == (
        "https://dumps.wikimedia.org/enwiki/latest/enwiki-latest-externallinks.sql.gz"
    )
    assert dump_url("dewiki", "20240101", host="https://mirror.test") == (
        "https://mirror.test/dewiki/20240101/dewiki-20240101-externallinks.sql.gz"
    )


class _GzipFileHandler(BaseHTTPRequestHandler):
    """Serve a single in-memory gzip dump body sequentially (like the dumps host)."""

    def log_message(self, *args: object) -> None:  # silence test output
        pass

    def do_GET(self) -> None:
        data: bytes = self.server.data  # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def test_connector_streams_gzip_dump_over_http() -> None:
    # The HTTPS path gunzips the sequential dump on the fly; the dump date and
    # the http(s) URL both survive into provenance (Path would mangle the URL).
    body = gzip.compress((_LINE + "\n").encode("utf-8"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), _GzipFileHandler)
    server.data = body  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/enwiki-20240101-externallinks.sql.gz"
        src = WikipediaExternalLinks([url])
        urls = list(src.iter_uris())
        assert urls == [
            "https://example.com/a",
            "mailto:hi@example.org",
            "ftp://files.test/x",
            "https://a.test/it's escaped",
        ]
        prov = src.provenance()
        assert prov["dump_dates"] == ["20240101"]
        assert prov["files_read"] == [url]
    finally:
        server.shutdown()
        thread.join()
