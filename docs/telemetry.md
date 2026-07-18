# Telemetry pipeline (Phase 2)

How real-world corpora become node-hit rates.

```
 Common Crawl / URL lists        corpus file            hits.json
 ┌──────────────────────┐  prep  ┌───────────┐  parse  ┌───────────────┐
 │ WARC / WAT / raw list │ ─────▶ │ uri-*.txt │ ──────▶ │ *.hits.json   │
 └──────────────────────┘ Python └───────────┘  Rust   └───────────────┘
        mvs_pipeline.corpus              mvs-telemetry (mvs-cli)
```

The language boundary (ADR 0001) is the **corpus file**: Python prepares it, the
native `mvs-telemetry` binary parses it.

## Preparing a corpus (Python)

`mvs_pipeline.corpus` extracts URIs from Common Crawl-style inputs and writes a
newline-delimited corpus:

```python
from mvs_pipeline import corpus

uris = corpus.iter_uris(open("crawl.warc").read(), fmt="warc")
corpus.write_corpus(uris, "corpus/uri.txt")   # dedupes, returns count
```

## Running telemetry (Rust)

The `mvs-telemetry` binary parses each corpus line against a Phase-1 AST and
writes an aggregated `hits.json`:

```bash
mvs-telemetry \
  --ast artifacts/rfc3986-uri.ast.json \
  --corpus corpus/uri-sample.txt \
  --out artifacts/rfc3986-uri.hits.json
```

`corpus/uri-sample.txt` (a representative 14-URI sample) and its generated
`artifacts/rfc3986-uri.hits.json` are committed and checked for reproducibility
by the `mvs-cli` integration tests. `total_samples` is every input processed;
each node is credited at most once per matched input, so
`hits[node] / total_samples` is the fraction of inputs that exercised the node —
exactly what the Phase 3 pruner thresholds against.

## X.509 certificates (DER)

The same engine handles the X.509 corpus, with a DER decoder instead of the text
matcher:

```
 CT log / cert dump         DER files            hits.json
 ┌────────────────┐  prep   ┌───────────┐  walk   ┌────────────────────┐
 │ base64 leaf certs│ ─────▶ │ certs/*.der│ ──────▶ │ rfc5280-x509.hits  │
 └────────────────┘ Python  └───────────┘  Rust   └────────────────────┘
      mvs_pipeline.ct                mvs-telemetry --der-dir
```

`mvs_pipeline.ct` decodes base64 leaf certificates into a directory of `.der`
files; `mvs-telemetry --der-dir` walks each certificate against the RFC 5280 AST
(`mvs_core::der::DerWalker`), recording the ASN.1 nodes its encoding exercises.
The walker is schema-directed but lenient — it credits modeled fields precisely
and consumes anything beyond the model opaquely, so telemetry is robust to a
minimal schema. `corpus/certs/sample-cert.der` (a real self-signed certificate)
and its generated `artifacts/rfc5280-x509.hits.json` are committed and checked
for reproducibility.

```bash
mvs-telemetry \
  --ast artifacts/rfc5280-x509.ast.json \
  --der-dir corpus/certs \
  --out artifacts/rfc5280-x509.hits.json
```

### Fetching a real cert corpus from a CT log

For a statistically adequate corpus, pull real certificates straight from a
[Certificate Transparency](https://www.rfc-editor.org/rfc/rfc6962) log — the X.509
analog of Common Crawl. `mvs_pipeline.ct` pages `get-entries`, parses each
`MerkleTreeLeaf`, and writes a full DER certificate per entry (the leaf for
`x509_entry`, the pre-certificate for `precert_entry`), deduplicating by SHA-256
and keeping a deterministic `--sample-rate` fraction:

```bash
# Scan 2,000,000 entries of a public log, keep ~1-in-4, into a DER dir.
python -m mvs_pipeline.ct \
  --log https://<ct-log-host>/<log> \
  --start 0 --count 2000000 --sample-rate 0.25 --seed 0 \
  --out-dir corpus/certs-real
# → corpus/certs-real/cert-*.der + provenance.json, then run mvs-telemetry --der-dir on it.
```

The fetch is **reproducible**: a CT log is append-only, so a fixed
`(log, --start, --count, --seed)` returns the same certificates. Requests retry
transient failures with backoff (shared with the URI collector). Use
`get-sth` to size a log before scanning; each request returns at most the log's
per-call cap, and the pager advances by what it actually got.
