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


def test_argv_includes_bounds() -> None:
    argv = telemetry_argv("mvs-telemetry", "a.json", "c.txt", "o.json")
    assert "--max-depth" in argv and "--max-input-bytes" in argv
    # Bounds can be disabled explicitly.
    bare = telemetry_argv("b", "a", "c", "o", max_depth=None, max_input_bytes=None)
    assert "--max-depth" not in bare and "--max-input-bytes" not in bare


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
