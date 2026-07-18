"""The published spec under spec/ must stay reproducible from its own evidence.

The spec's ``pruned.json`` and ``.mvs.abnf`` are downstream of ``spec/hits.json``
(the 9.3M-URI corpus aggregate) and the override registry. This guards against
silent drift: if someone edits the grammar, the overrides, or the artifacts by
hand, the spec no longer matches its evidence and CI fails.
"""

from __future__ import annotations

from pathlib import Path

from mvs_pipeline import codegen, pruner, schema
from mvs_pipeline import overrides as overrides_mod
from mvs_pipeline.schema import load_document

ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / "spec" / "rfc3986-uri"
AST = ROOT / "artifacts" / "rfc3986-uri.ast.json"

SURVIVING = "spec/rfc3986-uri/rfc3986-uri.mvs.abnf"


def test_spec_artifacts_are_schema_valid() -> None:
    schema.validate("hits", load_document(SPEC / "hits.json"))
    schema.validate("pruned", load_document(SPEC / "pruned.json"))


def test_spec_pruned_reproduces_from_hits_and_overrides() -> None:
    ast = load_document(AST)
    hits = load_document(SPEC / "hits.json")
    overrides = overrides_mod.load_overrides()
    fresh = pruner.prune(ast, hits, overrides, surviving_grammar=SURVIVING)
    committed = load_document(SPEC / "pruned.json")
    assert committed == fresh, "spec/pruned.json is stale vs spec/hits.json + overrides"


def test_spec_grammar_reproduces_from_pruned() -> None:
    ast = load_document(AST)
    pruned = load_document(SPEC / "pruned.json")
    assert (SPEC / "rfc3986-uri.mvs.abnf").read_text() == codegen.generate(ast, pruned)
