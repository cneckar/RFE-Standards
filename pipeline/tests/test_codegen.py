from pathlib import Path

import pytest

from mvs_pipeline import abnf, asn1, codegen
from mvs_pipeline.schema import load_document

ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / "artifacts"
MVS = ROOT / "mvs"


def _load(grammar):
    ast = load_document(ARTIFACTS / f"{grammar}.ast.json")
    pruned = load_document(ARTIFACTS / f"{grammar}.pruned.json")
    return ast, pruned


def test_format_detection():
    uri_ast, _ = _load("rfc3986-uri")
    x509_ast, _ = _load("rfc5280-x509")
    assert codegen._detect_format(uri_ast) == "abnf"
    assert codegen._detect_format(x509_ast) == "asn1"


def test_grammar_mismatch_raises():
    uri_ast, _ = _load("rfc3986-uri")
    _, x509_pruned = _load("rfc5280-x509")
    with pytest.raises(ValueError):
        codegen.generate(uri_ast, x509_pruned)


# --- minified ABNF --------------------------------------------------------- #


def test_minified_abnf_reparses_and_is_a_subset():
    ast, pruned = _load("rfc3986-uri")
    text = codegen.generate(ast, pruned)

    rules = abnf.parse_rules(text)  # must be syntactically valid ABNF
    regenerated = {r.name for r in rules}

    original = {n["name"] for n in ast["nodes"].values() if n["kind"] == "rule"}
    pruned_ids = set(pruned["pruned"])
    surviving = {
        n["name"]
        for nid, n in ast["nodes"].items()
        if n["kind"] == "rule" and nid not in pruned_ids
    }

    assert regenerated <= original
    assert regenerated == surviving
    assert "URI" in regenerated


def test_committed_minified_abnf_reproducible():
    ast, pruned = _load("rfc3986-uri")
    assert (MVS / "rfc3986-uri.mvs.abnf").read_text() == codegen.generate(ast, pruned)


# --- minified ASN.1 -------------------------------------------------------- #


def test_minified_asn1_reparses():
    ast, pruned = _load("rfc5280-x509")
    text = codegen.generate(ast, pruned)

    rules = asn1.parse_rules(text)  # must be syntactically valid ASN.1
    regenerated = {r.name for r in rules}
    assert "Certificate" in regenerated
    assert "TBSCertificate" in regenerated

    surviving = {
        n["name"]
        for nid, n in ast["nodes"].items()
        if n["kind"] == "rule" and nid not in set(pruned["pruned"])
    }
    assert regenerated == surviving


def test_committed_minified_asn1_reproducible():
    ast, pruned = _load("rfc5280-x509")
    assert (MVS / "rfc5280-x509.mvs.asn1").read_text() == codegen.generate(ast, pruned)
