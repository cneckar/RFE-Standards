"""The MVS pruning logic engine (Task 3.1).

The decision matrix for what stays and what goes: ingest a Phase-1 AST, the
global telemetry aggregate (`hits.json`), and the Criticality Override Registry,
then emit the set of node ids to amputate — every node whose usage fraction is
below ``MIN_USAGE_PERCENTAGE`` **and** which is not protected. The output
conforms to ``schemas/pruned.schema.json`` and feeds the Phase-4 code generator.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from mvs_pipeline import overrides as overrides_mod
from mvs_pipeline import schema
from mvs_pipeline.schema import load_document

# Default usage threshold: a node used by fewer than 0.1% of samples is a
# pruning candidate. Overridable per run.
MIN_USAGE_PERCENTAGE = 0.001


def prune(
    ast: dict[str, Any],
    hits: dict[str, Any],
    overrides: dict[str, Any],
    *,
    threshold: float = MIN_USAGE_PERCENTAGE,
    surviving_grammar: str,
    provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute the pruned-node set for a grammar.

    A node is pruned when ``hits/total_samples < threshold`` and it is not marked
    protected in ``overrides``. With an empty corpus every node has usage 0, so
    only protected nodes survive.

    Corpus provenance (T6.7) is carried onto the pruned document: an explicit
    ``provenance`` wins, else the block stamped on ``hits`` (if any) is inherited,
    so a pruning decision stays traceable to its evidence.
    """
    if ast["grammar"] != hits["grammar"]:
        raise ValueError(
            f"grammar mismatch: AST is {ast['grammar']!r}, hits are {hits['grammar']!r}"
        )
    total = hits["total_samples"]
    hitmap = hits["hits"]
    protected = overrides_mod.protected_nodes(overrides)

    pruned = [
        nid
        for nid in ast["nodes"]
        if nid not in protected and _usage(hitmap.get(nid, 0), total) < threshold
    ]
    pruned.sort()

    doc: dict[str, Any] = {
        "schema_version": 1,
        "grammar": ast["grammar"],
        "threshold": threshold,
        "pruned": pruned,
        "surviving_grammar": surviving_grammar,
    }
    inherited = provenance if provenance is not None else hits.get("provenance")
    if inherited:
        doc["provenance"] = inherited
    schema.validate("pruned", doc)
    return doc


def _usage(hits: int, total_samples: int) -> float:
    if total_samples <= 0:
        return 0.0
    return hits / total_samples


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compute the MVS pruned-node set.")
    parser.add_argument("--ast", type=Path, required=True)
    parser.add_argument("--hits", type=Path, required=True)
    parser.add_argument(
        "--overrides",
        type=Path,
        default=None,
        help="override registry (default: the packaged overrides.yaml)",
    )
    parser.add_argument("--threshold", type=float, default=MIN_USAGE_PERCENTAGE)
    parser.add_argument("--surviving-grammar", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    ast = load_document(args.ast)
    hits = load_document(args.hits)
    overrides = overrides_mod.load_overrides(args.overrides)

    doc = prune(
        ast,
        hits,
        overrides,
        threshold=args.threshold,
        surviving_grammar=args.surviving_grammar,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    kept = len(ast["nodes"]) - len(doc["pruned"])
    print(f"grammar={doc['grammar']} pruned={len(doc['pruned'])} kept={kept}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
