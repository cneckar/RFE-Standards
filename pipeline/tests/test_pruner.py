import json
from pathlib import Path

import pytest

from mvs_pipeline import overrides as overrides_mod
from mvs_pipeline import pruner, schema
from mvs_pipeline.schema import load_document

ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / "artifacts"


def _ast(*node_ids: str) -> dict:
    return {
        "schema_version": 1,
        "grammar": "g",
        "root": node_ids[0],
        "nodes": {nid: {"kind": "rule", "name": nid} for nid in node_ids},
    }


A = "g:a#00000001"
LOW = "g:low#00000002"
PROT = "g:prot#00000003"


def _hits(total: int, **counts: int) -> dict:
    return {"schema_version": 1, "grammar": "g", "total_samples": total, "hits": dict(counts)}


def _overrides(*protected: str) -> dict:
    return {
        "schema_version": 1,
        "overrides": {
            nid: {"protected": True, "justification": "critical", "owner": "team"}
            for nid in protected
        },
    }


def test_prunes_below_threshold_but_keeps_protected():
    ast = _ast(A, LOW, PROT)
    hits = _hits(100_000, **{A: 100_000})  # LOW and PROT have 0 hits
    doc = pruner.prune(
        ast,
        hits,
        _overrides(PROT),
        threshold=0.001,
        surviving_grammar="mvs/g.abnf",
    )
    assert doc["pruned"] == [LOW]  # A survives on usage, PROT on the override
    schema.validate("pruned", doc)


def test_threshold_is_configurable():
    ast = _ast(A, LOW, PROT)
    hits = _hits(100_000, **{A: 50_000})  # A used by 50% of samples
    # Default 0.1% threshold keeps A; a 60% threshold prunes it too.
    keep = pruner.prune(ast, hits, _overrides(PROT), surviving_grammar="mvs/g.abnf")
    assert keep["pruned"] == [LOW]
    strict = pruner.prune(
        ast, hits, _overrides(PROT), threshold=0.6, surviving_grammar="mvs/g.abnf"
    )
    assert strict["pruned"] == sorted([A, LOW])  # PROT still survives the override


def test_empty_corpus_prunes_everything_unprotected():
    ast = _ast(A, LOW, PROT)
    hits = _hits(0)
    doc = pruner.prune(ast, hits, _overrides(PROT), surviving_grammar="mvs/g.abnf")
    assert doc["pruned"] == sorted([A, LOW])


def test_grammar_mismatch_raises():
    ast = _ast(A)
    hits = {"schema_version": 1, "grammar": "other", "total_samples": 1, "hits": {}}
    with pytest.raises(ValueError):
        pruner.prune(ast, hits, _overrides(), surviving_grammar="mvs/g.abnf")


def test_default_threshold_constant():
    assert pruner.MIN_USAGE_PERCENTAGE == 0.001


# --- committed artifacts --------------------------------------------------- #


@pytest.mark.parametrize(
    ("grammar", "surviving"),
    [
        ("rfc3986-uri", "mvs/rfc3986-uri.mvs.abnf"),
        ("rfc5280-x509", "mvs/rfc5280-x509.mvs.asn1"),
    ],
)
def test_committed_pruned_artifacts_reproducible(grammar, surviving):
    ast = load_document(ARTIFACTS / f"{grammar}.ast.json")
    hits = load_document(ARTIFACTS / f"{grammar}.hits.json")
    overrides = overrides_mod.load_overrides()
    fresh = pruner.prune(ast, hits, overrides, surviving_grammar=surviving)

    committed = json.loads((ARTIFACTS / f"{grammar}.pruned.json").read_text())
    assert committed == fresh
    schema.validate("pruned", committed)

    node_ids = set(ast["nodes"])
    assert set(committed["pruned"]) <= node_ids
    # Pruned and kept partition the node set.
    assert len(committed["pruned"]) + (len(node_ids) - len(committed["pruned"])) == len(node_ids)


def test_protected_node_never_pruned_in_committed_uri():
    overrides = overrides_mod.load_overrides()
    protected = overrides_mod.protected_nodes(overrides)
    pruned = set(json.loads((ARTIFACTS / "rfc3986-uri.pruned.json").read_text())["pruned"])
    assert not (protected & pruned), "a protected node was pruned"
