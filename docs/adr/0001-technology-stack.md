# ADR 0001: Technology Stack

- **Status:** Accepted
- **Date:** 2026-07-14

## Context

The MVS/RFE framework spans two very different kinds of work:

1. **Corpus-scale, throughput-critical parsing** — the instrumented parser core
   (Task 2.1) runs against hundreds of millions of certificates (CT logs) and URIs
   (Common Crawl). This is the hot path.
2. **Glue, orchestration, and ingestion** — CT log clients, Common Crawl extract
   handling, the pruner decision logic, and the RFE validation webhook. This is
   iteration-heavy and ecosystem-dependent.

Additionally, the **reference implementations produced in Phase 4 must be native
and cross-platform** — they are the deliverable consumers actually run, so they
must compile to standalone binaries on Linux, macOS, and Windows with no
interpreter or VM dependency.

## Decision

Adopt a **hybrid Python + Rust** stack:

| Concern | Language | Rationale |
| --- | --- | --- |
| Instrumented parser core (Task 2.1) | **Rust** | Throughput at corpus scale; zero-cost node-hit instrumentation. |
| Reference parsers (Phase 4) | **Rust** (`nom`) | **Native, cross-platform** binaries — the shipped deliverable. |
| AST extraction (Phase 1) | **Python** | Fast iteration; `abnf`, `asn1crypto` ecosystems. Emits language-neutral JSON. |
| Corpus ingestion (Tasks 2.2, 2.3) | **Python** | Rich CT-log / Common Crawl / HTTP ecosystem. |
| Pruner (Phase 3) | **Python** | Pure data transform over JSON artifacts. |
| RFE service (Phase 5) | **Python** (FastAPI) | Webhook + PR automation glue. |

The **JSON artifact spine** (AST, `hits.json`, `overrides.yaml`, `pruned.json`)
is the language boundary. Python produces and consumes these files; the Rust
core consumes the AST and emits hit maps. No FFI is required — phases communicate
through serialized artifacts, keeping the boundary clean and independently
testable.

## Constraints

- **Native + cross-platform for shipped artifacts.** Phase 4 reference parsers
  MUST build for `x86_64`/`aarch64` on Linux, macOS, and Windows. CI must prove
  this with a cross-compilation matrix. No shipped component may require a Python
  runtime on the consumer's machine.
- Python components are dev-time/server-side tooling only.

## Consequences

- Two toolchains (Cargo + a Python package manager) and a CI matrix that covers both.
- The AST node-ID contract (Task 0.2) becomes load-bearing — it is the interface
  between the Python and Rust halves. It must be specified and frozen early.
- Phase 4's `nom` choice from the plan is confirmed rather than left open.
