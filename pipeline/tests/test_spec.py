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


def _abnf_block(md: str) -> str:
    """Extract the first ```abnf fenced block from a Markdown document."""
    start = md.index("```abnf\n") + len("```abnf\n")
    end = md.index("\n```", start)
    return md[start:end]


def test_rfe3986_doc_embeds_generated_grammar_verbatim() -> None:
    # The normative grammar in the prose spec MUST be the generated grammar, not a
    # hand-maintained copy that can drift from the evidence.
    doc = (SPEC / "RFE-3986.md").read_text()
    grammar = (SPEC / "rfc3986-uri.mvs.abnf").read_text()
    assert _abnf_block(doc).strip() == grammar.strip(), (
        "RFE-3986.md's normative grammar block is out of sync with rfc3986-uri.mvs.abnf"
    )


def test_rfe3986_doc_documents_every_override() -> None:
    # Every protected production must be named in the prose spec, so the security
    # floor can't be silently dropped from the standard's text.
    doc = (SPEC / "RFE-3986.md").read_text()
    ast = load_document(AST)["nodes"]
    overrides = overrides_mod.load_overrides()
    uri_overrides = [
        n for n in overrides_mod.protected_nodes(overrides) if n.startswith("rfc3986-uri:")
    ]
    assert uri_overrides, "expected some rfc3986-uri overrides"
    for nid in uri_overrides:
        name = ast[nid]["name"].strip('"')
        assert name in doc, f"override-protected production {name!r} is undocumented in RFE-3986.md"
