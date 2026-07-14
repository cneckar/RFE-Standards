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

## Documents

- [`docs/PLAN.md`](docs/PLAN.md) — the architectural source of truth. Phases are Epics; Tasks are tickets.

## Status

Planning. See `docs/PLAN.md` for the full phase/task breakdown. Implementation
tickets are being decomposed from the plan.
