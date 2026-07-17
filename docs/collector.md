# Collector: free URI corpus → hits.json

The `mvs_pipeline.collector` subsystem (Epic 6) builds a representative,
free-sourced URI corpus and runs it through the telemetry engine. It implements
the plan in [`CORPUS-PLAN.md`](CORPUS-PLAN.md).

```
 sources ──▶ normalize/dedup/cap ──▶ stratified sample ──▶ corpus shards
 (CC index,  (T6.4: non-lossy hygiene,  (T6.5: quotas, seed,     │
  CC WAT,     exact dedup, per-domain    bottom-k, manifest)      ▼
  Wikipedia,  cap via vendored PSL)                          mvs-telemetry ×N
  file lists)                                                (T4.2 bounds on)
                                                                  │
                                                        merge (T6.6) + stamp
                                                        provenance (T6.7)
                                                                  ▼
                                                         validated hits.json
```

## Pieces

| Stage | Module | What |
| --- | --- | --- |
| Sources | `commoncrawl`, `wat`, `wikipedia`, `filelist` | stream URIs from each free source, sampleable, provenance-tracked |
| Normalize/dedup/cap | `normalize`, `psl`, `dedup` | non-lossy hygiene; exact dedup + per-registrable-domain cap in bounded memory |
| Sample | `sampler` | per-stratum quotas → exact N via deterministic bottom-k; sharded corpus + `manifest.json` |
| Merge | `hitsmerge` | sum per-shard `hits.json` (associative, schema-valid) |
| Provenance | `provenance` | additive block stamped on hits/pruned |
| Orchestrate | `orchestrate` | one call: sources → shards → bounded telemetry → merged, stamped `hits.json` |

## Running it

Build the binary once, then drive everything through
`python -m mvs_pipeline.collector.orchestrate`. Each source is a repeatable
flag; strata combine under the weights you give them.

```bash
cargo build -p mvs-cli --release --manifest-path core/Cargo.toml   # → core/target/release/mvs-telemetry
```

**Local files** (`--list`/`--wat`/`--wiki` take `NAME=WEIGHT:PATH`):

```bash
python -m mvs_pipeline.collector.orchestrate \
  --ast artifacts/rfc3986-uri.ast.json \
  --list pages=0.8:corpus/pages.txt \
  --list outlinks=0.2:corpus/outlinks.txt \
  --target 100000 --seed 0 --domain-cap 1000 \
  --workdir .work --out out \
  --binary core/target/release/mvs-telemetry
# → out/corpus/corpus-*.txt, out/corpus/manifest.json, out/hits.json
```

**Straight from Common Crawl** — `--cc-crawl` streams the columnar URL index for
a crawl straight from the public `data.commoncrawl.org` mirror over HTTPS range
requests — **no AWS credentials, no manual path wrangling**. Start with
`--cc-limit 2` to validate it in a few minutes, then scale up and thin with
`--cc-sample-rate`:

```bash
python -m mvs_pipeline.collector.orchestrate \
  --ast artifacts/rfc3986-uri.ast.json \
  --cc-crawl CC-MAIN-2024-10 --cc-limit 2 --cc-sample-rate 1.0 \
  --target 200000 --seed 0 --domain-cap 1000 \
  --workdir .work --out out \
  --binary core/target/release/mvs-telemetry
```

**All three strata streamed, zero downloads** — `--cc-crawl` (page URLs),
`--wat-crawl` (scheme-diverse outlinks), and `--wiki-dump` (curated external
links) each resolve their own manifests off the public mirrors and stream over
HTTPS. This is the de-biased corpus the pruner needs: page URLs alone are almost
all http/https, so `mailto:`/`tel:`/`ftp:`/IPv6/userinfo only appear once the
outlink and Wikipedia strata are mixed in.

```bash
python -m mvs_pipeline.collector.orchestrate \
  --ast artifacts/rfc3986-uri.ast.json \
  --cc-crawl CC-MAIN-2024-10 --cc-weight 0.7 --cc-limit 300 --cc-sample-rate 0.03 \
  --wat-crawl CC-MAIN-2024-10 --wat-weight 0.2 --wat-limit 40 --wat-sample-rate 0.5 \
  --wiki-dump enwiki --wiki-date latest --wiki-weight 0.1 \
  --target 100000000 --seed 0 --domain-cap 1000 \
  --workdir .work --out out --binary core/target/release/mvs-telemetry
```

`--wat-crawl <id>` fetches `crawl-data/<id>/wat.paths.gz` and streams that many
(`--wat-limit`) WAT files sequentially — each is large, so start small.
`--wiki-dump <wiki>` streams `<wiki>/<date>/<wiki>-<date>-externallinks.sql.gz`
from `dumps.wikimedia.org` (`--wiki-date` defaults to `latest`). The local-file
`--wat`/`--wiki NAME=WEIGHT:PATH` forms still work if you'd rather pre-download.

