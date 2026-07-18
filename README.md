# RFE-Standards

**Minimum Viable Standard (MVS) & Request for Evidence (RFE) Framework**

Decompose bloated, legacy RFCs (X.509/ASN.1 and RFC 3986 URIs) into usage-driven,
empirical standards. Coverage is dictated by observable real-world usage data plus an
explicit Criticality Override Registry — not design-by-committee.

> **First published spec: [RFE-3986-URI](spec/rfc3986-uri/)** — a Minimum Viable
> Standard for URIs derived from **9,266,505** real-world URIs (Common Crawl +
> Wikipedia). 28 rules, evidence for every one.

## The Pipeline

```
                    Phase 1                Phase 2                 Phase 3               Phase 4
  ┌──────────┐   Grammar→AST   ┌───────┐  Telemetry  ┌──────────┐  Pruner   ┌───────┐  Codegen  ┌──────────┐
  │  RFC 3986 │ ─────────────▶ │  AST  │ ──────────▶ │ Hit-rate │ ────────▶ │  MVS  │ ────────▶ │ Reference │
  │  RFC 5280 │                │ (nodes)│  (corpus)  │ aggregates│  + overrides│ grammar│           │  parsers  │
  └──────────┘                └───────┘             └──────────┘           └───────┘           └──────────┘
                                                          ▲                                          │
                                                          │              Phase 5: RFE               │
                                                          └──────────  (evidence to restore)  ◀──────┘
```

- **Phase 1 — Grammar to AST Mapping:** every grammar rule / ASN.1 tag becomes a uniquely-identified, measurable node.
- **Phase 2 — Telemetry Engine & Corpus Ingestion:** instrumented parsers trace real-world data (CT logs, Common Crawl) against the AST and tally node hit rates.
- **Phase 3 — MVS Pruner:** amputate nodes below an empirical usage threshold (unless protected by the override registry).
- **Phase 4 — Reference Implementations:** auto-generate fast, strict parsers from the minified standard.
- **Phase 5 — Request for Evidence (RFE):** consumers submit corpora to prove a pruned feature is necessary in their domain.

## Stack

Hybrid **Python + Rust** (see [ADR 0001](docs/adr/0001-technology-stack.md)):

- **Rust** — the throughput-critical instrumented parser core (Phase 2) and the
  **native, cross-platform** reference parsers (Phase 4, via `nom`). These are the
  shipped deliverables: standalone binaries for Linux/macOS/Windows, no runtime.
- **Python** — dev-time/server-side tooling only: AST extraction, corpus
  ingestion, the pruner, and the RFE service. Ships nothing to consumers.

Phases communicate through a **JSON artifact spine** (AST, `hits.json`,
`overrides.yaml`, `pruned.json`), which is the language boundary — no FFI.

## Layout

```
core/          Rust workspace — instrumented parser core + native reference parsers
  crates/mvs-core/       node-hit telemetry primitives (Task 2.1)
  crates/mvs-refparse/   reference parsers + strict failure model (Phase 4)
pipeline/      Python — AST extraction, corpus ingestion, pruner (Phases 1–3)
rfe-service/   Python — RFE webhook + telemetry re-run (Phase 5)
schemas/       JSON/YAML artifact contracts (Task 0.2)
docs/          plan, tickets, ADRs
```

## Developing

```bash
# Rust
cd core && cargo fmt --all --check && cargo clippy --all-targets --all-features && cargo test --all

# Python (from repo root)
pip install ".[dev]"
ruff check . && ruff format --check . && pytest
```

CI (`.github/workflows/ci.yml`) runs the same lint/test on every push and PR, plus a
cross-compilation matrix that builds the Rust workspace for Linux/macOS/Windows on
`x86_64` + `aarch64` — enforcing the native/cross-platform constraint from ADR 0001.

## Documents

- [`docs/PLAN.md`](docs/PLAN.md) — the architectural source of truth. Phases are Epics; Tasks are tickets.
- [`docs/TICKETS.md`](docs/TICKETS.md) — the ticket breakdown with dependencies.
- [`docs/adr/0001-technology-stack.md`](docs/adr/0001-technology-stack.md) — stack decision.
- [`docs/telemetry.md`](docs/telemetry.md) — corpus → hits pipeline (URI text + X.509 DER).
- [`docs/CORPUS-PLAN.md`](docs/CORPUS-PLAN.md) — plan for a free, representative 10⁸-URI corpus.
- [`docs/collector.md`](docs/collector.md) — the free-source collector (Epic 6): sources → sample → bounded telemetry → hits.
- [`docs/overrides.md`](docs/overrides.md) — the Criticality Override Registry.
- [`docs/rfe.md`](docs/rfe.md) — the submit → re-run → auto-PR RFE loop.

## Status

**All phases complete.** The end-to-end pipeline runs:

| Phase | What ships |
| --- | --- |
| 0 — Foundations | Monorepo, CI + cross-compile matrix, frozen JSON artifact contracts |
| 1 — Grammar → AST | ABNF (RFC 3986) & ASN.1 (RFC 5280) front-ends → node-keyed ASTs |
| 2 — Telemetry | Instrumented recognizer + DER walker; URI (Common Crawl) & X.509 (CT) ingestors → `hits.json`; override registry |
| 3 — Pruner | Usage-threshold pruning (+ overrides) → `pruned.json` → minified, valid ABNF/ASN.1 |
| 4 — Reference impls | Native `mvs-validate` parsers accepting exactly the MVS, with strict `ERR_MVS_*` failure states |
| 5 — RFE | Submission webhook → re-run vs full AST → auto-`overrides.yaml` PR when the data clears the threshold |

Run `cd core && cargo test --all` and `pytest` for the full suite (Rust + Python).
