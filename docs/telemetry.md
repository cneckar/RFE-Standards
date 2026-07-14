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
