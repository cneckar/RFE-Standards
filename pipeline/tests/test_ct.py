import base64
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
