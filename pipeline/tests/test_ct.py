import base64
import json
import struct
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from mvs_pipeline import ct

ROOT = Path(__file__).resolve().parents[2]


def test_iter_certs_b64_decodes_committed_sample():
    b64 = (ROOT / "testdata" / "sample-cert.b64").read_text()
    certs = list(ct.iter_certs_b64(b64))
    assert len(certs) == 1
    der = certs[0]
    assert der[0] == 0x30, "DER certificate must start with a SEQUENCE tag"
    # Round-trips to the exact committed DER fixture the Rust walker tests against.
    assert der == (ROOT / "corpus" / "certs" / "sample-cert.der").read_bytes()


def test_iter_certs_b64_skips_comments_and_blanks():
    payload = b"\x30\x03\x02\x01\x05"
    text = "# a comment\n" + base64.b64encode(payload).decode() + "\n\n"
    assert list(ct.iter_certs_b64(text)) == [payload]


def test_write_cert_dir(tmp_path):
    n = ct.write_cert_dir([b"\x30\x00", b"\x30\x01\x00"], tmp_path / "certs")
    assert n == 2
    files = sorted((tmp_path / "certs").glob("*.der"))
    assert [f.name for f in files] == ["cert-000000.der", "cert-000001.der"]
    assert files[0].read_bytes() == b"\x30\x00"


def test_telemetry_der_argv():
    argv = ct.telemetry_der_argv("mvs-telemetry", "x509.json", "certs/", "hits.json")
    assert argv == [
        "mvs-telemetry",
        "--ast",
        "x509.json",
        "--der-dir",
        "certs/",
        "--out",
        "hits.json",
    ]


# --- CT log fetcher (RFC 6962) --------------------------------------------- #


def _u24(n: int) -> bytes:
    return n.to_bytes(3, "big")


def _x509_leaf(der: bytes) -> bytes:
    """A MerkleTreeLeaf for an x509_entry embedding ``der`` (as get-entries serves)."""
    # version(0) leaf_type(0) timestamp(8) entry_type(0) ASN.1Cert(u24 len + der) ext(0)
    return b"\x00\x00" + struct.pack(">Q", 0) + b"\x00\x00" + _u24(len(der)) + der + b"\x00\x00"


def _precert_leaf(tbs: bytes) -> bytes:
    """A precert_entry leaf: holds only the TBSCertificate (full DER is in extra_data)."""
    return (
        b"\x00\x00"
        + struct.pack(">Q", 0)
        + b"\x00\x01"  # entry_type = precert_entry
        + b"\x00" * 32  # issuer_key_hash
        + _u24(len(tbs))
        + tbs
        + b"\x00\x00"
    )


def _precert_extra(der: bytes) -> bytes:
    """A PrecertChainEntry extra_data whose first ASN.1Cert is the full precert ``der``."""
    return _u24(len(der)) + der + _u24(0)


def test_cert_der_from_x509_and_precert_entries():
    der = (ROOT / "corpus" / "certs" / "sample-cert.der").read_bytes()
    # x509_entry: cert lives in the leaf; extra_data (chain) is irrelevant here.
    assert ct.cert_der_from_entry(_x509_leaf(der), _u24(0)) == der
    # precert_entry: leaf has only the TBS; full precert DER comes from extra_data.
    assert ct.cert_der_from_entry(_precert_leaf(b"\x30\x02\x05\x00"), _precert_extra(der)) == der
    # Unknown/short input is skipped, not raised.
    assert ct.cert_der_from_entry(b"\x00\x00", b"") is None
    assert ct.cert_der_from_entry(b"\x00\x09" + b"\x00" * 10, b"") is None  # unknown entry_type


class _CtLogHandler(BaseHTTPRequestHandler):
    """Minimal RFC 6962 log: get-sth + paged get-entries over an in-memory list."""

    def log_message(self, *args: object) -> None:
        pass

    def _json(self, obj: object) -> None:
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        entries = self.server.entries  # type: ignore[attr-defined]
        if self.path.startswith("/ct/v1/get-sth"):
            self._json({"tree_size": len(entries)})
            return
        from urllib.parse import parse_qs, urlparse

        q = parse_qs(urlparse(self.path).query)
        start, end = int(q["start"][0]), int(q["end"][0])
        page = entries[start : min(end, start + self.server.max_per_req - 1) + 1]  # type: ignore[attr-defined]
        self._json({"entries": page})


def _serve_log(entries: list[dict], max_per_req: int = 256):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _CtLogHandler)
    srv.entries = entries  # type: ignore[attr-defined]
    srv.max_per_req = max_per_req  # type: ignore[attr-defined]
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    return srv, thread, f"http://127.0.0.1:{srv.server_address[1]}"


def _entry(leaf: bytes, extra: bytes = b"") -> dict:
    return {
        "leaf_input": base64.b64encode(leaf).decode(),
        "extra_data": base64.b64encode(extra).decode(),
    }


def test_ct_log_source_fetches_dedupes_and_reports_provenance():
    der_a = (ROOT / "corpus" / "certs" / "sample-cert.der").read_bytes()
    der_b = b"\x30\x03\x02\x01\x2a"  # a second, distinct "cert"
    entries = [
        _entry(_x509_leaf(der_a)),
        _entry(_x509_leaf(der_a)),  # exact duplicate -> dropped
        _entry(_precert_leaf(b"\x30\x01\x00"), _precert_extra(der_b)),
    ]
    srv, thread, url = _serve_log(entries)
    try:
        src = ct.CtLogSource(url, start=0, count=3)
        got = list(src.iter_ders())
        assert got == [der_a, der_b]  # dedup dropped the repeat, in scan order
        prov = src.provenance()
        assert prov["source"] == "certificate-transparency"
        assert prov["entries_scanned"] == 3
        assert prov["duplicates_dropped"] == 1
        assert prov["certs_kept"] == 2
        assert ct.get_sth(url) == 3
    finally:
        srv.shutdown()
        thread.join()


def test_ct_log_source_pages_and_samples_deterministically():
    ders = [b"\x30\x03\x02\x01" + bytes([i % 251]) for i in range(300)]
    entries = [_entry(_x509_leaf(d)) for d in ders]
    srv, thread, url = _serve_log(entries, max_per_req=64)  # force multi-page paging
    try:
        a = list(ct.CtLogSource(url, count=300, sample_rate=0.5, seed=7, page_size=128).iter_ders())
        b = list(ct.CtLogSource(url, count=300, sample_rate=0.5, seed=7, page_size=128).iter_ders())
        assert a == b  # reproducible for a fixed seed
        assert 0 < len(a) < 300  # actually sampled a strict subset
        assert set(a).issubset(set(ders))
    finally:
        srv.shutdown()
        thread.join()
