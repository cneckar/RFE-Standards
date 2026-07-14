"""ASN.1 -> AST (Task 1.2).

A focused parser for the subset of ASN.1 used by the RFC 5280 X.509 certificate
module. It ingests type assignments and emits an AST conforming to
``schemas/ast.schema.json``, giving every ASN.1 construct a unique node id:

- each type assignment becomes a ``rule`` node,
- ``SEQUENCE``/``SET`` bodies become ``sequence`` nodes of ``named-type`` fields,
- ``CHOICE`` becomes ``alternation``,
- ``SEQUENCE OF``/``SET OF`` become ``repetition``,
- context tags ``[n]`` become ``tag`` nodes,
- ``OPTIONAL``/``DEFAULT`` fields become ``optional`` nodes,
- character string types become ``string-type`` nodes,
- other primitives become ``terminal`` nodes and type references ``reference``.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mvs_pipeline import astbuild
from mvs_pipeline.astbuild import Elem, Rule

_GRAMMAR_DIR = Path(__file__).resolve().parent / "grammars"

_STRING_TYPES = {
    "UTF8String",
    "PrintableString",
    "IA5String",
    "TeletexString",
    "T61String",
    "UniversalString",
    "BMPString",
    "NumericString",
    "VisibleString",
    "GeneralString",
    "GraphicString",
    "VideotexString",
    "ISO646String",
}

# Primitive/base types rendered as terminal leaves (multi-word ones handled inline).
_PRIMITIVES = {"INTEGER", "BOOLEAN", "NULL", "UTCTime", "GeneralizedTime", "REAL", "ENUMERATED"}

_TOKEN = re.compile(
    r"""
      (?P<assign>::=)
    | (?P<punct>[{}\[\](),;])
    | (?P<range>\.\.)
    | (?P<dot>\.)
    | (?P<word>[A-Za-z][A-Za-z0-9-]*)
    | (?P<num>[0-9]+)
    | (?P<other>\S)
    """,
    re.VERBOSE,
)


class Asn1SyntaxError(ValueError):
    """Raised when the ASN.1 text cannot be parsed."""


@dataclass
class _Tok:
    kind: str
    text: str
    pos: int


def _strip_comments(text: str) -> str:
    """Remove ASN.1 ``--`` comments (end at the next ``--`` or end of line)."""
    out: list[str] = []
    for line in text.split("\n"):
        i = 0
        buf: list[str] = []
        while i < len(line):
            if line[i : i + 2] == "--":
                j = line.find("--", i + 2)
                if j == -1:
                    break  # comment runs to end of line
                i = j + 2  # comment closed; resume after the second '--'
            else:
                buf.append(line[i])
                i += 1
        out.append("".join(buf))
    return "\n".join(out)


def _tokenize(text: str) -> list[_Tok]:
    toks: list[_Tok] = []
    for m in _TOKEN.finditer(text):
        kind = m.lastgroup
        assert kind is not None
        text_ = m.group()
        if kind == "punct":
            kind = text_
        toks.append(_Tok(kind, text_, m.start()))
    return toks


class _Parser:
    def __init__(self, toks: list[_Tok]):
        self.toks = toks
        self.i = 0
        self.n = len(toks)

    # -- token helpers -----------------------------------------------------
    def _peek(self) -> _Tok | None:
        return self.toks[self.i] if self.i < self.n else None

    def _next(self) -> _Tok:
        if self.i >= self.n:
            raise Asn1SyntaxError("unexpected end of input")
        tok = self.toks[self.i]
        self.i += 1
        return tok

    def _at_word(self, *words: str) -> bool:
        tok = self._peek()
        return tok is not None and tok.kind == "word" and tok.text in words

    def _expect_word(self, word: str) -> _Tok:
        tok = self._next()
        if tok.kind != "word" or tok.text != word:
            raise Asn1SyntaxError(f"expected {word!r}, got {tok.text!r} at {tok.pos}")
        return tok

    def _expect(self, kind: str) -> _Tok:
        tok = self._next()
        if tok.kind != kind:
            raise Asn1SyntaxError(f"expected {kind!r}, got {tok.text!r} at {tok.pos}")
        return tok

    def _end_pos(self) -> int:
        prev = self.toks[self.i - 1]
        return prev.pos + len(prev.text)

    # -- module structure --------------------------------------------------
    def parse_module(self) -> list[Rule]:
        # Skip the module header up to and including BEGIN.
        while self.i < self.n and not self._at_word("BEGIN"):
            self.i += 1
        if self.i >= self.n:
            raise Asn1SyntaxError("no BEGIN found")
        self._next()  # consume BEGIN

        # Optional IMPORTS/EXPORTS clauses terminated by ';'.
        while self._at_word("IMPORTS", "EXPORTS"):
            while self.i < self.n and self._peek().kind != ";":  # type: ignore[union-attr]
                self.i += 1
            if self.i < self.n:
                self._next()  # consume ';'

        rules: list[Rule] = []
        seen: set[str] = set()
        while True:
            tok = self._peek()
            if tok is None:
                break
            if tok.kind == "word" and tok.text == "END":
                break
            rules.append(self._assignment(seen))
        return rules

    def _assignment(self, seen: set[str]) -> Rule:
        name_tok = self._next()
        if name_tok.kind != "word":
            raise Asn1SyntaxError(f"expected type name, got {name_tok.text!r} at {name_tok.pos}")
        if name_tok.text in seen:
            raise Asn1SyntaxError(f"duplicate assignment {name_tok.text!r}")
        self._expect("assign")
        body = self._type()
        seen.add(name_tok.text)
        return Rule(name_tok.text, body, name_tok.pos, self._end_pos())

    # -- types -------------------------------------------------------------
    def _type(self) -> Elem:
        tok = self._peek()
        if tok is None:
            raise Asn1SyntaxError("expected a type")

        if tok.kind == "[":
            return self._tagged()

        if tok.kind == "word":
            word = tok.text
            if word in ("SEQUENCE", "SET"):
                return self._sequence_or_set(word)
            if word == "CHOICE":
                return self._choice()
            if word == "INTEGER":
                self._next()
                self._skip_braced()  # optional named-number list
                self._skip_constraint()
                return Elem(astbuild.TERMINAL, "INTEGER", tag="INTEGER")
            if word == "BIT":
                self._next()
                self._expect_word("STRING")
                self._skip_braced()
                self._skip_constraint()
                return Elem(astbuild.TERMINAL, "BIT STRING", tag="BIT_STRING")
            if word == "OCTET":
                self._next()
                self._expect_word("STRING")
                self._skip_constraint()
                return Elem(astbuild.TERMINAL, "OCTET STRING", tag="OCTET_STRING")
            if word == "OBJECT":
                self._next()
                self._expect_word("IDENTIFIER")
                return Elem(astbuild.TERMINAL, "OBJECT IDENTIFIER", tag="OID")
            if word == "ANY":
                self._next()
                if self._at_word("DEFINED"):
                    self._next()
                    self._expect_word("BY")
                    self._next()  # the referenced field name
                return Elem(astbuild.TERMINAL, "ANY", tag="ANY")
            if word in _STRING_TYPES:
                self._next()
                self._skip_constraint()
                return Elem(astbuild.STRING_TYPE, word, tag=f"str:{word}")
            if word in _PRIMITIVES:
                self._next()
                self._skip_constraint()
                return Elem(astbuild.TERMINAL, word, tag=f"prim:{word}")
            # Otherwise: a reference to another named type.
            self._next()
            self._skip_constraint()
            return Elem(astbuild.REFERENCE, word, tag=f"ref:{word}")

        raise Asn1SyntaxError(f"unexpected token {tok.text!r} at {tok.pos}")

    def _tagged(self) -> Elem:
        self._expect("[")
        parts: list[str] = []
        while self.i < self.n and self._peek().kind != "]":  # type: ignore[union-attr]
            parts.append(self._next().text)
        self._expect("]")
        tag_text = " ".join(parts)
        tagging = ""
        if self._at_word("IMPLICIT", "EXPLICIT"):
            tagging = self._next().text
        inner = self._type()
        label = f"[{tag_text}]" + (f" {tagging}" if tagging else "")
        return Elem(astbuild.TAG, label, [inner], tag=f"tag:{tag_text}:{tagging}")

    def _sequence_or_set(self, word: str) -> Elem:
        self._next()  # SEQUENCE / SET
        self._skip_constraint()  # optional SIZE(...) before OF
        if self._at_word("OF"):
            self._next()
            inner = self._type()
            return Elem(astbuild.REPETITION, f"{word} OF", [inner], tag=f"{word.lower()}of")
        components = self._component_list()
        return Elem(astbuild.SEQUENCE, word, components, tag=word.lower())

    def _choice(self) -> Elem:
        self._next()  # CHOICE
        components = self._component_list()
        return Elem(astbuild.ALTERNATION, "CHOICE", components, tag="choice")

    def _component_list(self) -> list[Elem]:
        self._expect("{")
        components: list[Elem] = []
        while True:
            tok = self._peek()
            if tok is None:
                raise Asn1SyntaxError("unterminated component list")
            if tok.kind == "}":
                self._next()
                break
            components.append(self._component())
            nxt = self._peek()
            if nxt is not None and nxt.kind == ",":
                self._next()
        return components

    def _component(self) -> Elem:
        name_tok = self._next()
        if name_tok.kind != "word":
            raise Asn1SyntaxError(f"expected field name, got {name_tok.text!r} at {name_tok.pos}")
        inner = self._type()
        if self._at_word("OPTIONAL"):
            self._next()
            inner = Elem(astbuild.OPTIONAL, "OPTIONAL", [inner], tag="optional")
        elif self._at_word("DEFAULT"):
            self._next()
            self._next()  # default value token
            inner = Elem(astbuild.OPTIONAL, "DEFAULT", [inner], tag="default")
        return Elem(astbuild.NAMED_TYPE, name_tok.text, [inner], tag=f"comp:{name_tok.text}")

    # -- helpers to skip decoration ---------------------------------------
    def _skip_braced(self) -> None:
        """Skip a balanced ``{ ... }`` block if the next token opens one."""
        if self._peek() is None or self._peek().kind != "{":  # type: ignore[union-attr]
            return
        depth = 0
        while self.i < self.n:
            k = self._next().kind
            if k == "{":
                depth += 1
            elif k == "}":
                depth -= 1
                if depth == 0:
                    return
        raise Asn1SyntaxError("unterminated '{'")

    def _skip_constraint(self) -> None:
        """Skip a balanced ``( ... )`` constraint if present."""
        if self._peek() is None or self._peek().kind != "(":  # type: ignore[union-attr]
            return
        depth = 0
        while self.i < self.n:
            k = self._next().kind
            if k == "(":
                depth += 1
            elif k == ")":
                depth -= 1
                if depth == 0:
                    return
        raise Asn1SyntaxError("unterminated '('")


def parse_rules(asn1_text: str) -> list[Rule]:
    """Parse an ASN.1 module into a list of :class:`Rule`."""
    toks = _tokenize(_strip_comments(asn1_text))
    return _Parser(toks).parse_module()


def build_ast(grammar: str, asn1_text: str, source: dict[str, str] | None = None) -> dict[str, Any]:
    """Build an ``ast.schema.json``-conforming AST from ASN.1 text."""
    return astbuild.assemble(grammar, parse_rules(asn1_text), source)


def load_grammar(name: str) -> str:
    """Read a vendored ``.asn1`` module by base name (e.g. ``'rfc5280'``)."""
    return (_GRAMMAR_DIR / f"{name}.asn1").read_text()


def build_rfc5280_x509_ast() -> dict[str, Any]:
    """Build the AST for the RFC 5280 certificate core (grammar ``rfc5280-x509``)."""
    return build_ast(
        "rfc5280-x509",
        load_grammar("rfc5280"),
        source={
            "rfc": "RFC 5280",
            "uri": "https://www.rfc-editor.org/rfc/rfc5280",
            "section": "Appendix A",
        },
    )


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compile an ASN.1 module to an MVS AST.")
    parser.add_argument("--out", type=Path, required=True, help="output JSON path")
    args = parser.parse_args(argv)
    ast = build_rfc5280_x509_ast()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(ast, indent=2, sort_keys=True) + "\n")
    print(f"wrote {len(ast['nodes'])} nodes to {args.out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
