"""Every published spec under spec/ must stay reproducible from its own evidence.

A spec's ``pruned.json`` and generated grammar are downstream of its
``hits.json`` (the corpus aggregate) and the override registry, and its prose
document (``RFE-*.md``) embeds the generated grammar verbatim. These tests guard
against silent drift: edit the grammar, the overrides, the artifacts, or the
prose out of step and CI fails. Covers both published specs (URI + X.509), so the
method is protected the same way regardless of grammar.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mvs_pipeline import codegen, pruner, schema
from mvs_pipeline import overrides as overrides_mod
from mvs_pipeline.schema import load_document

ROOT = Path(__file__).resolve().parents[2]


class Spec:
    def __init__(self, grammar: str, spec_dir: str, grammar_file: str, doc: str, lang: str) -> None:
        self.grammar = grammar
        self.dir = ROOT / "spec" / spec_dir
        self.ast = ROOT / "artifacts" / f"{grammar}.ast.json"
        self.grammar_file = self.dir / grammar_file
        self.surviving = f"spec/{spec_dir}/{grammar_file}"
        self.doc = self.dir / doc
        self.lang = lang  # markdown fence language for the embedded grammar block


SPECS = [
    Spec("rfc3986-uri", "rfc3986-uri", "rfc3986-uri.mvs.abnf", "RFE-3986.md", "abnf"),
    Spec("rfc5280-x509", "rfc5280-x509", "rfc5280-x509.mvs.asn1", "RFE-5280.md", "asn1"),
]
_IDS = [s.grammar for s in SPECS]


def _fenced_block(md: str, lang: str) -> str:
    """Extract the first ```<lang> fenced block from a Markdown document."""
    fence = f"```{lang}\n"
    start = md.index(fence) + len(fence)
    end = md.index("\n```", start)
    return md[start:end]


@pytest.mark.parametrize("spec", SPECS, ids=_IDS)
def test_spec_artifacts_are_schema_valid(spec: Spec) -> None:
    schema.validate("hits", load_document(spec.dir / "hits.json"))
    schema.validate("pruned", load_document(spec.dir / "pruned.json"))


@pytest.mark.parametrize("spec", SPECS, ids=_IDS)
def test_spec_pruned_reproduces_from_hits_and_overrides(spec: Spec) -> None:
    ast = load_document(spec.ast)
    hits = load_document(spec.dir / "hits.json")
    overrides = overrides_mod.load_overrides()
    fresh = pruner.prune(ast, hits, overrides, surviving_grammar=spec.surviving)
    committed = load_document(spec.dir / "pruned.json")
    assert committed == fresh, f"{spec.surviving} pruned.json is stale vs hits + overrides"


@pytest.mark.parametrize("spec", SPECS, ids=_IDS)
def test_spec_grammar_reproduces_from_pruned(spec: Spec) -> None:
    ast = load_document(spec.ast)
    pruned = load_document(spec.dir / "pruned.json")
    assert spec.grammar_file.read_text() == codegen.generate(ast, pruned)


@pytest.mark.parametrize("spec", SPECS, ids=_IDS)
def test_doc_embeds_generated_grammar_verbatim(spec: Spec) -> None:
    # The normative grammar in the prose spec MUST be the generated grammar, not a
    # hand-maintained copy that can drift from the evidence.
    doc = spec.doc.read_text()
    grammar = spec.grammar_file.read_text()
    assert _fenced_block(doc, spec.lang).strip() == grammar.strip(), (
        f"{spec.doc.name}'s normative grammar block is out of sync with {spec.grammar_file.name}"
    )


@pytest.mark.parametrize("spec", SPECS, ids=_IDS)
def test_doc_documents_every_override(spec: Spec) -> None:
    # Every protected production for this grammar must be named in its prose spec,
    # so the security floor can't be silently dropped from the standard's text.
    doc = spec.doc.read_text()
    ast = load_document(spec.ast)["nodes"]
    overrides = overrides_mod.load_overrides()
    grammar_overrides = [
        n for n in overrides_mod.protected_nodes(overrides) if n.startswith(f"{spec.grammar}:")
    ]
    assert grammar_overrides, f"expected some {spec.grammar} overrides"
    for nid in grammar_overrides:
        name = ast[nid]["name"].strip('"')
        assert name in doc, (
            f"override-protected production {name!r} undocumented in {spec.doc.name}"
        )
