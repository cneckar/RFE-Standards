"""End-to-end corpus → hits orchestration with bounded ingestion (T6.8).

Ties the whole collector together into one repeatable run:

    sources ─▶ normalize/dedup/cap ─▶ stratified sample ─▶ corpus shards
            ─▶ mvs-telemetry per shard (T4.2 bounds on) ─▶ per-shard hits
            ─▶ merge ─▶ stamp provenance ─▶ validated hits.json

The per-shard parse step runs the native ``mvs-telemetry`` binary with the T4.2
bounds enabled (``--max-depth`` / ``--max-input-bytes``) so a single pathological
URL in a 10⁸ corpus can't stall a shard. The telemetry step is injectable so the
wiring is testable without the binary; the default runs the real thing.

The full 10⁸ run is documented in ``docs/CORPUS-PLAN.md`` and ``docs/collector.md``
(crawl selection, expected time/disk); the CI end-to-end exercises the same code
path at fixture scale.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from mvs_pipeline import schema
from mvs_pipeline.collector.sampler import SampleResult, Stratum, stratified_sample
from mvs_pipeline.provenance import provenance_from_manifest

# A telemetry runner maps a corpus shard file to its hits document.
TelemetryRunner = Callable[[Path], dict[str, Any]]

#: Default T4.2 bounds for ingestion (deep enough for real URIs, capped so junk
#: can't run away).
DEFAULT_MAX_DEPTH = 400
DEFAULT_MAX_INPUT_BYTES = 8192


def telemetry_argv(
    binary: str | Path,
    ast_path: str | Path,
    corpus_path: str | Path,
    out_path: str | Path,
    *,
    max_depth: int | None = DEFAULT_MAX_DEPTH,
    max_input_bytes: int | None = DEFAULT_MAX_INPUT_BYTES,
) -> list[str]:
    """Build the ``mvs-telemetry`` argv for one shard, with T4.2 bounds."""
    argv = [
        str(binary),
        "--ast",
        str(ast_path),
        "--corpus",
        str(corpus_path),
        "--out",
        str(out_path),
    ]
    if max_depth is not None:
        argv += ["--max-depth", str(max_depth)]
    if max_input_bytes is not None:
        argv += ["--max-input-bytes", str(max_input_bytes)]
    return argv


def binary_telemetry_runner(
    ast_path: str | Path,
    *,
    binary: str | Path = "mvs-telemetry",
    max_depth: int | None = DEFAULT_MAX_DEPTH,
    max_input_bytes: int | None = DEFAULT_MAX_INPUT_BYTES,
) -> TelemetryRunner:
    """A telemetry runner that shells out to the native ``mvs-telemetry`` binary."""

    def run(corpus_path: Path) -> dict[str, Any]:
        out_path = corpus_path.with_suffix(".hits.json")
        argv = telemetry_argv(
            binary,
            ast_path,
            corpus_path,
            out_path,
            max_depth=max_depth,
            max_input_bytes=max_input_bytes,
        )
        subprocess.run(argv, check=True, capture_output=True)
        return json.loads(out_path.read_text())

    return run


def run_collection(
    strata: Sequence[Stratum],
    ast_path: str | Path,
    *,
    target_n: int,
    workdir: str | Path,
    out_dir: str | Path,
    seed: int = 0,
    domain_cap: int | None = 1000,
    num_shards: int = 16,
    num_output_shards: int = 4,
    telemetry_runner: TelemetryRunner | None = None,
    binary: str | Path = "mvs-telemetry",
    max_depth: int | None = DEFAULT_MAX_DEPTH,
    max_input_bytes: int | None = DEFAULT_MAX_INPUT_BYTES,
    timestamp: str | None = None,
) -> dict[str, Any]:
    """Run the full sources → shards → telemetry → merged hits.json pipeline.

    Returns a summary dict with the sample result, the merged ``hits.json`` path,
    and per-shard totals. The merged hits are schema-validated and stamped with
    provenance derived from the corpus manifest.
    """
    out = Path(out_dir)
    corpus_dir = out / "corpus"
    sample: SampleResult = stratified_sample(
        strata,
        target_n=target_n,
        workdir=workdir,
        out_dir=corpus_dir,
        seed=seed,
        domain_cap=domain_cap,
        num_shards=num_shards,
        num_output_shards=num_output_shards,
    )

    runner = telemetry_runner or binary_telemetry_runner(
        ast_path, binary=binary, max_depth=max_depth, max_input_bytes=max_input_bytes
    )

    from mvs_pipeline.hitsmerge import merge_hits

    shard_hits: list[dict[str, Any]] = []
    for shard_name in sample.shards:
        shard_hits.append(runner(corpus_dir / Path(shard_name).name))

    manifest = json.loads((corpus_dir / "manifest.json").read_text())
    provenance = provenance_from_manifest(
        manifest, manifest_ref=str(corpus_dir / "manifest.json"), timestamp=timestamp
    )
    merged = merge_hits(shard_hits, provenance=provenance)
    schema.validate("hits", merged)

    out.mkdir(parents=True, exist_ok=True)
    hits_path = out / "hits.json"
    hits_path.write_text(json.dumps(merged, indent=2, sort_keys=True) + "\n")

    return {
        "hits_path": str(hits_path),
        "corpus_dir": str(corpus_dir),
        "manifest": str(corpus_dir / "manifest.json"),
        "total_samples": merged["total_samples"],
        "total_written": sample.total_written,
        "shards": len(sample.shards),
        "grammar": merged["grammar"],
    }


def _main(argv: list[str] | None = None) -> int:
    import argparse

    from mvs_pipeline.collector.filelist import FileListSource

    parser = argparse.ArgumentParser(
        description="Run the free-corpus collector end to end (T6.8).",
    )
    parser.add_argument("--ast", type=Path, required=True, help="the grammar AST")
    parser.add_argument(
        "--list",
        action="append",
        default=[],
        metavar="NAME=WEIGHT:PATH",
        help="a file-list stratum, e.g. seed=1.0:corpus/seed.txt (repeatable)",
    )
    parser.add_argument("--target", type=int, required=True, help="target corpus size N")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--domain-cap", type=int, default=1000)
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--binary", default="mvs-telemetry")
    parser.add_argument("--max-depth", type=int, default=DEFAULT_MAX_DEPTH)
    parser.add_argument("--max-input-bytes", type=int, default=DEFAULT_MAX_INPUT_BYTES)
    parser.add_argument("--timestamp", default=None)
    args = parser.parse_args(argv)

    strata: list[Stratum] = []
    for spec in args.list:
        name_weight, _, path = spec.partition(":")
        name, _, weight = name_weight.partition("=")
        strata.append(Stratum(FileListSource([path], name=name), float(weight), name=name))
    if not strata:
        parser.error("provide at least one --list stratum")

    summary = run_collection(
        strata,
        args.ast,
        target_n=args.target,
        workdir=args.workdir,
        out_dir=args.out,
        seed=args.seed,
        domain_cap=args.domain_cap,
        binary=args.binary,
        max_depth=args.max_depth,
        max_input_bytes=args.max_input_bytes,
        timestamp=args.timestamp,
    )
    print(
        f"grammar={summary['grammar']} corpus={summary['total_written']} "
        f"samples={summary['total_samples']} shards={summary['shards']} "
        f"hits={summary['hits_path']}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
