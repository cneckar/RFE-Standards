from pathlib import Path

import pytest
from jsonschema import ValidationError

import mvs_pipeline
from mvs_pipeline import schema
from mvs_pipeline.schema import load_document, schema_dir

# Example fixtures live at the repo root (shared with the Rust crate); the schema
# definitions themselves are vendored inside the package.
EXAMPLES = Path(__file__).resolve().parents[2] / "schemas" / "examples"

# (artifact kind, fixture filename)
VALID_FIXTURES = [
    ("ast", "uri.ast.json"),
    ("hits", "uri.hits.json"),
    ("overrides", "overrides.yaml"),
    ("pruned", "uri.pruned.json"),
]


def test_schemas_are_vendored_in_the_package():
    """The schema definitions resolve from inside the installed package.

    Guards the packaging fix: ``schema_dir()`` must live under the
    ``mvs_pipeline`` package (adjacent to the module), so a plain ``pip install``
    ships them and resolution never depends on a repo checkout being present.
    """
    pkg_root = Path(mvs_pipeline.__file__).resolve().parent
    assert schema_dir() == pkg_root / "schemas"
    for kind in ("ast", "hits", "overrides", "pruned", "rfe"):
        # Each declared schema is present next to the module and parses.
        assert schema.is_valid(kind, {}) in (True, False)  # loads without FileNotFoundError


@pytest.mark.parametrize(("kind", "filename"), VALID_FIXTURES)
def test_fixture_validates(kind, filename):
    doc = load_document(EXAMPLES / filename)
    schema.validate(kind, doc)  # raises on failure
    assert schema.is_valid(kind, doc)


def test_invalid_override_missing_justification_is_rejected():
    doc = load_document(EXAMPLES / "invalid" / "overrides-missing-justification.yaml")
    assert not schema.is_valid("overrides", doc)
    with pytest.raises(ValidationError):
        schema.validate("overrides", doc)


def test_unknown_kind_raises():
    with pytest.raises(ValueError):
        schema.is_valid("nonsense", {})


def test_bad_node_id_is_rejected():
    doc = {
        "schema_version": 1,
        "grammar": "rfc3986-uri",
        "total_samples": 10,
        "hits": {"not-a-valid-node-id": 3},
    }
    assert not schema.is_valid("hits", doc)


# --- Cross-artifact referential integrity of the example scenario ---------


def _load_scenario():
    ast = load_document(EXAMPLES / "uri.ast.json")
    hits = load_document(EXAMPLES / "uri.hits.json")
    overrides = load_document(EXAMPLES / "overrides.yaml")
    pruned = load_document(EXAMPLES / "uri.pruned.json")
    return ast, hits, overrides, pruned


def test_ast_references_are_internally_consistent():
    ast = load_document(EXAMPLES / "uri.ast.json")
    node_ids = set(ast["nodes"])
    assert ast["root"] in node_ids
    for node in ast["nodes"].values():
        for child in node.get("children", []):
            assert child in node_ids, f"dangling child reference: {child}"


def test_hits_overrides_pruned_reference_known_nodes():
    ast, hits, overrides, pruned = _load_scenario()
    node_ids = set(ast["nodes"])
    assert set(hits["hits"]) <= node_ids
    assert set(overrides["overrides"]) <= node_ids
    assert set(pruned["pruned"]) <= node_ids


def test_pruning_decision_matches_threshold_and_overrides():
    ast, hits, overrides, pruned = _load_scenario()
    total = hits["total_samples"]
    threshold = pruned["threshold"]
    protected = {nid for nid, rec in overrides["overrides"].items() if rec["protected"]}

    def below_threshold(nid):
        return hits["hits"].get(nid, 0) / total < threshold

    # Everything actually pruned was below threshold and unprotected.
    for nid in pruned["pruned"]:
        assert below_threshold(nid)
        assert nid not in protected

    # pct-encoded is below threshold but protected -> must NOT be pruned.
    pct = "rfc3986-uri:pct-encoded#6a7b8c9d"
    assert below_threshold(pct)
    assert pct in protected
    assert pct not in pruned["pruned"]
