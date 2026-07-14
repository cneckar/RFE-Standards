# schemas/

The **artifact spine** — the JSON/YAML contracts every phase reads and writes,
and the boundary between the Python and Rust halves (see
[ADR 0001](../docs/adr/0001-technology-stack.md)). Frozen in **Task 0.2** (#8).

## Contracts (JSON Schema, draft 2020-12)

| File | Artifact | Produced by | Consumed by |
| --- | --- | --- | --- |
| `ast.schema.json` | AST nodes with stable `node_id`s | Phase 1 | 2, 3, 5 |
| `hits.schema.json` | `node_id → count` + `total_samples` | Phase 2 | 3, 5 |
| `overrides.schema.json` | Criticality Override Registry (`overrides.yaml`) | Task 2.4 | 3, 5 |
| `pruned.schema.json` | removed `node_id`s + surviving-grammar reference | Phase 3 | 4 |

## Node identity

Every node is keyed by a stable `node_id`:

```
<grammar>:<rule-path>#<hash8>
        e.g.  rfc3986-uri:pct-encoded#6a7b8c9d
```

`hash8` is a hex digest of the node's **structural path**, so repeated
occurrences of the same rule at different positions get distinct ids. All four
schemas validate `node_id`s against the same pattern, which is what lets the
Python tooling and the Rust core agree on a node without any FFI.

## Examples (`examples/`)

A single coherent RFC 3986 scenario used as fixtures by both test suites:
`uri.ast.json`, `uri.hits.json`, `overrides.yaml`, `uri.pruned.json`. In it
`userinfo` falls below the 0.1% threshold and is pruned, while `pct-encoded` is
also rare but survives because it is `protected` in the override registry.
`examples/invalid/` holds intentionally-broken fixtures for negative tests.

## Validation

- **Python** (authoritative JSON Schema validation): `mvs_pipeline.schema` +
  `pipeline/tests/test_schemas.py`.
- **Rust** (typed round-trip + structural invariants): the `mvs-schema` crate.
