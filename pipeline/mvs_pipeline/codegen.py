"""MVS code generator — the minified standard (Task 3.2).

Recompile a Phase-1 AST into a grammar file with the pruned nodes removed,
producing a minified but *valid* ABNF (URIs) or ASN.1 (X.509) definition. It
unparses each surviving rule back to source, dropping pruned rules entirely and
pruned children (dead alternation branches, never-present OPTIONAL fields) from
the rules that remain.

The node-hit invariant makes this safe: a reference is only credited when its
target rule is, so a kept reference always points at a kept rule — the minified
grammar has no dangling references.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from mvs_pipeline.schema import load_document


def _detect_format(ast: dict[str, Any]) -> str:
    """Infer ``"asn1"`` vs ``"abnf"`` from the node kinds present."""
    kinds = {node["kind"] for node in ast["nodes"].values()}
    if kinds & {"tag", "string-type", "named-type"}:
        return "asn1"
    return "abnf"


def _rule_order(ast: dict[str, Any]) -> list[str]:
    """Rule node ids in source-definition order (by recorded span)."""
    rules = [(nid, node) for nid, node in ast["nodes"].items() if node["kind"] == "rule"]
    rules.sort(key=lambda item: item[1].get("span", {}).get("start", 0))
    return [nid for nid, _ in rules]


def _unparse(nid: str, nodes: dict[str, Any], pruned: set[str], fmt: str) -> str | None:
    """Render one non-rule node to source, or ``None`` if it drops out."""
    if nid in pruned:
        return None
    node = nodes[nid]
    kind = node["kind"]
    name = node["name"]
    children = node.get("children", [])

    def kids() -> list[str]:
        return [u for c in children if (u := _unparse(c, nodes, pruned, fmt)) is not None]

    if kind in ("terminal", "string-type", "reference"):
        return name

    if kind == "named-type":  # ASN.1 field: "name Type [OPTIONAL]"
        inner = _unparse(children[0], nodes, pruned, fmt) if children else None
        return None if inner is None else f"{name} {inner}"

    if kind == "optional":
        inner = _unparse(children[0], nodes, pruned, fmt) if children else None
        if inner is None:
            return None
        return f"[ {inner} ]" if fmt == "abnf" else f"{inner} OPTIONAL"

    if kind == "group":
        inner = _unparse(children[0], nodes, pruned, fmt) if children else None
        return None if inner is None else f"( {inner} )"

    if kind == "repetition":
        inner = _unparse(children[0], nodes, pruned, fmt) if children else None
        if inner is None:
            return None
        if fmt == "abnf":
            prefix = name.split("(", 1)[0]  # "1*4(...)" -> "1*4"
            return f"{prefix}{inner}"
        return f"{name} {inner}"  # "SEQUENCE OF" / "SET OF"

    if kind == "tag":  # ASN.1 "[n] EXPLICIT Type"
        inner = _unparse(children[0], nodes, pruned, fmt) if children else None
        return None if inner is None else f"{name} {inner}"

    if kind == "sequence":
        parts = kids()
        if not parts:
            return None
        if fmt == "abnf":
            return parts[0] if len(parts) == 1 else " ".join(parts)
        return f"{name} " + "{ " + ", ".join(parts) + " }"

    if kind == "alternation":
        parts = kids()
        if not parts:
            return None
        if fmt == "abnf":
            return parts[0] if len(parts) == 1 else " / ".join(parts)
        return "CHOICE { " + ", ".join(parts) + " }"

    raise ValueError(f"cannot unparse node kind {kind!r}")


def generate(ast: dict[str, Any], pruned_doc: dict[str, Any], fmt: str | None = None) -> str:
    """Generate the minified grammar text for ``ast`` minus the pruned nodes."""
    if ast["grammar"] != pruned_doc["grammar"]:
        raise ValueError("AST and pruned set are for different grammars")
    fmt = fmt or _detect_format(ast)
    nodes = ast["nodes"]
    pruned = set(pruned_doc["pruned"])
    assign = "=" if fmt == "abnf" else "::="

    header = (
        f"; Minified MVS grammar for {ast['grammar']} (generated).\n"
        f"; {len(pruned)} nodes pruned below usage threshold "
        f"{pruned_doc['threshold']}.\n\n"
        if fmt == "abnf"
        else f"-- Minified MVS grammar for {ast['grammar']} (generated).\n"
        f"-- {len(pruned)} nodes pruned below usage threshold "
        f"{pruned_doc['threshold']}.\n\n"
    )

    lines: list[str] = []
    for rid in _rule_order(ast):
        if rid in pruned:
            continue
        node = nodes[rid]
        body = _unparse(node["children"][0], nodes, pruned, fmt) if node.get("children") else None
        if body is None:
            continue
        lines.append(f"{node['name']} {assign} {body}")

    body_text = "\n".join(lines)
    if fmt == "asn1":
        module = ast["grammar"].replace("-", "_")
        return f"{header}{module} DEFINITIONS ::= BEGIN\n\n{body_text}\n\nEND\n"
    return f"{header}{body_text}\n"


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the minified MVS grammar.")
    parser.add_argument("--ast", type=Path, required=True)
    parser.add_argument("--pruned", type=Path, required=True)
    parser.add_argument("--format", choices=["abnf", "asn1"], default=None)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    ast = load_document(args.ast)
    pruned_doc = load_document(args.pruned)
    text = generate(ast, pruned_doc, args.format)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text)
    kept = text.count(" ::= ") + text.count(" = ")
    print(f"grammar={ast['grammar']} rules={kept} -> {args.out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
