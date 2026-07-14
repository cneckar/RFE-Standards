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

## Documents

- [`docs/PLAN.md`](docs/PLAN.md) — the architectural source of truth. Phases are Epics; Tasks are tickets.
- [`docs/TICKETS.md`](docs/TICKETS.md) — the filable ticket breakdown with dependencies.
- [`docs/adr/0001-technology-stack.md`](docs/adr/0001-technology-stack.md) — stack decision.

## Status

Planning. Ticket breakdown complete in `docs/TICKETS.md`; issues being filed.
