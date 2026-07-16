"""Tests for provenance stamping on hits/pruned artifacts (T6.7)."""

from __future__ import annotations

from mvs_pipeline import schema
from mvs_pipeline.hitsmerge import merge_hits
from mvs_pipeline.provenance import make_provenance, provenance_from_manifest
from mvs_pipeline.pruner import prune

A = "abnf:URI#0a1b2c3d"


def _hits(total: int, **nodes: int) -> dict:
    return {
        "schema_version": 1,
        "grammar": "rfc3986-uri",
        "total_samples": total,
        "hits": dict(nodes),
    }


# --- provenance block construction -----------------------------------------


def test_make_provenance_omits_unset() -> None:
    assert make_provenance(seed=7, sample_size=100) == {"seed": 7, "sample_size": 100}
    assert make_provenance() == {}


def test_provenance_from_manifest() -> None:
    manifest = {
        "seed": 3,
        "total_written": 500,
        "strata": [
            {"provenance": {"crawl_id": "CC-MAIN-2024-10"}},
            {"provenance": {"dump_dates": ["20240101"]}},
            {"provenance": {"source": "no-ids"}},
        ],
    }
    prov = provenance_from_manifest(
        manifest, manifest_ref="o/manifest.json", timestamp="2026-01-01T00:00:00Z"
    )
    assert prov == {
        "manifest": "o/manifest.json",
        "crawl_ids": ["CC-MAIN-2024-10"],
        "dump_dates": ["20240101"],
        "seed": 3,
        "sample_size": 500,
        "timestamp": "2026-01-01T00:00:00Z",
    }


# --- schema additivity -----------------------------------------------------


def test_hits_valid_with_and_without_provenance() -> None:
    doc = _hits(10, **{A: 3})
    schema.validate("hits", doc)  # no provenance — still valid
    doc["provenance"] = {"seed": 1, "sample_size": 10, "crawl_ids": ["CC-MAIN-2024-10"]}
    schema.validate("hits", doc)


def test_pruned_valid_with_provenance() -> None:
    doc = {
        "schema_version": 1,
        "grammar": "rfc3986-uri",
        "threshold": 0.001,
        "pruned": [A],
        "surviving_grammar": "mvs.abnf",
        "provenance": {"manifest": "o/manifest.json", "seed": 2},
    }
    schema.validate("pruned", doc)


def test_unknown_provenance_field_rejected() -> None:
    doc = _hits(10, **{A: 3})
    doc["provenance"] = {"bogus": 1}
    import pytest

    with pytest.raises(Exception):  # noqa: B017 — jsonschema.ValidationError
        schema.validate("hits", doc)


# --- emitted by merge + pruner ---------------------------------------------


def test_merge_stamps_provenance() -> None:
    prov = make_provenance(seed=9, sample_size=17, manifest="o/manifest.json")
    merged = merge_hits([_hits(10, **{A: 2}), _hits(7, **{A: 1})], provenance=prov)
    assert merged["provenance"] == prov
    assert merged["total_samples"] == 17
    schema.validate("hits", merged)


def test_prune_inherits_provenance_from_hits() -> None:
    ast = {"schema_version": 1, "grammar": "rfc3986-uri", "root": A, "nodes": {A: {}}}
    hits = _hits(1000, **{A: 0})
    hits["provenance"] = {"seed": 5, "sample_size": 1000}
    doc = prune(ast, hits, {"schema_version": 1, "overrides": {}}, surviving_grammar="mvs.abnf")
    assert doc["provenance"] == {"seed": 5, "sample_size": 1000}


def test_prune_explicit_provenance_overrides_hits() -> None:
    ast = {"schema_version": 1, "grammar": "rfc3986-uri", "root": A, "nodes": {A: {}}}
    hits = _hits(1000, **{A: 0})
    hits["provenance"] = {"seed": 5}
    doc = prune(
        ast,
        hits,
        {"schema_version": 1, "overrides": {}},
        surviving_grammar="mvs.abnf",
        provenance={"seed": 99, "manifest": "x"},
    )
    assert doc["provenance"] == {"seed": 99, "manifest": "x"}
