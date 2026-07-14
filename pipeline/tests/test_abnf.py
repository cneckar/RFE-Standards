import json
from pathlib import Path

import pytest

from mvs_pipeline import schema
from mvs_pipeline.abnf import (
    AbnfSyntaxError,
    build_ast,
    build_rfc3986_uri_ast,
    parse_rules,
)

ARTIFACT = Path(__file__).resolve().parents[2] / "artifacts" / "rfc3986-uri.ast.json"


def _kinds(ast):
    return {nid: node["kind"] for nid, node in ast["nodes"].items()}


def _rule_body(ast, rule_name):
    """Return the single body node of a named rule."""
    rule_id = next(
        nid for nid, n in ast["nodes"].items() if n["kind"] == "rule" and n["name"] == rule_name
    )
    body_id = ast["nodes"][rule_id]["children"][0]
    return ast["nodes"][body_id]


# --- small-grammar unit tests --------------------------------------------- #


def test_alternation_of_references():
    ast = build_ast("g", 'a = b / c\nb = "x"\nc = "y"')
    body = _rule_body(ast, "a")
    assert body["kind"] == "alternation"
    child_kinds = [ast["nodes"][c]["kind"] for c in body["children"]]
    assert child_kinds == ["reference", "reference"]


def test_sequence():
    ast = build_ast("g", 'a = b c\nb = "x"\nc = "y"')
    body = _rule_body(ast, "a")
    assert body["kind"] == "sequence"
    assert len(body["children"]) == 2


def test_repetition():
    ast = build_ast("g", 'a = 1*4b\nb = "x"')
    body = _rule_body(ast, "a")
    assert body["kind"] == "repetition"
    assert len(body["children"]) == 1


def test_optional_and_group():
    ast = build_ast("g", 'a = [ b ] ( b b )\nb = "x"')
    body = _rule_body(ast, "a")
    assert body["kind"] == "sequence"
    kinds = [ast["nodes"][c]["kind"] for c in body["children"]]
    assert kinds == ["optional", "group"]


def test_num_val_range():
    ast = build_ast("g", "a = %x30-39")
    body = _rule_body(ast, "a")
    assert body["kind"] == "terminal"
    assert body["name"] == "%x30-39"


def test_prose_val_with_zero_repeat():
    ast = build_ast("g", "a = 0<pchar>")
    body = _rule_body(ast, "a")
    assert body["kind"] == "repetition"
    inner = ast["nodes"][body["children"][0]]
    assert inner["kind"] == "terminal"
    assert inner["name"] == "<pchar>"


def test_incremental_alternative():
    ast = build_ast("g", 'a = b\na =/ c\nb = "x"\nc = "y"')
    body = _rule_body(ast, "a")
    assert body["kind"] == "alternation"
    assert len(body["children"]) == 2


def test_semicolon_in_char_val_is_not_a_comment():
    ast = build_ast("g", 'a = ";"  ; trailing comment is stripped')
    body = _rule_body(ast, "a")
    assert body["kind"] == "terminal"
    assert body["name"] == '";"'


def test_syntax_errors():
    with pytest.raises(AbnfSyntaxError):
        parse_rules("a = ( b")  # unclosed group
    with pytest.raises(AbnfSyntaxError):
        parse_rules("a =/ b")  # increment with no base rule


# --- RFC 3986 integration -------------------------------------------------- #


def test_rfc3986_ast_is_schema_valid():
    ast = build_rfc3986_uri_ast()
    schema.validate("ast", ast)
    assert ast["grammar"] == "rfc3986-uri"
    assert ast["root"] in ast["nodes"]
    assert ast["nodes"][ast["root"]]["name"] == "URI"


def test_rfc3986_expected_rules_present():
    ast = build_rfc3986_uri_ast()
    rule_names = {n["name"] for n in ast["nodes"].values() if n["kind"] == "rule"}
    for expected in [
        "URI",
        "scheme",
        "authority",
        "userinfo",
        "host",
        "port",
        "pct-encoded",
        "IPv6address",
        "reg-name",
        "sub-delims",
        "ALPHA",
        "DIGIT",
        "HEXDIG",
    ]:
        assert expected in rule_names


def test_rfc3986_all_references_resolve():
    ast = build_rfc3986_uri_ast()
    rule_names = {n["name"] for n in ast["nodes"].values() if n["kind"] == "rule"}
    refs = {n["name"] for n in ast["nodes"].values() if n["kind"] == "reference"}
    assert refs <= rule_names, f"unresolved references: {sorted(refs - rule_names)}"


def test_rfc3986_node_ids_unique_and_well_formed():
    ast = build_rfc3986_uri_ast()
    # dict keys are inherently unique; assert the id shape and internal references.
    node_ids = set(ast["nodes"])
    for nid, node in ast["nodes"].items():
        assert nid.startswith("rfc3986-uri:")
        for child in node.get("children", []):
            assert child in node_ids


def test_committed_artifact_is_reproducible():
    committed = json.loads(ARTIFACT.read_text())
    assert committed == build_rfc3986_uri_ast()
