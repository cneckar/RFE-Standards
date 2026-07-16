"""Merge per-shard ``hits.json`` documents into one (T6.6).

The 10⁸-URI corpus is processed in shards — ``mvs-telemetry`` runs once per
shard and emits a ``hits.json`` for that slice (see ``docs/CORPUS-PLAN.md``).
This module recombines them: node counts and ``total_samples`` are summed, so
the merged document is exactly what a single run over the whole corpus would
have produced.

Merging is plain integer addition, so it is **associative and
order-independent**: any grouping or ordering of the same shards yields the same
result. Every input and the output are validated against ``hits.schema.json``,
and all shards must agree on ``grammar`` (you cannot sum counts across grammars).
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from mvs_pipeline import schema


def merge_hits(
    docs: Iterable[dict[str, Any]],
    *,
    provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Sum a collection of shard ``hits`` documents into one.

    Each document is validated against the hits schema on the way in and the
    merged result is validated on the way out. All shards must share the same
    ``grammar``. An empty input is rejected — there is no grammar to attribute
    the (empty) result to. An optional ``provenance`` block (T6.7) is stamped
    onto the merged document when given.
    """
    grammar: str | None = None
    total_samples = 0
    counts: dict[str, int] = {}

    seen = False
    for doc in docs:
        seen = True
        schema.validate("hits", doc)
        if grammar is None:
            grammar = doc["grammar"]
        elif doc["grammar"] != grammar:
            raise ValueError(
                f"cannot merge hits across grammars: {grammar!r} vs {doc['grammar']!r}"
            )
        total_samples += doc["total_samples"]
        for node_id, count in doc["hits"].items():
            counts[node_id] = counts.get(node_id, 0) + count

    if not seen:
        raise ValueError("merge_hits requires at least one hits document")

    merged: dict[str, Any] = {
        "schema_version": 1,
        "grammar": grammar,
        "total_samples": total_samples,
        # Sort keys so the merged artifact is byte-stable regardless of shard order.
        "hits": {k: counts[k] for k in sorted(counts)},
    }
    if provenance:
        merged["provenance"] = provenance
    schema.validate("hits", merged)
    return merged


def merge_hits_files(
    paths: Iterable[str | Path],
    *,
    provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Load and merge shard ``hits.json`` files from disk."""
    return merge_hits((schema.load_document(p) for p in paths), provenance=provenance)


def _main(argv: list[str] | None = None) -> int:
    import argparse

    from mvs_pipeline.provenance import provenance_from_manifest

    parser = argparse.ArgumentParser(description="Merge per-shard hits.json files.")
    parser.add_argument("shards", type=Path, nargs="+", help="per-shard hits.json files")
    parser.add_argument("--out", type=Path, required=True, help="merged hits.json output")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="corpus manifest.json to stamp as provenance (T6.7)",
    )
    parser.add_argument(
        "--timestamp",
        default=None,
        help="ISO-8601 timestamp to record in the provenance block",
    )
    args = parser.parse_args(argv)

    provenance = None
    if args.manifest is not None:
        manifest = schema.load_document(args.manifest)
        provenance = provenance_from_manifest(
            manifest, manifest_ref=str(args.manifest), timestamp=args.timestamp
        )
    merged = merge_hits_files(args.shards, provenance=provenance)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(merged, indent=2, sort_keys=True) + "\n")
    print(
        f"merged {len(args.shards)} shard(s): grammar={merged['grammar']} "
        f"total_samples={merged['total_samples']} nodes={len(merged['hits'])}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
