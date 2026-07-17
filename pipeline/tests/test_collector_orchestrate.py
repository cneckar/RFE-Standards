"""End-to-end orchestration tests for the collector (T6.8).

The wiring test runs the whole pipeline (sources → dedup/cap → sample → shard →
telemetry → merge → stamped, validated hits.json) with an injected pure-Python
telemetry runner, so it exercises the orchestration in the normal Python CI job
without needing the Rust binary. A second test runs the *real* ``mvs-telemetry``
binary when one is available (the dedicated CI e2e job builds it), covering the
T4.2-bounded ingestion path for real.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from mvs_pipeline import schema
from mvs_pipeline.collector.filelist import FileListSource
from mvs_pipeline.collector.orchestrate import (
    binary_telemetry_runner,
    parse_stratum_spec,
    run_collection,
    telemetry_argv,
)
from mvs_pipeline.collector.sampler import Stratum

FIXTURES = Path(__file__).parent / "fixtures" / "e2e"
AST = Path(__file__).resolve().parents[2] / "artifacts" / "rfc3986-uri.ast.json"


def _strata() -> list[Stratum]:
    seed = FileListSource([FIXTURES / "seed_uris.txt"], name="pages")
    div = FileListSource([FIXTURES / "diverse_uris.txt"], name="outlinks")
    return [Stratum(seed, 0.8, name="pages"), Stratum(div, 0.2, name="outlinks")]


def _fake_runner(corpus_path: Path) -> dict:
    """A stand-in telemetry runner: count lines as samples, record no hits."""
    lines = [ln for ln in corpus_path.read_text().splitlines() if ln.strip()]
    return {
        "schema_version": 1,
        "grammar": "rfc3986-uri",
        "total_samples": len(lines),
        "hits": {},
    }


def _protected_ids_runner(corpus_path: Path) -> dict:
    """A runner that credits exactly the override-protected nodes, once each.

    Lets the prune+codegen chain be exercised with a non-empty hit set so the
    minified grammar keeps the protected rules and drops the rest.
    """
    import yaml

    from mvs_pipeline import overrides as overrides_mod

    reg = yaml.safe_load(overrides_mod.default_path().read_text())
    n = sum(1 for ln in corpus_path.read_text().splitlines() if ln.strip())
    return {
        "schema_version": 1,
        "grammar": "rfc3986-uri",
        "total_samples": n,
        "hits": {nid: n for nid, rec in reg["overrides"].items() if rec.get("protected")},
    }


def test_orchestrate_is_lazy_but_reachable_from_package() -> None:
    # Importing the package must NOT eagerly import the orchestrate submodule
    # (that pre-import is what makes `python -m ...orchestrate` warn, once per
    # spawned worker). Lazy access via the package still resolves the callable.
    import subprocess
    import sys

    code = (
        "import sys, mvs_pipeline.collector as c; "
        "assert 'mvs_pipeline.collector.orchestrate' not in sys.modules, 'imported eagerly'; "
        "assert callable(c.run_collection), 'lazy attr missing'; "
        "assert 'mvs_pipeline.collector.orchestrate' in sys.modules, 'lazy load failed'"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_argv_includes_bounds() -> None:
    argv = telemetry_argv("mvs-telemetry", "a.json", "c.txt", "o.json")
    assert "--max-depth" in argv and "--max-input-bytes" in argv
    # Bounds can be disabled explicitly.
    bare = telemetry_argv("b", "a", "c", "o", max_depth=None, max_input_bytes=None)
    assert "--max-depth" not in bare and "--max-input-bytes" not in bare


def test_parse_stratum_spec() -> None:
    assert parse_stratum_spec("pages=0.8:corpus/seed.txt") == ("pages", 0.8, "corpus/seed.txt")
    # A Windows-y path with a drive colon still splits on the first ':' only once
    # after NAME=WEIGHT, so paths may contain colons.
    assert parse_stratum_spec("x=1:a/b:c") == ("x", 1.0, "a/b:c")


@pytest.mark.parametrize("bad", ["noweight:path", "n=x:path", "n=1", "=1:p", "n=1:"])
def test_parse_stratum_spec_rejects_malformed(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_stratum_spec(bad)


def test_cli_runs_over_file_lists(tmp_path: Path) -> None:
    # The --list path builds FileListSource strata and runs end-to-end with a
    # binary-free run by pointing --binary at a stub that emits valid hits.
    from mvs_pipeline.collector.orchestrate import _main

    stub = tmp_path / "stub-telemetry"
    stub.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "a = sys.argv\n"
        "corpus = a[a.index('--corpus') + 1]\n"
        "out = a[a.index('--out') + 1]\n"
        "n = sum(1 for ln in open(corpus) if ln.strip())\n"
        'open(out, \'w\').write(\'{"schema_version":1,"grammar":"rfc3986-uri",\'\n'
        '                     f\'"total_samples":{n},"hits":{{}}}}\')\n'
    )
    stub.chmod(0o755)

    rc = _main(
        [
            "--ast",
            str(AST),
            "--list",
            f"pages=1.0:{FIXTURES / 'seed_uris.txt'}",
            "--target",
            "300",
            "--workdir",
            str(tmp_path / "w"),
            "--out",
            str(tmp_path / "o"),
            "--binary",
            str(stub),
        ]
    )
    assert rc == 0
    hits = json.loads((tmp_path / "o" / "hits.json").read_text())
    schema.validate("hits", hits)
    assert hits["total_samples"] == 300


def test_cli_requires_a_source(tmp_path: Path) -> None:
    from mvs_pipeline.collector.orchestrate import _main

    with pytest.raises(SystemExit):
        _main(
            [
                "--ast",
                str(AST),
                "--target",
                "10",
                "--workdir",
                str(tmp_path / "w"),
                "--out",
                str(tmp_path / "o"),
            ]
        )


def test_end_to_end_wiring(tmp_path: Path) -> None:
    summary = run_collection(
        _strata(),
        AST,
        target_n=1000,
        workdir=tmp_path / "w",
        out_dir=tmp_path / "o",
        seed=11,
        domain_cap=50,
        telemetry_runner=_fake_runner,
        timestamp="2026-01-01T00:00:00Z",
    )
    assert summary["grammar"] == "rfc3986-uri"
    assert summary["total_written"] == 1000
    # Fake runner counts every corpus line, so merged samples == corpus size.
    assert summary["total_samples"] == 1000

    hits = json.loads(Path(summary["hits_path"]).read_text())
    schema.validate("hits", hits)
    assert hits["provenance"]["seed"] == 11
    assert hits["provenance"]["sample_size"] == 1000
    assert hits["provenance"]["timestamp"] == "2026-01-01T00:00:00Z"


def test_progress_callback_receives_phase_lines(tmp_path: Path) -> None:
    lines: list[str] = []
    run_collection(
        _strata(),
        AST,
        target_n=200,
        workdir=tmp_path / "w",
        out_dir=tmp_path / "o",
        seed=1,
        telemetry_runner=_fake_runner,
        progress=lines.append,
    )
    joined = "\n".join(lines)
    assert "sampling" in joined  # opening phase
    assert "corpus:" in joined  # after sampling
    assert "telemetry shard 1/" in joined  # per-shard
    assert any("kept" in ln for ln in lines)  # per-stratum result


def test_end_to_end_is_reproducible(tmp_path: Path) -> None:
    a = run_collection(
        _strata(),
        AST,
        target_n=500,
        workdir=tmp_path / "wa",
        out_dir=tmp_path / "oa",
        seed=3,
        telemetry_runner=_fake_runner,
    )
    b = run_collection(
        _strata(),
        AST,
        target_n=500,
        workdir=tmp_path / "wb",
        out_dir=tmp_path / "ob",
        seed=3,
        telemetry_runner=_fake_runner,
    )
    corpus_a = (Path(a["corpus_dir"]) / "corpus-000.txt").read_text()
    corpus_b = (Path(b["corpus_dir"]) / "corpus-000.txt").read_text()
    assert corpus_a == corpus_b


def test_domain_cap_curbs_mega_site(tmp_path: Path) -> None:
    summary = run_collection(
        _strata(),
        AST,
        target_n=1500,
        workdir=tmp_path / "w",
        out_dir=tmp_path / "o",
        seed=1,
        domain_cap=20,
        telemetry_runner=_fake_runner,
    )
    all_uris: list[str] = []
    for p in sorted((Path(summary["corpus_dir"])).glob("corpus-*.txt")):
        all_uris += [ln for ln in p.read_text().splitlines() if ln]
    mega = [u for u in all_uris if "mega.example" in u]
    assert len(mega) <= 20


def test_emit_mvs_chains_prune_and_codegen(tmp_path: Path) -> None:
    # One call: sources → hits → prune → minified grammar. The runner credits
    # only the override-protected nodes, so pruning drops the rest and the
    # generated ABNF is non-empty and mentions the surviving grammar name.
    mvs_out = tmp_path / "mvs.abnf"
    pruned_out = tmp_path / "pruned.json"
    summary = run_collection(
        _strata(),
        AST,
        target_n=300,
        workdir=tmp_path / "w",
        out_dir=tmp_path / "o",
        seed=5,
        telemetry_runner=_protected_ids_runner,
        emit_mvs=mvs_out,
        pruned_out=pruned_out,
    )
    assert summary["mvs_path"] == str(mvs_out)
    assert summary["kept_nodes"] > 0
    assert summary["pruned_count"] > 0
    # The pruned doc validates and inherited the corpus provenance from hits.
    pruned = json.loads(pruned_out.read_text())
    schema.validate("pruned", pruned)
    assert pruned["surviving_grammar"] == "rfc3986-uri-mvs"
    assert pruned["provenance"]["seed"] == 5
    text = mvs_out.read_text()
    assert text.strip()  # a real grammar came out
    assert "generated" in text  # the header


def test_emit_mvs_cli_end_to_end(tmp_path: Path) -> None:
    from mvs_pipeline.collector.orchestrate import _main

    stub = tmp_path / "stub-telemetry"
    stub.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "a = sys.argv\n"
        "corpus = a[a.index('--corpus') + 1]\n"
        "out = a[a.index('--out') + 1]\n"
        "n = sum(1 for ln in open(corpus) if ln.strip())\n"
        'open(out, \'w\').write(\'{"schema_version":1,"grammar":"rfc3986-uri",\'\n'
        '                     f\'"total_samples":{n},"hits":{{}}}}\')\n'
    )
    stub.chmod(0o755)
    mvs_out = tmp_path / "mvs.abnf"
    rc = _main(
        [
            "--ast",
            str(AST),
            "--list",
            f"pages=1.0:{FIXTURES / 'seed_uris.txt'}",
            "--target",
            "300",
            "--workdir",
            str(tmp_path / "w"),
            "--out",
            str(tmp_path / "o"),
            "--binary",
            str(stub),
            "--emit-mvs",
            str(mvs_out),
        ]
    )
    assert rc == 0
    # Empty hit set → everything below threshold is pruned except protected nodes,
    # so the grammar still generates (and the file exists) without error.
    assert mvs_out.exists()


def test_pruned_out_requires_emit_mvs(tmp_path: Path) -> None:
    from mvs_pipeline.collector.orchestrate import _main

    with pytest.raises(SystemExit):
        _main(
            [
                "--ast",
                str(AST),
                "--list",
                f"pages=1.0:{FIXTURES / 'seed_uris.txt'}",
                "--target",
                "10",
                "--workdir",
                str(tmp_path / "w"),
                "--out",
                str(tmp_path / "o"),
                "--pruned-out",
                str(tmp_path / "p.json"),
            ]
        )


def _find_binary() -> str | None:
    env = os.environ.get("MVS_TELEMETRY_BIN")
    if env and Path(env).exists():
        return env
    which = shutil.which("mvs-telemetry")
    if which:
        return which
    root = Path(__file__).resolve().parents[2] / "core" / "target"
    for profile in ("release", "debug"):
        cand = root / profile / "mvs-telemetry"
        if cand.exists():
            return str(cand)
    return None


@pytest.mark.skipif(_find_binary() is None, reason="mvs-telemetry binary not built")
def test_end_to_end_with_real_binary(tmp_path: Path) -> None:
    binary = _find_binary()
    assert binary is not None
    summary = run_collection(
        _strata(),
        AST,
        target_n=800,
        workdir=tmp_path / "w",
        out_dir=tmp_path / "o",
        seed=7,
        domain_cap=50,
        telemetry_runner=binary_telemetry_runner(AST, binary=binary),
    )
    hits = json.loads(Path(summary["hits_path"]).read_text())
    schema.validate("hits", hits)
    assert hits["total_samples"] == 800
    # Real parser: the http/https page URLs should match and record node hits.
    assert hits["hits"], "expected some node hits from real telemetry"
