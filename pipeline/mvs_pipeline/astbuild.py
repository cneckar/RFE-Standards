"""Shared AST assembly for grammar/schema front-ends (Phase 1).

Both the ABNF front-end (Task 1.1) and the ASN.1 front-end (Task 1.2) produce an
internal tree of :class:`Elem` nodes grouped into :class:`Rule` definitions, then
hand them here to be flattened into an ``ast.schema.json``-conforming document.

Node ids are ``<grammar>:<rule-name>#<hash8>`` where ``hash8`` digests the node's
structural path from its owning rule, so repeated constructs get distinct ids.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

# Schema `kind` values shared by both front-ends.
ALTERNATION = "alternation"
SEQUENCE = "sequence"
REPETITION = "repetition"
OPTIONAL = "optional"
GROUP = "group"
REFERENCE = "reference"
TERMINAL = "terminal"
TAG = "tag"
STRING_TYPE = "string-type"
NAMED_TYPE = "named-type"


class AstBuildError(ValueError):
    """Raised when an AST cannot be assembled (e.g. a node-id collision)."""


@dataclass
class Elem:
    """An internal grammar/schema construct before it is flattened to a node."""

    kind: str
    name: str
    children: list[Elem] = field(default_factory=list)
    # Distinguishing detail (terminal value, reference target, tag/repeat spec)
    # that keeps structurally-different siblings apart in the path hash.
    tag: str = ""


@dataclass
class Rule:
    """A named top-level definition: a name, its body, and a source span."""

    name: str
    body: Elem
    start: int
    end: int


def hash8(grammar: str, structural_path: str) -> str:
    """Stable 8-hex digest of a node's structural path within a grammar."""
    return hashlib.sha256(f"{grammar}|{structural_path}".encode()).hexdigest()[:8]


def assemble(
    grammar: str, rules: list[Rule], source: dict[str, str] | None = None
) -> dict[str, Any]:
    """Flatten parsed ``rules`` into an ``ast.schema.json``-conforming document."""
    if not rules:
        raise AstBuildError("grammar contains no rules")

    nodes: dict[str, dict[str, Any]] = {}

    def node_id(rule_name: str, structural_path: str) -> str:
        return f"{grammar}:{rule_name}#{hash8(grammar, structural_path)}"

    def build_elem(elem: Elem, rule_name: str, path: str) -> str:
        nid = node_id(rule_name, path)
        child_ids: list[str] = []
        for idx, child in enumerate(elem.children):
            child_path = f"{path}>{child.kind}:{child.tag}:{idx}"
            child_ids.append(build_elem(child, rule_name, child_path))
        node: dict[str, Any] = {"kind": elem.kind, "name": elem.name}
        if child_ids:
            node["children"] = child_ids
        if nid in nodes:
            raise AstBuildError(f"node id collision on {nid} ({path})")
        nodes[nid] = node
        return nid

    root_id: str | None = None
    for rule in rules:
        rid = node_id(rule.name, rule.name)
        body_id = build_elem(rule.body, rule.name, f"{rule.name}>body")
        nodes[rid] = {
            "kind": "rule",
            "name": rule.name,
            "children": [body_id],
            "span": {"start": rule.start, "end": rule.end, "source": grammar},
        }
        if root_id is None:
            root_id = rid

    assert root_id is not None
    ast: dict[str, Any] = {
        "schema_version": 1,
        "grammar": grammar,
        "root": root_id,
        "nodes": nodes,
    }
    if source:
        ast["source"] = source
    return ast
