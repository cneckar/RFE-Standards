"""Tests for per-shard hits.json merging (T6.6)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mvs_pipeline import schema
from mvs_pipeline.hitsmerge import merge_hits, merge_hits_files


def _hits(grammar: str, total: int, **nodes: int) -> dict:
    return {
        "schema_version": 1,
        "grammar": grammar,
        "total_samples": total,
        "hits": dict(nodes),
    }


A = "abnf:URI#0a1b2c3d"
B = "abnf:host#deadbeef"
C = "abnf:scheme#00112233"


def test_sums_counts_and_totals() -> None:
    merged = merge_hits(
        [
            _hits("rfc3986-uri", 10, **{A: 5, B: 2}),
            _hits("rfc3986-uri", 7, **{A: 1, C: 3}),
        ]
    )
    assert merged["grammar"] == "rfc3986-uri"
    assert merged["total_samples"] == 17
    assert merged["hits"] == {A: 6, B: 2, C: 3}


def test_output_is_schema_valid_and_sorted() -> None:
    merged = merge_hits([_hits("rfc3986-uri", 3, **{B: 1, A: 1})])
    schema.validate("hits", merged)
    assert list(merged["hits"]) == sorted([A, B])


def test_order_independent() -> None:
    shards = [
        _hits("rfc3986-uri", 4, **{A: 2}),
        _hits("rfc3986-uri", 5, **{B: 3}),
        _hits("rfc3986-uri", 6, **{A: 1, C: 1}),
    ]
    assert merge_hits(shards) == merge_hits(list(reversed(shards)))


def test_associative() -> None:
    x = _hits("rfc3986-uri", 4, **{A: 2})
    y = _hits("rfc3986-uri", 5, **{B: 3})
    z = _hits("rfc3986-uri", 6, **{A: 1})
    left = merge_hits([merge_hits([x, y]), z])
    right = merge_hits([x, merge_hits([y, z])])
    assert left == right == merge_hits([x, y, z])


def test_single_shard_roundtrips_counts() -> None:
    doc = _hits("rfc3986-uri", 9, **{A: 4, B: 5})
    merged = merge_hits([doc])
    assert merged["total_samples"] == 9
    assert merged["hits"] == {A: 4, B: 5}


def test_rejects_grammar_mismatch() -> None:
    with pytest.raises(ValueError, match="across grammars"):
        merge_hits([_hits("rfc3986-uri", 1, **{A: 1}), _hits("x509-cert", 1)])


def test_rejects_empty() -> None:
    with pytest.raises(ValueError, match="at least one"):
        merge_hits([])


def test_rejects_invalid_input() -> None:
    bad = {"schema_version": 1, "grammar": "rfc3986-uri", "total_samples": 1}  # no hits
    with pytest.raises(Exception):  # noqa: B017 (jsonschema.ValidationError)
        merge_hits([bad])


def test_merge_files(tmp_path: Path) -> None:
    p1 = tmp_path / "shard-0.json"
    p2 = tmp_path / "shard-1.json"
    p1.write_text(json.dumps(_hits("rfc3986-uri", 2, **{A: 2})))
    p2.write_text(json.dumps(_hits("rfc3986-uri", 3, **{A: 1, B: 4})))
    merged = merge_hits_files([p1, p2])
    assert merged["total_samples"] == 5
    assert merged["hits"] == {A: 3, B: 4}
