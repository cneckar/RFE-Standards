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
    protected = _expand_protection(
        ast, overrides_mod.protected_nodes(overrides), hitmap, total, threshold
    )

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


def _subtree(nodes: dict[str, Any], root: str) -> set[str]:
    """Every node id in the subtree rooted at ``root`` (inclusive)."""
    seen: set[str] = set()
    stack = [root]
    while stack:
        nid = stack.pop()
        if nid in seen:
            continue
        seen.add(nid)
        stack.extend(nodes[nid].get("children", []))
    return seen


def _expand_protection(
    ast: dict[str, Any],
    protected: set[str],
    hitmap: dict[str, int],
    total: int,
    threshold: float,
) -> set[str]:
    """Grow an explicit protected set into one that actually *renders*.

    Protecting a single rule node is not enough: the code generator drops a rule
    whose whole body pruned away, and would emit a dangling reference to a rule it
    kept but whose definition pruned. So a protected feature that is rare enough to
    have an empty body (userinfo, IP-literal on a page-URL corpus) still vanishes.

    This expands the set so protected features survive end to end:

    * **Downward** — keep each protected node's subtree, then follow references.
      A referenced rule that would otherwise be pruned (below ``threshold``) is
      force-kept in full, recursively, so the feature's sub-grammar renders with
      no dangling reference. A rule that survives on its own usage is left to
      normal pruning, so shared leaf rules (``sub-delims``, ``unreserved``) are
      not broadened.
    * **Upward** — for each explicitly protected *rule*, keep every reference to
      it plus the wrapper/separator nodes up to the containing rule, so the
      feature reappears in context (e.g. ``userinfo`` back in
      ``authority = [ userinfo "@" ] host [ ":" port ]``).
    """
    nodes = ast["nodes"]
    rule_by_name = {
        n["name"]: nid for nid, n in nodes.items() if n.get("kind") == "rule" and "name" in n
    }
    # The registry may protect nodes from other grammars; keep only ours.
    protected = {pid for pid in protected if pid in nodes}
    keep: set[str] = set()
    for pid in protected:
        keep |= _subtree(nodes, pid)

    # Downward: force-keep below-threshold rules reachable by reference.
    changed = True
    while changed:
        changed = False
        for nid in list(keep):
            node = nodes[nid]
            if node.get("kind") != "reference":
                continue
            target = rule_by_name.get(node.get("name"))
            if target is None or target in keep:
                continue
            if _usage(hitmap.get(target, 0), total) < threshold:
                keep |= _subtree(nodes, target)
                changed = True

    # Upward: reattach each protected rule to the rules that reference it.
    parent = {c: nid for nid, node in nodes.items() for c in node.get("children", [])}
    protected_rule_names = {
        nodes[p]["name"] for p in protected if nodes[p].get("kind") == "rule" and "name" in nodes[p]
    }
    for nid, node in nodes.items():
        if node.get("kind") != "reference" or node.get("name") not in protected_rule_names:
            continue
        cur = nid
        keep.add(cur)
        while cur in parent:
            p = parent[cur]
            if nodes[p].get("kind") == "rule":
                break
            keep.add(p)
            # Keep literal separators sitting beside the reference in a sequence
            # (the "@" in [ userinfo "@" ]) so the context renders faithfully.
            if nodes[p].get("kind") == "sequence":
                for sib in nodes[p].get("children", []):
                    if nodes[sib].get("kind") == "terminal":
                        keep.add(sib)
            cur = p
    return keep


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