`--cc-crawl` reads over HTTPS by default (works anywhere the mirror is
reachable); pass `--cc-transport s3` to stream from `s3://commoncrawl`
anonymously instead (e.g. on EC2, for free in-region egress). It defaults to the
`warc` subset (the page-URL corpus); the
`crawldiagnostics`/`robotstxt` subsets are crawler bookkeeping and are excluded.

## One command: sources → minified grammar

Add `--emit-mvs PATH` and the run continues past `hits.json` straight into
pruning (Phase 3) and code generation (Phase 4): every node used by fewer than
`--threshold` of samples that the override registry does not protect is dropped,
and the surviving rules are unparsed to a minified but valid grammar. This is the
whole pipeline in a single invocation — raw sources to a shippable MVS.

```bash
python -m mvs_pipeline.collector.orchestrate \
  --ast artifacts/rfc3986-uri.ast.json \
  --cc-crawl CC-MAIN-2024-10 --cc-weight 0.7 --cc-sample-rate 0.03 \
  --wat-crawl CC-MAIN-2024-10 --wat-weight 0.2 --wat-limit 40 --wat-sample-rate 0.5 \
  --wiki-dump enwiki --wiki-weight 0.1 \
  --target 100000000 --seed 0 --domain-cap 1000 --workers 8 \
  --workdir .work --out out --binary core/target/release/mvs-telemetry \
  --emit-mvs out/rfc3986-uri.mvs.abnf --pruned-out out/rfc3986-uri.pruned.json
# → out/hits.json, out/rfc3986-uri.pruned.json, out/rfc3986-uri.mvs.abnf
```

`--overrides` picks a registry other than the repo `overrides.yaml`;
`--surviving-grammar` names the output grammar (default `<grammar>-mvs`);
`--threshold` sets the usage cutoff; `--mvs-format abnf|asn1` overrides the
auto-detected format. `--workers N` fans the CPU-bound dedup/partition step
across `N` processes without changing the (deterministic) output.

CI builds the binary and runs the same path over committed fixtures
(`pipeline/tests/test_collector_orchestrate.py`, the `e2e` job).

## The full 10⁸ run

Reaching a statistically adequate 10⁸-URI corpus (see the sizing argument in
[`CORPUS-PLAN.md`](CORPUS-PLAN.md#sizing--compute-budget)):

- **Crawl selection.** Pin one recent monthly Common Crawl (e.g. the latest
  `CC-MAIN-YYYY-WW`). `CommonCrawlUrlIndex.from_crawl(crawl_id)` resolves the
  columnar `cc-index` subset files from
  `crawl-data/<id>/cc-index-table.paths.gz` over the free `data.commoncrawl.org`
  mirror. Add the same crawl's WAT outlinks and the latest
  `enwiki-<date>-externallinks.sql.gz`.
- **Quotas.** Default **70%** CC page URLs · **20%** CC outlinks · **10%**
  Wikipedia, per-registrable-domain cap ~1000. Tunable; recorded in the manifest.
- **Volume & disk.** ~3 B CC page URLs (sample ~1-in-30 → 7×10⁷) + ~2×10⁷
  outlinks + ~10⁷ Wikipedia → 10⁸. Raw corpus ≈ 8 GB / ~2–3 GB compressed.
- **Bandwidth.** Column/predicate pushdown over the CC index reads tens of GB
  (not the ~300 GB full index); free public egress; a few hours.
- **Time.** Embarrassingly parallel: shard the corpus and run `mvs-telemetry`
  per shard (T4.2 bounds on so no single URL stalls a shard), then merge. A
  laptop finishes overnight.
- **Reproducibility.** Fix `--seed`; the manifest records crawl/dump ids, seed,
  per-stratum counts, and each source's files. Same seed → same corpus, and the
  provenance block on `hits.json` ties every pruning decision back to it.

## Bounded ingestion (T4.2)

`mvs-telemetry` takes `--max-depth` (abandon a parse descending deeper than N
frames) and `--max-input-bytes` (skip an over-long line). The orchestrator
enables both by default so one pathological URL in 10⁸ cannot stall a shard.

The recognizer recurses ~one frame per matched byte, so `--max-depth` is really
a bound on input *length*, not just grammar nesting — set too low it records
long-but-valid URLs as non-matches. The default (200,000) is sized to accept any
URL within `--max-input-bytes` (8192), and the binary runs its matching on a
large-stack worker thread so that deep-but-legal recursion doesn't overflow.
