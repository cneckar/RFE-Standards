# Ticket Breakdown

Decomposition of `docs/PLAN.md` into filable work items. Each **Phase** is an
**Epic**; each **Task** is an **issue** carrying its acceptance criteria as a
checklist. Two foundational tickets (T0.x) are added ahead of Phase 1 because
every downstream phase depends on them.

Stack per [ADR 0001](adr/0001-technology-stack.md): hybrid Python + Rust, with
native cross-platform reference parsers in Phase 4.

## Dependency graph

```
T0.1 ─┬─▶ T0.2 ─┬─▶ 1.1 ─┐
      │         └─▶ 1.2 ─┤
      │                  ├─▶ 2.1 ─┬─▶ 2.2 ─┐
      │                  │        └─▶ 2.3 ─┤
      └──────────────────┴─▶ 2.4 ─────────┼─▶ 3.1 ─▶ 3.2 ─▶ 4.1 ─▶ 4.2
                                          │                          │
                                          └─────────▶ 5.1 ─▶ 5.2 ◀───┘
```

---

## Epic 0 — Foundations (not in original plan; prerequisite)

### T0.1 — Repo scaffolding & CI
* **Why:** Nothing else can be built or verified without the workspace + pipelines.
* **Acceptance Criteria:**
  - [ ] Monorepo layout: `core/` (Rust workspace), `pipeline/` (Python), `rfe-service/` (Python), `schemas/`, `docs/`.
  - [ ] Rust workspace with `cargo` + `clippy` + `cargo fmt`.
  - [ ] Python project with lint (`ruff`), format, and test (`pytest`) config.
  - [ ] CI runs Rust + Python lint/test on every push.
  - [ ] **CI cross-compilation matrix** proving Rust artifacts build for Linux/macOS/Windows on `x86_64` + `aarch64`.
* **Depends on:** —

### T0.2 — Shared artifact schemas (the contract)
* **Why:** The AST node-ID scheme is the interface between the Python and Rust halves; freezing it de-risks all parallel work.
* **Acceptance Criteria:**
  - [ ] JSON Schema for the **AST** (stable `node_id` = rule name + structural path hash; children; source spans).
  - [ ] JSON Schema for `hits.json` (`node_id → count` + total-samples denominator).
  - [ ] Schema for `overrides.yaml` (`node_id → {protected, justification, owner}`).
  - [ ] JSON Schema for `pruned.json` (removed IDs + surviving grammar reference).
  - [ ] Round-trip fixtures + validation tests in both Python and Rust.
* **Depends on:** T0.1

---

## Epic 1 — Grammar to AST Mapping  *(Phase 1)*

### T1.1 — URI ABNF to AST (RFC 3986)
* **Acceptance Criteria:**
  - [ ] ABNF parser (existing `abnf` lib or vendored) integrated.
  - [ ] RFC 3986 ABNF ingested.
  - [ ] Serialized AST (JSON) where every rule (`host`, `userinfo`, `pct-encoded`, …) has a unique node ID conforming to T0.2.
* **Depends on:** T0.2

### T1.2 — ASN.1 to Schema AST (X.509 / RFC 5280)
* **Acceptance Criteria:**
  - [ ] ASN.1 compiler/parser integrated (`asn1crypto`-backed or a dedicated module parser).
  - [ ] RFC 5280 modules ingested.
  - [ ] Serialized AST where every tag, string type, and OPTIONAL sequence is uniquely identified per T0.2.
* **Depends on:** T0.2

---

## Epic 2 — Telemetry Engine & Corpus Ingestion  *(Phase 2)*

### T2.1 — Instrumented parser core (Rust)
* **Acceptance Criteria:**
  - [ ] Takes an input string/buffer + target AST; returns parsed object **and** the traversed node-ID set/map.
  - [ ] Zero-cost-ish instrumentation; benchmarked for throughput.
  - [ ] Emits `hits.json` conforming to T0.2.
* **Depends on:** T1.1, T1.2

### T2.2 — Certificate Transparency (CT) log ingestor (Python)
* **Acceptance Criteria:**
  - [ ] Connects to public CT endpoints or downloads static datasets.
  - [ ] Pipes DER payloads into the instrumented X.509 parser.
  - [ ] Aggregates node hit rates globally into `hits.json`.
* **Depends on:** T2.1

### T2.3 — Common Crawl / URI corpus ingestor (Python)
* **Acceptance Criteria:**
  - [ ] Ingests URI lists (Common Crawl extracts).
  - [ ] Pipes URIs into the instrumented URI parser.
  - [ ] Aggregates node hit rates globally into `hits.json`.
* **Depends on:** T2.1

### T2.4 — Criticality Override Registry
* **Acceptance Criteria:**
  - [ ] `overrides.yaml` schema allowing `protected: true` per node.
  - [ ] Documented justification requirement per override.
* **Depends on:** T0.2

---

## Epic 3 — The MVS Pruner  *(Phase 3)*

### T3.1 — Pruning logic engine (Python)
* **Acceptance Criteria:**
  - [ ] Ingests telemetry aggregates + `overrides.yaml`.
  - [ ] Configurable `MIN_USAGE_PERCENTAGE` (default `0.001`).
  - [ ] Outputs `pruned.json` — nodes below threshold AND not protected.
* **Depends on:** T2.2, T2.3, T2.4

### T3.2 — MVS code generator (minified standard)
* **Acceptance Criteria:**
  - [ ] Takes `pruned.json` + original AST.
  - [ ] Emits minified, **valid** `mvs.abnf`.
  - [ ] Emits minified, **valid** `mvs.asn1`.
* **Depends on:** T3.1

---

## Epic 4 — Reference Implementations  *(Phase 4 — native, cross-platform)*

### T4.1 — Parser generator integration (Rust / `nom`)
* **Acceptance Criteria:**
  - [ ] Feeds minified ABNF/ASN.1 into the generator.
  - [ ] Compiles executable **native** parsers.
  - [ ] Cross-platform build verified (Linux/macOS/Windows) per ADR 0001.
* **Depends on:** T3.2

### T4.2 — Strict failure-state handlers
* **Acceptance Criteria:**
  - [ ] Strict bounds checking.
  - [ ] Specific exceptions (e.g. `ERR_MVS_UNSUPPORTED_NODE`) instead of partial recovery / fallback loops.
* **Depends on:** T4.1

---

## Epic 5 — Request for Evidence (RFE) Framework  *(Phase 5)*

### T5.1 — RFE validation webhook (Python / FastAPI)
* **Acceptance Criteria:**
  - [ ] Ingestion schema for user-submitted corpora (e.g. zip of traffic).
  - [ ] Payload format validation.
* **Depends on:** T2.1

### T5.2 — Telemetry re-run pipeline
* **Acceptance Criteria:**
  - [ ] Runs the user dataset against the **Full RFC AST** (not the MVS).
  - [ ] Report: does the dataset push a pruned node over `MIN_USAGE_PERCENTAGE`?
  - [ ] If yes, auto-opens a PR modifying `overrides.yaml` with the data as justification.
* **Depends on:** T5.1, T3.1
