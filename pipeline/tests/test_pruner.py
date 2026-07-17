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


# --- transitive protection renders zero-usage security features -------------- #


def _refs_in(text: str) -> tuple[set[str], set[str]]:
    """Return (defined rule names, referenced rule names) for ABNF ``text``."""
    from mvs_pipeline import abnf

    rules = abnf.parse_rules(text)
    defined = {r.name for r in rules}
    used: set[str] = set()

    def walk(e: object) -> None:
        if e.kind == "reference":  # type: ignore[attr-defined]
            used.add(e.name)  # type: ignore[attr-defined]
        for c in e.children or []:  # type: ignore[attr-defined]
            walk(c)

    for r in rules:
        walk(r.body)
    return defined, used


def test_transitive_protection_renders_zero_usage_features():
    from mvs_pipeline import codegen

    ast = load_document(ARTIFACTS / "rfc3986-uri.ast.json")
    hits = load_document(ARTIFACTS / "rfc3986-uri.hits.json")
    # Zero out every hit for the IP-host / userinfo machinery so ONLY the override
    # (via transitive protection) can keep them — mimics a real page-URL corpus.
    rare = {"userinfo", "IP-literal", "IPv6address", "IPvFuture", "IPv4address", "h16", "ls32"}
    hm = {nid: c for nid, c in hits["hits"].items() if ast["nodes"][nid]["name"] not in rare}
    hits = {**hits, "hits": hm}

    doc = pruner.prune(ast, hits, overrides_mod.load_overrides(), surviving_grammar="mvs/x.abnf")
    text = codegen.generate(ast, doc)
    defined, used = _refs_in(text)

    # The protected features render, and IPvFuture rides in transitively via IP-literal.
    for feat in ("userinfo", "IP-literal", "IPv6address", "IPv4address", "IPvFuture"):
        assert feat in defined, f"{feat} missing from MVS"
    # …in context, not as orphan rules.
    authority = next(ln for ln in text.splitlines() if ln.startswith("authority "))
    host = next(ln for ln in text.splitlines() if ln.startswith("host "))
    assert "userinfo" in authority and "port" in authority
    assert "IP-literal" in host and "IPv4address" in host
    # No dangling references: every referenced rule is defined.
    assert not (used - defined), f"dangling references: {used - defined}"

    # Negative control: with an empty registry the same corpus drops them entirely,
    # proving it is the override — not residual usage — that keeps them.
    empty = {"schema_version": 1, "overrides": {}}
    bare = codegen.generate(ast, pruner.prune(ast, hits, empty, surviving_grammar="mvs/x.abnf"))
    bare_defined, _ = _refs_in(bare)
    assert not ({"userinfo", "IP-literal", "IPv6address"} & bare_defined)
