# Project: Minimum Viable Standard (MVS) & Request for Evidence (RFE) Framework

**Objective:** Decompose bloated, legacy RFCs (specifically X.509/ASN.1 and RFC 3986 URIs) into usage-driven, empirical standards (MVS).

**Philosophy:** Unapologetically break the long tail. Standard coverage is dictated strictly by observable real-world usage data, supplemented by an explicit Criticality Override Registry, rather than design-by-committee.

## Agent Instructions for Claude Code
1. Parse this document as the primary architectural source of truth.
2. Treat each "Phase" as an Epic.
3. Treat each "Task" as an individual ticket.
4. For each task, review the "Acceptance Criteria" before generating code.
5. Ask for clarification on technology stack choices (e.g., Rust vs. Python) before generating the first scaffolding.

---

## Phase 1: Grammar to AST Mapping
**Goal:** Mechanically convert specification documents into directed graphs where every feature/rule is a distinct, measurable node.

### Task 1.1: URI ABNF to AST (RFC 3986)
* **Context:** We need to parse the ABNF grammar of URIs into a measurable tree.
* **Acceptance Criteria:**
  * Implement an ABNF parser (or use an existing library).
  * Ingest RFC 3986 ABNF definitions.
  * Output a serialized AST (JSON/YAML) where every grammar rule (e.g., `host`, `userinfo`, `pct-encoded`) has a unique node ID.

### Task 1.2: ASN.1 to Schema AST (X.509 / RFC 5280)
* **Context:** We need to parse ASN.1 modules into an abstract schema.
* **Acceptance Criteria:**
  * Implement or integrate an ASN.1 compiler/parser.
  * Ingest RFC 5280 modules.
  * Output a serialized AST where every tag, string type, and optional sequence is uniquely identified.

---

## Phase 2: The Telemetry Engine & Corpus Ingestion
**Goal:** Build instrumented parsers that trace exact execution paths against the ASTs using real-world data, tallying node hit rates.

### Task 2.1: Instrumented Parser Core
* **Context:** A parser wrapper that maps successful execution paths back to the AST node IDs generated in Phase 1.
* **Acceptance Criteria:**
  * Takes an input string/buffer and the target AST.
  * Returns the parsed object AND an array/map of AST node IDs traversed.
  * Highly optimized for throughput.

### Task 2.2: Certificate Transparency (CT) Log Ingestor
* **Context:** The corpus for X.509.
* **Acceptance Criteria:**
  * Connect to public CT log endpoints (or download static datasets).
  * Pipe certificate DER payloads into the instrumented X.509 parser.
  * Aggregate node hit rates globally.

### Task 2.3: Common Crawl / URI Corpus Ingestor
* **Context:** The corpus for URIs.
* **Acceptance Criteria:**
  * Ingest standard lists of URIs (e.g., from Common Crawl extracts).
  * Pipe URIs into the instrumented URI parser.
  * Aggregate node hit rates globally.

### Task 2.4: Criticality Override Registry
* **Context:** A configuration layer to manually protect low-usage but critical nodes (e.g., accessibility, security fallbacks).
* **Acceptance Criteria:**
  * Define a `overrides.yaml` schema allowing nodes to be marked as `protected: true`.
  * Document the justification requirement for each override.

---

## Phase 3: The MVS Pruner
**Goal:** Amputate dead or underutilized nodes from the standard based on empirical thresholds.

### Task 3.1: Pruning Logic Engine
* **Context:** The decision matrix for what stays and what goes.
* **Acceptance Criteria:**
  * Ingest the global telemetry aggregates (Phase 2) and `overrides.yaml`.
  * Configurable threshold variable (e.g., `MIN_USAGE_PERCENTAGE = 0.001`).
  * Output a list of pruned node IDs (nodes below threshold AND not protected).

### Task 3.2: MVS Code Generator (Minified Standard)
* **Context:** Recompile the original standard without the pruned nodes.
* **Acceptance Criteria:**
  * Take the pruned node list and the original AST.
  * Generate a minified, valid ABNF file (for URIs).
  * Generate a minified, valid ASN.1 definition file (for X.509).

---

## Phase 4: Reference Implementations
**Goal:** Auto-generate secure, fast reference parsers based strictly on the MVS definitions.

### Task 4.1: Parser Generator Integration
* **Context:** We bypass writing manual parsers.
* **Acceptance Criteria:**
  * Feed the minified ABNF/ASN.1 into a parser generator tool (e.g., `nom` for Rust, or Kaitai Struct).
  * Compile executable parsers in the target language.

### Task 4.2: Strict Failure State Handlers
* **Context:** The MVS must fail cleanly when encountering legacy bloated formats.
* **Acceptance Criteria:**
  * Implement strict bounds checking.
  * Define and throw specific exceptions (e.g., `ERR_MVS_UNSUPPORTED_NODE`) instead of attempting partial state recovery or endless fallback loops.

---

## Phase 5: Request for Evidence (RFE) Framework
**Goal:** Shift the burden of proof to consumers. Allow them to submit data proving a removed feature is necessary in their domain.

### Task 5.1: RFE Validation Webhook
* **Context:** An API or CI/CD action to accept user-submitted corpora.
* **Acceptance Criteria:**
  * Define an ingestion schema for users to upload datasets (e.g., a zip of standard traffic).
  * Validate payload formatting.

### Task 5.2: Telemetry Re-run Pipeline
* **Context:** Automatically evaluate RFE submissions.
* **Acceptance Criteria:**
  * Run the user's dataset against the *Full RFC AST* (not the MVS).
  * Generate a report: Does this dataset provide enough hits to push a pruned node over the `MIN_USAGE_PERCENTAGE` threshold?
  * If yes, automatically open a PR modifying `overrides.yaml` with the new data as justification.

---

## Open Investigations (backlog)
**Goal:** Ideas surfaced from real runs that are worth doing but not yet scoped into a phase.

### INV-1: Feature-usage reporting in the reference implementation
* **Context:** A corpus can never exercise every valid-but-rare feature (userinfo,
  IPv6 literals) — those are kept by the Criticality Override Registry rather than
  by observed hits. It would be valuable for a generated reference parser to
  *report* which such features it actually exercised in production, closing the
  loop back to the registry (promote/retire overrides from live evidence).
* **Open questions / why it's parked:** likely non-trivial — the emitted parser
  would need lightweight, low-overhead instrumentation (which MVS nodes fired)
  and a way to aggregate it safely without leaking parsed content. Investigate
  feasibility and overhead before committing to a phase.

### INV-2: Protection depth for structural machinery of a kept feature
* **Context:** Transitive protection (Task 3.1) keeps a protected rule's subtree
  and force-keeps *below-threshold* rules it reaches, so features like `IP-literal`
  render fully. But a reachable rule that has some incidental usage above the
  threshold (e.g. `dec-octet` on a corpus where octets happen to cluster) is left
  to normal pruning and can render *narrower* than the RFC (accepting only part of
  0–255). Harmless on a real page-URL corpus (those rules sit far below threshold
  and are force-kept in full), but worth a decision: should a protected feature's
  private machinery always be kept in full regardless of incidental usage?

