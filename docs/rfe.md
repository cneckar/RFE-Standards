# Request for Evidence (RFE)

The RFE framework shifts the burden of proof to consumers. When the pruner
removes a feature that a particular domain actually depends on, that domain can
submit a corpus proving it — and the standard restores the feature by protecting
its node in `overrides.yaml`.

```
 consumer          webhook (T5.1)         re-run (T5.2)                overrides PR
 ┌────────┐  zip   ┌──────────────┐  ok   ┌──────────────────────┐  evidence  ┌───────────────┐
 │ corpus  │ ────▶ │ /rfe/submit  │ ────▶ │ run vs FULL RFC AST   │ ─────────▶ │ overrides.yaml │
 │ + why   │       │ validate fmt │       │ threshold report      │            │ PR (auto)      │
 └────────┘        └──────────────┘       └──────────────────────┘            └───────────────┘
        rfe_service.ingest              rfe_service.rerun            git branch + PR
```

## 1. Submit (`rfe_service.app`, `rfe_service.ingest`)

A submission is a zip: a `submission.json` manifest
([`schemas/rfe-submission.schema.json`](../schemas/rfe-submission.schema.json))
plus a corpus (`corpus/uris.txt` or `corpus/certs.b64`). The webhook
`POST /rfe/submit` validates the payload formatting and returns a summary or a
`422` with the reason.

## 2. Re-run (`rfe_service.rerun`)

The submitted corpus is run through the native `mvs-telemetry` binary against the
**full** RFC AST — not the MVS — so that pruned nodes can still be exercised.
`evaluate_report` then asks the key question: **does this dataset provide enough
hits to push a pruned node over `MIN_USAGE_PERCENTAGE`?** Every pruned node whose
usage *in the submission* clears the threshold is reported as restorable.

```bash
python -m rfe_service.rerun \
  --submission submission.zip \
  --ast artifacts/rfc3986-uri.ast.json \
  --pruned artifacts/rfc3986-uri.pruned.json \
  --out-report report.json \
  --out-overrides overrides.yaml \
  --out-pr pr.json
```

## 3. Auto-PR

When there is evidence, `apply_to_overrides` produces an updated, schema-valid
override registry — each restored node protected and **justified by the
submission's rationale plus the observed hit rate** — and `pr_metadata` produces
the branch name, title, and body. A thin CI step commits the new `overrides.yaml`
to the `pr.json` branch and opens the pull request:

```bash
git switch -c "$(jq -r .branch pr.json)"
cp overrides.yaml ./overrides.yaml && git commit -am "$(jq -r .title pr.json)"
# open the PR with the emitted title/body (gh, the GitHub API, or an action)
```

The same requirements as any override apply (see [`overrides.md`](overrides.md)):
a machine-proposed protection still carries a written justification and an owner
(`rfe:<submitter>`), so restored features remain reviewable debt rather than
silent re-expansion of the standard.
