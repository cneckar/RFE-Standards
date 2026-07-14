import json
from pathlib import Path

import pytest

from mvs_pipeline import schema
from mvs_pipeline.asn1 import (
    Asn1SyntaxError,
    build_ast,
    build_rfc5280_x509_ast,
    parse_rules,
)

ARTIFACT = Path(__file__).resolve().parents[2] / "artifacts" / "rfc5280-x509.ast.json"


def _rule_body(ast, rule_name):
    rule_id = next(
        nid for nid, n in ast["nodes"].items() if n["kind"] == "rule" and n["name"] == rule_name
    )
    return ast["nodes"][ast["nodes"][rule_id]["children"][0]]


def _kind_counts(ast):
    counts: dict[str, int] = {}
    for node in ast["nodes"].values():
        counts[node["kind"]] = counts.get(node["kind"], 0) + 1
    return counts


# --- small-module unit tests ---------------------------------------------- #


def _mod(body: str) -> str:
    return f"M DEFINITIONS ::= BEGIN\n{body}\nEND"


def test_sequence_of_named_fields():
    ast = build_ast("g", _mod("T ::= SEQUENCE { a INTEGER, b BOOLEAN }"))
    body = _rule_body(ast, "T")
    assert body["kind"] == "sequence"
    field_kinds = [ast["nodes"][c]["kind"] for c in body["children"]]
    assert field_kinds == ["named-type", "named-type"]


def test_choice_is_alternation():
    ast = build_ast("g", _mod("T ::= CHOICE { a INTEGER, b BOOLEAN }"))
    assert _rule_body(ast, "T")["kind"] == "alternation"


def test_sequence_of_is_repetition():
    ast = build_ast("g", _mod("T ::= SEQUENCE OF INTEGER"))
    assert _rule_body(ast, "T")["kind"] == "repetition"


def test_context_tag_becomes_tag_node():
    ast = build_ast("g", _mod("T ::= SEQUENCE { v [0] EXPLICIT INTEGER }"))
    named = ast["nodes"][_rule_body(ast, "T")["children"][0]]
    tagnode = ast["nodes"][named["children"][0]]
    assert tagnode["kind"] == "tag"
    assert tagnode["name"] == "[0] EXPLICIT"


def test_optional_and_default_wrap_in_optional():
    ast = build_ast(
        "g",
        _mod("T ::= SEQUENCE { a INTEGER OPTIONAL, b BOOLEAN DEFAULT FALSE }"),
    )
    fields = [ast["nodes"][c] for c in _rule_body(ast, "T")["children"]]
    wrapped = [ast["nodes"][f["children"][0]]["kind"] for f in fields]
    assert wrapped == ["optional", "optional"]


def test_string_type_node():
    ast = build_ast("g", _mod("T ::= UTF8String"))
    body = _rule_body(ast, "T")
    assert body["kind"] == "string-type"
    assert body["name"] == "UTF8String"


def test_comment_to_end_of_line():
    ast = build_ast("g", _mod("T ::= INTEGER  -- comment runs to end of line"))
    assert _rule_body(ast, "T")["kind"] == "terminal"


def test_closed_inline_comment():
    # A "-- ... --" pair is a closed comment; the tokens around it are code.
    ast = build_ast("g", _mod("T ::= SEQUENCE { a -- inline note -- INTEGER }"))
    body = _rule_body(ast, "T")
    assert body["kind"] == "sequence"
    named = ast["nodes"][body["children"][0]]
    assert named["name"] == "a"
    assert ast["nodes"][named["children"][0]]["kind"] == "terminal"


def test_syntax_error_on_missing_begin():
    with pytest.raises(Asn1SyntaxError):
        parse_rules("T ::= INTEGER")  # no module / BEGIN


# --- RFC 5280 integration -------------------------------------------------- #


def test_rfc5280_ast_is_schema_valid():
    ast = build_rfc5280_x509_ast()
    schema.validate("ast", ast)
    assert ast["grammar"] == "rfc5280-x509"
    assert ast["nodes"][ast["root"]]["name"] == "Certificate"


def test_rfc5280_expected_types_present():
    ast = build_rfc5280_x509_ast()
    rule_names = {n["name"] for n in ast["nodes"].values() if n["kind"] == "rule"}
    for expected in [
        "Certificate",
        "TBSCertificate",
        "Version",
        "AlgorithmIdentifier",
        "Name",
        "DirectoryString",
        "Validity",
        "SubjectPublicKeyInfo",
        "Extensions",
        "Extension",
    ]:
        assert expected in rule_names


def test_rfc5280_tags_string_types_and_optionals_identified():
    ast = build_rfc5280_x509_ast()
    counts = _kind_counts(ast)
    # [0] version, [1] issuerUniqueID, [2] subjectUniqueID, [3] extensions
    assert counts["tag"] == 4
    # DirectoryString's five string alternatives
    assert counts["string-type"] == 5
    # Four tagged/plain OPTIONAL + two DEFAULT fields
    assert counts["optional"] == 6


def test_rfc5280_all_references_resolve():
    ast = build_rfc5280_x509_ast()
    rule_names = {n["name"] for n in ast["nodes"].values() if n["kind"] == "rule"}
    refs = {n["name"] for n in ast["nodes"].values() if n["kind"] == "reference"}
    assert refs <= rule_names, f"unresolved references: {sorted(refs - rule_names)}"


def test_rfc5280_node_ids_well_formed_and_internal():
    ast = build_rfc5280_x509_ast()
    node_ids = set(ast["nodes"])
    for nid, node in ast["nodes"].items():
        assert nid.startswith("rfc5280-x509:")
        for child in node.get("children", []):
            assert child in node_ids


def test_committed_artifact_is_reproducible():
    committed = json.loads(ARTIFACT.read_text())
    assert committed == build_rfc5280_x509_ast()
