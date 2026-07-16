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

Small run over local files (one stratum per `--list NAME=WEIGHT:PATH`):

```bash
cargo build -p mvs-cli --manifest-path core/Cargo.toml   # builds mvs-telemetry

python -m mvs_pipeline.collector.orchestrate \
  --ast artifacts/rfc3986-uri.ast.json \
  --list pages=0.8:corpus/pages.txt \
  --list outlinks=0.2:corpus/outlinks.txt \
  --target 100000 --seed 0 --domain-cap 1000 \
  --workdir .work --out out \
  --binary core/target/debug/mvs-telemetry
# → out/corpus/corpus-*.txt, out/corpus/manifest.json, out/hits.json
```

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
