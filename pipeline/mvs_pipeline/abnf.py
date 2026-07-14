"""ABNF -> AST (Task 1.1).

A focused recursive-descent parser for the ABNF *of* ABNF (RFC 5234 section 4),
enough to ingest the RFC 3986 URI grammar and emit an AST that conforms to
``schemas/ast.schema.json``.

Every grammar rule becomes a ``rule`` node, and each rule body is decomposed into
structural child nodes (``alternation``/``sequence``/``repetition``/``optional``/
``group``/``reference``/``terminal``). Each node's id is

    <grammar>:<rule-name>#<hash8>

where ``hash8`` is a digest of the node's *structural path* from its owning rule,
so repeated occurrences of the same construct get distinct ids.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_GRAMMAR_DIR = Path(__file__).resolve().parent / "grammars"

# Kinds emitted for grammar constructs. All are members of the schema `kind` enum.
_ALTERNATION = "alternation"
_SEQUENCE = "sequence"
_REPETITION = "repetition"
_OPTIONAL = "optional"
_GROUP = "group"
_REFERENCE = "reference"
_TERMINAL = "terminal"

_RULENAME = re.compile(r"[A-Za-z][A-Za-z0-9-]*")
_DEFINED_AS = re.compile(r"\s*(=/|=)\s*")


class AbnfSyntaxError(ValueError):
    """Raised when the ABNF text cannot be parsed."""


@dataclass
class Elem:
    """An internal grammar-construct node before it is flattened to schema nodes."""

    kind: str
    name: str
    children: list[Elem] = field(default_factory=list)
    # Distinguishing detail (terminal value, reference target, repeat spec) that
    # keeps structurally-different siblings apart in the path hash.
    tag: str = ""


@dataclass
class Rule:
    """A parsed ABNF rule: a name, its body, and its span in the source text."""

    name: str
    body: Elem
    start: int
    end: int


# --------------------------------------------------------------------------- #
# Lexical preprocessing: strip comments (respecting quotes/prose) and fold      #
# continuation lines into one logical line per rule.                            #
# --------------------------------------------------------------------------- #


def _strip_comments_line(text: str) -> str:
    """Comment-strip line by line, tracking quote/prose state across the line."""
    result_lines: list[str] = []
    for line in text.split("\n"):
        in_quote = False
        in_prose = False
        cut = len(line)
        for i, ch in enumerate(line):
            if ch == '"' and not in_prose:
                in_quote = not in_quote
            elif ch == "<" and not in_quote:
                in_prose = True
            elif ch == ">" and not in_quote:
                in_prose = False
            elif ch == ";" and not in_quote and not in_prose:
                cut = i
                break
        result_lines.append(line[:cut].rstrip())
    return "\n".join(result_lines)


def _split_rules(text: str) -> list[tuple[str, str, str, int, int]]:
    """Fold lines into ``(name, defined_as, elements, start, end)`` tuples.

    A new rule begins on a line whose first column holds ``rulename`` followed by
    ``=`` or ``=/``; indented / continuation lines extend the current rule. Spans
    are byte offsets into ``text``.
    """
    rules: list[tuple[str, str, str, int, int]] = []
    lines = text.split("\n")

    # Precompute the start offset of each physical line.
    offsets: list[int] = []
    pos = 0
    for line in lines:
        offsets.append(pos)
        pos += len(line) + 1  # + newline

    cur: dict[str, Any] | None = None
    for idx, line in enumerate(lines):
        if not line.strip():
            continue
        starts_new = bool(line[:1].strip()) and _looks_like_rule_head(line)
        if starts_new:
            if cur is not None:
                rules.append(_finish_rule(cur))
            m = _RULENAME.match(line)
            assert m is not None
            name = m.group(0)
            rest = line[m.end() :]
            dm = _DEFINED_AS.match(rest)
            if dm is None:
                raise AbnfSyntaxError(f"expected '=' or '=/' after rule {name!r}")
            defined_as = dm.group(1)
            elements = rest[dm.end() :]
            cur = {
                "name": name,
                "defined_as": defined_as,
                "parts": [elements],
                "start": offsets[idx],
                "end": offsets[idx] + len(line),
            }
        else:
            if cur is None:
                raise AbnfSyntaxError(f"continuation line with no rule: {line!r}")
            cur["parts"].append(line.strip())
            cur["end"] = offsets[idx] + len(line)
    if cur is not None:
        rules.append(_finish_rule(cur))
    return rules


def _looks_like_rule_head(line: str) -> bool:
    m = _RULENAME.match(line)
    if m is None:
        return False
    return _DEFINED_AS.match(line[m.end() :]) is not None


def _finish_rule(cur: dict[str, Any]) -> tuple[str, str, str, int, int]:
    elements = " ".join(part for part in cur["parts"] if part)
    return (cur["name"], cur["defined_as"], elements, cur["start"], cur["end"])


# --------------------------------------------------------------------------- #
# Element parser: alternation / concatenation / repetition / element.          #
# --------------------------------------------------------------------------- #

_TERMINATORS = ")]"


class _ElementParser:
    def __init__(self, text: str, rule_name: str):
        self.s = text
        self.i = 0
        self.n = len(text)
        self.rule = rule_name

    def error(self, msg: str) -> AbnfSyntaxError:
        return AbnfSyntaxError(f"{self.rule}: {msg} at offset {self.i} in {self.s!r}")

    def _skip_ws(self) -> None:
        while self.i < self.n and self.s[self.i] in " \t":
            self.i += 1

    def parse(self) -> Elem:
        elem = self._alternation()
        self._skip_ws()
        if self.i != self.n:
            raise self.error("trailing input")
        return elem

    def _alternation(self) -> Elem:
        alts = [self._concatenation()]
        while True:
            self._skip_ws()
            if self.i < self.n and self.s[self.i] == "/":
                self.i += 1
                alts.append(self._concatenation())
            else:
                break
        if len(alts) == 1:
            return alts[0]
        return Elem(_ALTERNATION, _ALTERNATION, alts)

    def _concatenation(self) -> Elem:
        items: list[Elem] = []
        while True:
            self._skip_ws()
            if self.i >= self.n:
                break
            ch = self.s[self.i]
            if ch == "/" or ch in _TERMINATORS:
                break
            items.append(self._repetition())
        if not items:
            raise self.error("empty concatenation")
        if len(items) == 1:
            return items[0]
        return Elem(_SEQUENCE, _SEQUENCE, items)

    def _repetition(self) -> Elem:
        repeat = self._maybe_repeat()
        element = self._element()
        if repeat is None:
            return element
        return Elem(_REPETITION, f"{repeat}(...)", [element], tag=f"repeat:{repeat}")

    def _maybe_repeat(self) -> str | None:
        start = self.i
        while self.i < self.n and self.s[self.i].isdigit():
            self.i += 1
        has_star = False
        if self.i < self.n and self.s[self.i] == "*":
            has_star = True
            self.i += 1
            while self.i < self.n and self.s[self.i].isdigit():
                self.i += 1
        if self.i == start:
            return None
        if not has_star and not self.s[start : self.i].isdigit():
            # Only digits were consumed but no star and not all digits -> impossible.
            raise self.error("malformed repeat")
        return self.s[start : self.i]

    def _element(self) -> Elem:
        self._skip_ws()
        if self.i >= self.n:
            raise self.error("expected element")
        ch = self.s[self.i]
        if ch == "(":
            return self._group("(", ")", _GROUP)
        if ch == "[":
            return self._group("[", "]", _OPTIONAL)
        if ch == '"':
            return self._char_val()
        if ch == "%":
            return self._num_val()
        if ch == "<":
            return self._prose_val()
        if ch.isalpha():
            return self._reference()
        raise self.error(f"unexpected character {ch!r}")

    def _group(self, open_c: str, close_c: str, kind: str) -> Elem:
        assert self.s[self.i] == open_c
        self.i += 1
        inner = self._alternation()
        self._skip_ws()
        if self.i >= self.n or self.s[self.i] != close_c:
            raise self.error(f"expected {close_c!r}")
        self.i += 1
        return Elem(kind, kind, [inner])

    def _char_val(self) -> Elem:
        assert self.s[self.i] == '"'
        self.i += 1
        start = self.i
        while self.i < self.n and self.s[self.i] != '"':
            self.i += 1
        if self.i >= self.n:
            raise self.error("unterminated char-val")
        value = self.s[start : self.i]
        self.i += 1
        return Elem(_TERMINAL, f'"{value}"', tag=f"char:{value}")

    def _num_val(self) -> Elem:
        assert self.s[self.i] == "%"
        start = self.i
        self.i += 1
        if self.i >= self.n or self.s[self.i] not in "bdxBDX":
            raise self.error("expected b/d/x after '%'")
        self.i += 1
        self._consume_digits_for_base()
        while self.i < self.n and self.s[self.i] in "-.":
            sep = self.s[self.i]
            self.i += 1
            self._consume_digits_for_base()
            if sep == "-":
                break
        value = self.s[start : self.i]
        return Elem(_TERMINAL, value, tag=f"num:{value}")

    def _consume_digits_for_base(self) -> None:
        start = self.i
        while self.i < self.n and (self.s[self.i].isdigit() or self.s[self.i] in "abcdefABCDEF"):
            self.i += 1
        if self.i == start:
            raise self.error("expected digits in num-val")

    def _prose_val(self) -> Elem:
        assert self.s[self.i] == "<"
        self.i += 1
        start = self.i
        while self.i < self.n and self.s[self.i] != ">":
            self.i += 1
        if self.i >= self.n:
            raise self.error("unterminated prose-val")
        value = self.s[start : self.i]
        self.i += 1
        return Elem(_TERMINAL, f"<{value}>", tag=f"prose:{value}")

    def _reference(self) -> Elem:
        m = _RULENAME.match(self.s, self.i)
        if m is None:
            raise self.error("expected rulename")
        self.i = m.end()
        name = m.group(0)
        return Elem(_REFERENCE, name, tag=f"ref:{name}")


# --------------------------------------------------------------------------- #
# Public API                                                                    #
# --------------------------------------------------------------------------- #


def parse_rules(abnf_text: str) -> list[Rule]:
    """Parse ABNF source into a list of :class:`Rule`, merging ``=/`` increments."""
    stripped = _strip_comments_line(abnf_text)
    by_name: dict[str, Rule] = {}
    order: list[str] = []
    for name, defined_as, elements, start, end in _split_rules(stripped):
        body = _ElementParser(elements.strip(), name).parse()
        if defined_as == "=/":
            if name not in by_name:
                raise AbnfSyntaxError(f"'=/' for undefined rule {name!r}")
            existing = by_name[name]
            alts = existing.body.children if existing.body.kind == _ALTERNATION else [existing.body]
            new_alts = body.children if body.kind == _ALTERNATION else [body]
            existing.body = Elem(_ALTERNATION, _ALTERNATION, [*alts, *new_alts])
            existing.end = end
        else:
            if name in by_name:
                raise AbnfSyntaxError(f"duplicate rule {name!r}")
            by_name[name] = Rule(name, body, start, end)
            order.append(name)
    return [by_name[n] for n in order]


def _hash8(grammar: str, structural_path: str) -> str:
    digest = hashlib.sha256(f"{grammar}|{structural_path}".encode()).hexdigest()
    return digest[:8]


def build_ast(grammar: str, abnf_text: str, source: dict[str, str] | None = None) -> dict[str, Any]:
    """Build an ``ast.schema.json``-conforming AST from ABNF text."""
    rules = parse_rules(abnf_text)
    if not rules:
        raise AbnfSyntaxError("grammar contains no rules")

    nodes: dict[str, dict[str, Any]] = {}

    def node_id(rule_name: str, structural_path: str) -> str:
        return f"{grammar}:{rule_name}#{_hash8(grammar, structural_path)}"

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
            raise AbnfSyntaxError(f"node id collision on {nid} ({path})")
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


def load_grammar(name: str) -> str:
    """Read a vendored ``.abnf`` grammar file by base name (e.g. ``'rfc3986'``)."""
    return (_GRAMMAR_DIR / f"{name}.abnf").read_text()


def build_rfc3986_uri_ast() -> dict[str, Any]:
    """Build the AST for the RFC 3986 URI grammar (grammar id ``rfc3986-uri``)."""
    return build_ast(
        "rfc3986-uri",
        load_grammar("rfc3986"),
        source={
            "rfc": "RFC 3986",
            "uri": "https://www.rfc-editor.org/rfc/rfc3986",
            "section": "Appendix A",
        },
    )


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compile an ABNF grammar to an MVS AST.")
    parser.add_argument("--out", type=Path, required=True, help="output JSON path")
    args = parser.parse_args(argv)
    ast = build_rfc3986_uri_ast()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(ast, indent=2, sort_keys=True) + "\n")
    print(f"wrote {len(ast['nodes'])} nodes to {args.out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
