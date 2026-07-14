from fastapi.testclient import TestClient

from rfe_service import ingest
from rfe_service.app import app

client = TestClient(app)

META = {
    "schema_version": 1,
    "grammar": "rfc3986-uri",
    "kind": "uri",
    "submitter": "team@example.org",
    "rationale": "We rely on percent-encoding that the sample corpus under-counted.",
}


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_submit_accepts_valid_zip():
    zip_bytes = ingest.build_submission_zip(META, uris="http://a.example/\nhttp://b.example/\n")
    resp = client.post(
        "/rfe/submit", content=zip_bytes, headers={"content-type": "application/zip"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["accepted"] is True
    assert body["grammar"] == "rfc3986-uri"
    assert body["kind"] == "uri"
    assert body["samples"] == 2


def test_submit_rejects_bad_zip():
    resp = client.post("/rfe/submit", content=b"not a zip")
    assert resp.status_code == 422
    body = resp.json()
    assert body["accepted"] is False
    assert "zip" in body["error"]
