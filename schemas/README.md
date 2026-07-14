# schemas/

The **artifact spine** — the JSON/YAML contracts every phase reads and writes,
and the boundary between the Python and Rust halves (see
[ADR 0001](../docs/adr/0001-technology-stack.md)).

These are defined and frozen in **Task 0.2** (issue #8). Planned files:

- `ast.schema.json` — AST nodes with stable `node_id`s.
- `hits.schema.json` — `node_id → count` telemetry plus the total-samples denominator.
- `overrides.schema.json` — the Criticality Override Registry (`overrides.yaml`).
- `pruned.schema.json` — removed node IDs + the surviving minified grammar.

Until then this directory is intentionally empty.
