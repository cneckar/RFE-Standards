# Criticality Override Registry

The MVS pruner (Phase 3) removes any grammar/schema node whose usage in the
telemetry corpus falls below `MIN_USAGE_PERCENTAGE`. That is the point of the
project — but pure usage-driven pruning would happily delete features that are
rare *and* load-bearing. The **Criticality Override Registry**
(`pipeline/mvs_pipeline/overrides.yaml`, vendored in the package so it resolves
under any install layout) is the deliberate, auditable escape hatch: nodes listed
there with `protected: true` are never pruned, regardless of usage.

## Schema

Validated against [`schemas/overrides.schema.json`](../schemas/overrides.schema.json)
(frozen in Task 0.2). Each entry is keyed by a stable node id from a Phase-1 AST
artifact (`artifacts/*.ast.json`) and requires:

| field | requirement |
| --- | --- |
| `protected` | boolean; `true` keeps the node regardless of usage |
| `justification` | **non-empty string** — why this node must survive |
| `owner` | person or team accountable for the entry |

```yaml
schema_version: 1
overrides:
  "rfc3986-uri:pct-encoded#5b00e532":
    protected: true
    justification: >-
      Security-critical: percent-encoding must be parsed to correctly normalize
      and reject encoded traversal (%2e%2e) and injection payloads.
    owner: security-wg
```

## The justification requirement

An override is a claim that the empirical data is *wrong* about this node — that
it matters more than its hit rate suggests. That claim must be written down:

- **`justification` is mandatory and non-empty.** The schema rejects an override
  without one, so an unexplained protection cannot be merged or loaded. Loading
  the registry (`mvs_pipeline.overrides.load_overrides`) schema-validates it and
  raises on any violation.
- **State the harm of removal, not just the feature.** "Rarely used" is never a
  justification; "removing it turns a security control into a silent bypass" is.
  Good justifications cite a concrete failure mode: a security bypass, a safety
  or accessibility regression, or a protocol-compliance break.
- **Name an `owner`.** Overrides are reviewable debt. The owner is who to ask
  when revisiting whether the protection is still warranted.

## How it is used

- **Phase 3 (pruner):** reads `overrides.yaml`; a node that is below threshold
  **and** protected is kept, not pruned.
- **Phase 5 (RFE):** when a consumer submits evidence that a pruned node is
  needed in their domain, the telemetry re-run can open a PR adding a justified
  entry here — the same requirements apply to machine-proposed overrides.

Keep the registry small. Every entry is a place where the standard is *not*
following the data, so each one should be defensible on its own.
