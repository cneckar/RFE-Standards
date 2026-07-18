"""Certificate Transparency / X.509 corpus preparation (Task 2.2).

The Python side of the CT log ingestor: turn a Certificate Transparency feed or
static certificate dataset into a directory of DER files that the native
``mvs-telemetry`` binary walks (``--der-dir``) to produce ``hits.json``. Per
ADR 0001 the language boundary is the on-disk corpus — Python fetches/decodes
the certificates, Rust decodes the DER and records node hits.

The supported inputs are:

- a **base64 list** — one base64-encoded DER certificate per line (``#`` comments
  and blank lines ignored); and
- a live **Certificate Transparency log** (RFC 6962) via :class:`CtLogSource`,
  which pages ``get-entries``, parses each ``MerkleTreeLeaf``, and extracts a full
  DER certificate (the leaf for ``x509_entry``, the pre-certificate for
  ``precert_entry``). CT logs are the canonical public firehose of real-world
  certificates — the X.509 analog of Common Crawl for URIs. A fetch is reproducible
  from ``(log_url, start, count, seed)``: the log is append-only, so a fixed index
  range returns the same certificates every time.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
import urllib.parse
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any

from mvs_pipeline.collector.base import keep_sample
from mvs_pipeline.collector.http import fetch_bytes

#: RFC 6962 ``LogEntryType`` values.
_ENTRY_X509 = 0
_ENTRY_PRECERT = 1
#: ``MerkleLeafType.timestamped_entry``.
_LEAF_TIMESTAMPED = 0
#: Default entries per ``get-entries`` request. Logs cap the returned count (often
#: at 256–1024) and may return fewer; the pager advances by what it actually got.
DEFAULT_PAGE_SIZE = 256


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


# --- Certificate Transparency log fetcher (RFC 6962) ----------------------- #


def cert_der_from_entry(leaf_input: bytes, extra_data: bytes) -> bytes | None:
    """Extract a full DER certificate from one ``get-entries`` entry.

    ``leaf_input`` is a ``MerkleTreeLeaf``; ``extra_data`` its companion. For an
    ``x509_entry`` the leaf certificate DER is embedded in the leaf; for a
    ``precert_entry`` the leaf holds only the TBSCertificate, so the full
    pre-certificate DER is taken from ``extra_data`` (a ``PrecertChainEntry`` whose
    first element is the pre-certificate). Returns ``None`` for anything that does
    not parse as an expected structure, so a malformed or unknown entry is skipped
    rather than raising.
    """
    # MerkleTreeLeaf: version(1) leaf_type(1) TimestampedEntry{ timestamp(8)
    # entry_type(2) <cert-or-tbs> ... }.
    if len(leaf_input) < 12 or leaf_input[1] != _LEAF_TIMESTAMPED:
        return None
    entry_type = int.from_bytes(leaf_input[10:12], "big")
    if entry_type == _ENTRY_X509:
        return _read_u24_opaque(leaf_input, 12)
    if entry_type == _ENTRY_PRECERT:
        # extra_data = PrecertChainEntry { ASN.1Cert pre_certificate; chain }.
        return _read_u24_opaque(extra_data, 0)
    return None


def _read_u24_opaque(buf: bytes, offset: int) -> bytes | None:
    """Read a ``uint24``-length-prefixed byte string at ``offset``; ``None`` if short."""
    if len(buf) < offset + 3:
        return None
    length = int.from_bytes(buf[offset : offset + 3], "big")
    start = offset + 3
    end = start + length
    if length == 0 or len(buf) < end:
        return None
    return buf[start:end]


def get_sth(log_url: str, *, sleep: Callable[[float], None] = time.sleep) -> int:
    """Return the current tree size of a CT log (``get-sth``)."""
    data = fetch_bytes(f"{log_url.rstrip('/')}/ct/v1/get-sth", sleep=sleep)
    return int(json.loads(data)["tree_size"])


def get_entries(
    log_url: str, start: int, end: int, *, sleep: Callable[[float], None] = time.sleep
) -> list[dict[str, str]]:
    """Fetch entries ``[start, end]`` from a CT log (may return fewer than asked)."""
    query = urllib.parse.urlencode({"start": start, "end": end})
    data = fetch_bytes(f"{log_url.rstrip('/')}/ct/v1/get-entries?{query}", sleep=sleep)
    return json.loads(data)["entries"]


class CtLogSource:
    """Stream real DER certificates from a Certificate Transparency log.

    Scans ``count`` entries starting at ``start`` (paging ``get-entries``),
    extracts a full DER certificate from each, drops exact duplicates by SHA-256
    fingerprint, and keeps a deterministic ``sample_rate`` fraction by ``seed``.
    Because the log is append-only, a fixed ``(log_url, start, count, seed)``
    reproduces the same corpus.
    """

    def __init__(
        self,
        log_url: str,
        *,
        start: int = 0,
        count: int,
        sample_rate: float = 1.0,
        seed: int = 0,
        page_size: int = DEFAULT_PAGE_SIZE,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.log_url = log_url
        self.start = start
        self.count = count
        self.sample_rate = sample_rate
        self.seed = seed
        self.page_size = page_size
        self._sleep = sleep
        self._scanned = 0
        self._kept = 0
        self._duplicates = 0

    def iter_ders(self) -> Iterator[bytes]:
        """Yield deduplicated, sampled DER certificates across the scanned range."""
        seen: set[str] = set()
        idx = self.start
        remaining = self.count
        while remaining > 0:
            want = min(self.page_size, remaining)
            entries = get_entries(self.log_url, idx, idx + want - 1, sleep=self._sleep)
            if not entries:
                break  # reached the end of the log
            for entry in entries:
                self._scanned += 1
                der = cert_der_from_entry(
                    base64.b64decode(entry["leaf_input"]),
                    base64.b64decode(entry["extra_data"]),
                )
                if der is None:
                    continue
                fingerprint = hashlib.sha256(der).hexdigest()
                if fingerprint in seen:
                    self._duplicates += 1
                    continue
                seen.add(fingerprint)
                if keep_sample(fingerprint, self.sample_rate, self.seed):
                    self._kept += 1
                    yield der
            got = len(entries)
            idx += got
            remaining -= got

    def provenance(self) -> dict[str, Any]:
        """Record the reproducible fetch: log, range, sampling, and realized counts."""
        return {
            "source": "certificate-transparency",
            "log_url": self.log_url,
            "start": self.start,
            "count": self.count,
            "sample_rate": self.sample_rate,
            "seed": self.seed,
            "entries_scanned": self._scanned,
            "duplicates_dropped": self._duplicates,
            "certs_kept": self._kept,
        }


def _main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="Fetch a real certificate corpus from a CT log (RFC 6962) into a DER dir.",
    )
    parser.add_argument("--log", required=True, help="CT log base URL, e.g. https://ct.example/log")
    parser.add_argument("--start", type=int, default=0, help="first log index to scan")
    parser.add_argument("--count", type=int, required=True, help="number of log entries to scan")
    parser.add_argument(
        "--sample-rate", type=float, default=1.0, help="fraction of certs to keep (by seed)"
    )
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE)
    parser.add_argument("--out-dir", type=Path, required=True, help="directory for cert-*.der")
    args = parser.parse_args(argv)

    def progress(msg: str) -> None:
        print(msg, file=sys.stderr, flush=True)

    src = CtLogSource(
        args.log,
        start=args.start,
        count=args.count,
        sample_rate=args.sample_rate,
        seed=args.seed,
        page_size=args.page_size,
    )
    progress(f"scanning {args.count:,} entries from {args.log} at index {args.start}…")
    written = 0
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for der in src.iter_ders():
        (out / f"cert-{written:06d}.der").write_bytes(der)
        written += 1
        if written % 5000 == 0:
            progress(f"  wrote {written:,} certs")
    prov = src.provenance()
    progress(
        f"done: scanned {prov['entries_scanned']:,}, dropped {prov['duplicates_dropped']:,} dupes, "
        f"wrote {written:,} certs → {out}"
    )
    (out / "provenance.json").write_text(json.dumps(prov, indent=2) + "\n")
    print(written)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
