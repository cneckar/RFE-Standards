"""RFE submission ingestion (Task 5.1).

Accept and validate a consumer's RFE upload — an argument, backed by a corpus,
that a pruned feature is still needed in their domain. A submission is a zip:

```
submission.json         # manifest (schemas/rfe-submission.schema.json)
corpus/uris.txt         # for kind "uri": one URI per line
corpus/certs.b64        # for kind "cert": one base64 DER certificate per line
```

This module validates payload *formatting* only; the telemetry re-run
(Task 5.2) is what actually evaluates whether the corpus justifies restoring a
node. Kept independent of the web layer so it is testable without a server.
"""

from __future__ import annotations

import io
import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jsonschema import ValidationError

from mvs_pipeline import corpus, ct, schema

_MANIFEST = "submission.json"
_URI_CORPUS = "corpus/uris.txt"
_CERT_CORPUS = "corpus/certs.b64"


class SubmissionError(ValueError):
    """Raised when an RFE submission is malformed."""


@dataclass
class Submission:
    """A validated RFE submission."""

    meta: dict[str, Any]
    uris: list[str] = field(default_factory=list)
    certs: list[bytes] = field(default_factory=list)

    @property
    def grammar(self) -> str:
        return self.meta["grammar"]

    @property
    def kind(self) -> str:
        return self.meta["kind"]

    @property
    def sample_count(self) -> int:
        return len(self.uris) if self.kind == "uri" else len(self.certs)


def load_submission(data: bytes | str | Path) -> Submission:
    """Validate a submission zip (bytes or a path) and return it parsed."""
    raw = _read_bytes(data)
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise SubmissionError(f"not a valid zip archive: {exc}") from exc

    names = set(archive.namelist())
    if _MANIFEST not in names:
        raise SubmissionError(f"missing {_MANIFEST}")

    try:
        meta = json.loads(archive.read(_MANIFEST))
    except json.JSONDecodeError as exc:
        raise SubmissionError(f"{_MANIFEST} is not valid JSON: {exc}") from exc

    try:
        schema.validate("rfe", meta)
    except ValidationError as exc:
        raise SubmissionError(f"{_MANIFEST} failed schema validation: {exc.message}") from exc

    kind = meta["kind"]
    if kind == "uri":
        if _URI_CORPUS not in names:
            raise SubmissionError(f"kind 'uri' requires {_URI_CORPUS}")
        uris = list(corpus.iter_uris_from_list(archive.read(_URI_CORPUS).decode("utf-8")))
        if not uris:
            raise SubmissionError(f"{_URI_CORPUS} contained no URIs")
        return Submission(meta=meta, uris=uris)

    # kind == "cert" (enforced by the schema enum)
    if _CERT_CORPUS not in names:
        raise SubmissionError(f"kind 'cert' requires {_CERT_CORPUS}")
    try:
        certs = list(ct.iter_certs_b64(archive.read(_CERT_CORPUS).decode("utf-8")))
    except (ValueError, UnicodeDecodeError) as exc:
        raise SubmissionError(f"{_CERT_CORPUS} is not valid base64: {exc}") from exc
    if not certs:
        raise SubmissionError(f"{_CERT_CORPUS} contained no certificates")
    return Submission(meta=meta, certs=certs)


def _read_bytes(data: bytes | str | Path) -> bytes:
    if isinstance(data, bytes):
        return data
    return Path(data).read_bytes()


def build_submission_zip(
    meta: dict[str, Any], *, uris: str | None = None, certs_b64: str | None = None
) -> bytes:
    """Assemble a submission zip in memory (used by tests and tooling)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(_MANIFEST, json.dumps(meta))
        if uris is not None:
            archive.writestr(_URI_CORPUS, uris)
        if certs_b64 is not None:
            archive.writestr(_CERT_CORPUS, certs_b64)
    return buffer.getvalue()
