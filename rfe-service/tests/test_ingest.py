from pathlib import Path

import pytest

from rfe_service import ingest

ROOT = Path(__file__).resolve().parents[2]
CERT_B64 = (ROOT / "testdata" / "sample-cert.b64").read_text()

VALID_URI_META = {
    "schema_version": 1,
    "grammar": "rfc3986-uri",
    "kind": "uri",
    "submitter": "Ada Lovelace <ada@example.org>",
    "rationale": "Our internal URLs use userinfo, which the sample corpus omitted.",
}
VALID_CERT_META = {**VALID_URI_META, "grammar": "rfc5280-x509", "kind": "cert"}


def test_valid_uri_submission():
    zip_bytes = ingest.build_submission_zip(
        VALID_URI_META, uris="http://a.example/\nhttps://b.example/x\n"
    )
    sub = ingest.load_submission(zip_bytes)
    assert sub.grammar == "rfc3986-uri"
    assert sub.kind == "uri"
    assert sub.uris == ["http://a.example/", "https://b.example/x"]
    assert sub.sample_count == 2


def test_valid_cert_submission():
    zip_bytes = ingest.build_submission_zip(VALID_CERT_META, certs_b64=CERT_B64)
    sub = ingest.load_submission(zip_bytes)
    assert sub.kind == "cert"
    assert sub.sample_count == 1
    assert sub.certs[0][0] == 0x30  # DER SEQUENCE


def test_not_a_zip():
    with pytest.raises(ingest.SubmissionError, match="zip"):
        ingest.load_submission(b"definitely not a zip")


def test_missing_manifest():
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("corpus/uris.txt", "http://a.example/\n")
    with pytest.raises(ingest.SubmissionError, match="submission.json"):
        ingest.load_submission(buf.getvalue())


def test_manifest_fails_schema():
    bad = {**VALID_URI_META}
    del bad["rationale"]
    zip_bytes = ingest.build_submission_zip(bad, uris="http://a.example/\n")
    with pytest.raises(ingest.SubmissionError, match="schema"):
        ingest.load_submission(zip_bytes)


def test_empty_corpus_rejected():
    zip_bytes = ingest.build_submission_zip(VALID_URI_META, uris="# only a comment\n\n")
    with pytest.raises(ingest.SubmissionError, match="no URIs"):
        ingest.load_submission(zip_bytes)


def test_uri_kind_missing_corpus():
    zip_bytes = ingest.build_submission_zip(VALID_URI_META)  # no uris file
    with pytest.raises(ingest.SubmissionError, match="corpus/uris.txt"):
        ingest.load_submission(zip_bytes)
