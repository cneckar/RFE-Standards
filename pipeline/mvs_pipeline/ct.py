"""Certificate Transparency / X.509 corpus preparation (Task 2.2).

The Python side of the CT log ingestor: turn a Certificate Transparency feed or
static certificate dataset into a directory of DER files that the native
``mvs-telemetry`` binary walks (``--der-dir``) to produce ``hits.json``. Per
ADR 0001 the language boundary is the on-disk corpus — Python fetches/decodes
the certificates, Rust decodes the DER and records node hits.

The supported input is a **base64 list**: one base64-encoded DER certificate per
line (``#`` comments and blank lines ignored). Public CT endpoints and static
dumps expose certificates in exactly this shape, so a fetcher only has to write
the leaf certificates out line by line.
"""

from __future__ import annotations

import base64
from collections.abc import Iterable, Iterator
from pathlib import Path


def iter_certs_b64(text: str) -> Iterator[bytes]:
    """Yield DER bytes for each base64 line, skipping blanks and ``#`` comments."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            yield base64.b64decode(stripped)


def write_cert_dir(certs: Iterable[bytes], directory: str | Path) -> int:
    """Write each DER certificate to ``directory/cert-NNNNNN.der``; return the count."""
    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)
    count = 0
    for index, der in enumerate(certs):
        (out / f"cert-{index:06d}.der").write_bytes(der)
        count += 1
    return count


def telemetry_der_argv(
    binary: str | Path,
    ast_path: str | Path,
    der_dir: str | Path,
    out_path: str | Path,
) -> list[str]:
    """Build the argv to run ``mvs-telemetry`` in DER mode over a certificate dir."""
    return [
        str(binary),
        "--ast",
        str(ast_path),
        "--der-dir",
        str(der_dir),
        "--out",
        str(out_path),
    ]
