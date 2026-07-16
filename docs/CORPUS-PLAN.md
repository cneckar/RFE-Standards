# Plan: Free, Representative URI Corpus at 10⁸ Scale

**Goal.** Collect ~100,000,000 URIs that are *representative of actual usage*, using
**only free sources**, and feed them through the existing telemetry engine to
produce a trustworthy `rfc3986-uri.hits.json` for the pruner.

**Why it matters.** The pruner is only as good as the corpus is representative:
sampling bias becomes standard bias. The sample corpus committed today (14 URIs)
is a demonstrator; this plan replaces it with a statistically adequate, auditable
one — without paid data.

## Constraints

- **Free only.** No paid APIs/datasets. Every source below is publicly
  downloadable at no cost, with redistribution-friendly terms, and needs no
  crawling of live sites (so no robots.txt/ToS exposure).
- **Representative, not just large.** Stratified, per-domain-capped, seeded, and
  provenance-stamped so decisions are reproducible and challengeable.
- **Runs incrementally on one machine.** Shardable; no cluster or paid egress.

## Free sources

| Source | Access (free) | Contributes | Known bias |
| --- | --- | --- | --- |
| **Common Crawl — columnar URL index** (`cc-index` Parquet) | `https://data.commoncrawl.org/` public HTTPS; column + predicate pushdown, no full download | The bulk: ~2.7–3.5 B crawled page URLs per monthly crawl → the representative core | crawlable public web; ~99% http/https, reg-name hosts |
| **Common Crawl — WAT outlinks** | same host; `wat.paths` per crawl | Link diversity: `mailto:`/`tel:`/`ftp:`/`irc:`/IPv6/userinfo that page targets miss | link-context, not navigation |
| **Wikipedia — `externallinks` SQL dump** | `dumps.wikimedia.org` (CC BY-SA / GFDL) | Human-curated, extremely scheme-diverse external links | encyclopedic register |
| *(optional)* **GH Archive / Software Heritage** | free dumps | `git:`/`ssh:`/`file:` from configs & manifests | dev-centric |

Common Crawl + Wikipedia alone clear 10⁸ comfortably and for free. The columnar
**URL index** is the key efficiency: read just the `url` column with pushdown
instead of downloading WARCs.

## Representativeness strategy

1. **Proportional primary + flagged diversity supplement.** The hit-rate is
   measured mainly on a *proportional* sample of CC page target-URIs (true
   usage). Scheme-diverse sources (outlinks, Wikipedia) are included with modest,
   **documented weights** and tagged so their influence is explicit and can be
   excluded for a "pure navigation usage" cut.
2. **Per-registrable-domain cap.** Using the free Public Suffix List, cap URIs
   per registrable domain so a handful of mega-sites can't define the standard —
   representativeness means *across the web*, not "whatever Google does."
3. **Deterministic + audited.** Fixed RNG seed; a provenance manifest records
   crawl ids, source files, per-stratum counts, seed, and timestamp. `hits.json`
   is then reproducible and every pruning decision is traceable to its evidence.

Default quota (tunable): **70%** CC target-URIs · **20%** CC outlinks ·
**10%** Wikipedia externallinks, with a per-domain cap of ~1k.

## Sizing & compute budget

- **Volume.** One CC crawl ≈ 3 B target-URIs (sample ~1-in-30 for 7×10⁷) + tens
  of billions of outlinks (sample 2×10⁷) + Wikipedia (~10⁷). → 10⁸.
- **Disk.** 10⁸ URIs × ~80 B ≈ **8 GB** raw / ~2–3 GB compressed. Trivial.
- **Bandwidth.** Column/predicate pushdown over the CC index reads tens of GB
  (not the ~300 GB/crawl full index); free public egress. A few hours.
- **Statistical adequacy.** At N=10⁸, expected hits for a 10⁻⁶-usage node ≈ 100 —
  enough to separate "rare but real" from "absent" (rule-of-three bound ≈ 3×10⁻⁸).
  This is what justifies 10⁸ rather than an arbitrary number.
- **Runtime.** Embarrassingly parallel: shard the corpus, run `mvs-telemetry`
  per shard, merge. The Rust core is fast; a laptop finishes overnight.

