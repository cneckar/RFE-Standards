# RFE-Standards

**Minimum Viable Standard (MVS) & Request for Evidence (RFE) Framework**

Decompose bloated, legacy RFCs (X.509/ASN.1 and RFC 3986 URIs) into usage-driven,
empirical standards. Coverage is dictated by observable real-world usage data plus an
explicit Criticality Override Registry — not design-by-committee.

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
- [`docs/TICKETS.md`](docs/TICKETS.md) — the filable ticket breakdown with dependencies.
- [`docs/adr/0001-technology-stack.md`](docs/adr/0001-technology-stack.md) — stack decision.

## Status

Scaffolding complete (T0.1). Next: T0.2 — shared artifact schemas.
