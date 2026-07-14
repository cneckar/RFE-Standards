import json
from pathlib import Path

import pytest
from jsonschema import ValidationError

from mvs_pipeline import overrides, schema
from mvs_pipeline.schema import schema_dir

ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / "artifacts"


def _all_ast_node_ids() -> set[str]:
    ids: set[str] = set()
    for artifact in ARTIFACTS.glob("*.ast.json"):
        ids |= set(json.loads(artifact.read_text())["nodes"])
    return ids


def test_committed_registry_validates():
    doc = overrides.load_overrides()  # loads + schema-validates the repo file
    assert doc["schema_version"] == 1
    assert doc["overrides"], "registry should have at least the seeded entry"


def test_every_override_targets_a_real_node():
    doc = overrides.load_overrides()
    node_ids = _all_ast_node_ids()
    assert node_ids, "expected generated AST artifacts to exist"
    unknown = set(doc["overrides"]) - node_ids
    assert not unknown, f"overrides reference unknown node ids: {sorted(unknown)}"


def test_every_override_has_nonempty_justification():
    doc = overrides.load_overrides()
    for nid, rec in doc["overrides"].items():
        assert rec["justification"].strip(), f"{nid} has an empty justification"
        assert rec["owner"].strip(), f"{nid} has no owner"


def test_helpers():
    doc = overrides.load_overrides()
    protected = overrides.protected_nodes(doc)
    assert protected
    a_node = next(iter(protected))
    assert overrides.is_protected(doc, a_node)
    assert not overrides.is_protected(doc, "rfc3986-uri:does-not-exist#00000000")


def test_invalid_override_is_rejected():
    bad = schema_dir() / "examples" / "invalid" / "overrides-missing-justification.yaml"
    with pytest.raises(ValidationError):
        overrides.load_overrides(bad)
    assert not schema.is_valid("overrides", {"schema_version": 1})  # missing 'overrides'