## Architecture (new `collector/` subsystem)

```
 sources ──▶ normalize+dedup ──▶ stratified sampler ──▶ corpus shards
 (CC index,   (canonicalize,       (quotas, per-domain      │
  CC WAT,      exact-dedup,          cap, seed) + manifest    ▼
  Wikipedia)   domain cap)                              mvs-telemetry ×N
                                                              │
                                                        hits merge ──▶ hits.json
                                                        (+ provenance stamp)
```

Plugs straight into the existing pipeline: sources emit the same newline URI
corpus `mvs-telemetry --corpus` already consumes; only the merge + provenance
steps are new.

---

## Epic 6 — Free URI corpus collection at scale

### T6.1 — Source interface + Common Crawl URL-index connector
* **AC:** a streaming `Source` protocol yielding URIs; a connector that reads the
  `url` column from a crawl's `cc-index` Parquet over HTTPS with column/predicate
  pushdown (pyarrow/duckdb), bounded memory, resumable by index file; records
  crawl id + files read. Unit-tested against a tiny committed Parquet fixture.
* **Depends on:** —

### T6.2 — Common Crawl WAT outlink extractor
* **AC:** stream a crawl's WAT records and harvest extracted link URIs (the
  Links metadata), yielding scheme-diverse URIs page targets miss; sampleable;
  tested on a small committed WAT slice.
* **Depends on:** T6.1 (shared Source plumbing)

### T6.3 — Wikipedia externallinks connector
* **AC:** streaming parser for the `*-externallinks.sql.gz` dump (INSERT tuples)
  → external URLs; tested on a fixture dump slice; records dump date.
* **Depends on:** T6.1

### T6.4 — Normalization + large-scale dedup + domain cap
* **AC:** minimal canonicalization (UTF-8/control-char hygiene, no lossy
  rewriting); exact dedup at 10⁸ scale in bounded memory (external sort or
  sharded on-disk hashing); per-registrable-domain cap via a vendored Public
  Suffix List. Correctness + memory-bound tests on fixtures.
* **Depends on:** T6.1

### T6.5 — Stratified sampler + quota controller + manifest
* **AC:** combine sources under configurable per-stratum quotas and a per-domain
  cap; deterministic seed; reservoir/weighted sampling to an exact target N;
  emit sharded corpus files **and** a provenance manifest (sources, crawl/dump
  ids, seed, per-stratum counts). Reproducible: same seed → same corpus.
* **Depends on:** T6.2, T6.3, T6.4

### T6.6 — `hits.json` shard-merge (enabler)
* **AC:** merge many per-shard `hits.json` (sum per-node counts, sum
  `total_samples`), schema-validated; associative and order-independent; a
  `mvs-telemetry merge` subcommand (or `mvs_pipeline` helper). Fixtures.
* **Depends on:** —

### T6.7 — Provenance stamping on hits.json / pruned.json (enabler)
* **AC:** an **optional, additive** `provenance` block (corpus manifest ref,
  crawl/dump ids, timestamp, seed, sample size) on the hits and pruned schemas;
  emitted by the merge + pruner; existing artifacts/tests remain valid.
* **Depends on:** T6.6

### T6.8 — Scale-out orchestration + bounded ingestion
* **AC:** one CLI/Make target running sources → normalize/dedup → sample → shard
  → `mvs-telemetry` per shard **with T4.2 bounds enabled** → merge → stamp
  provenance → validated `hits.json`. A small end-to-end run (~10⁵, from
  committed fixtures) runs in CI; the full 10⁸ run is documented (crawl
  selection, expected time/disk).
* **Depends on:** T6.5, T6.6, T6.7

## Risks & notes

- **Bias is a choice, not an accident.** The quota weights and per-domain cap are
  the levers; they're documented in the manifest so reviewers can contest them.
- **CC index schema drift.** Pin the crawl id and the `cc-index` schema version;
  the connector should fail loudly on unexpected columns.
- **Throughput on adversarial input.** Ingestion runs with the T4.2 bounds on so
  one pathological URL in 10⁸ can't stall a shard.
- **Not a substitute for the RFE loop.** Even a great corpus under-samples
  private/regional/accessibility usage; the override registry + RFE remain the
  correction mechanism.
