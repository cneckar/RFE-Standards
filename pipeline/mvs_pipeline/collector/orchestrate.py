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

#: Default T4.2 bounds for ingestion. The recognizer recurses ~one frame per
#: matched byte, so the depth bound must cover the input-length bound or long
#: (valid) URLs are recorded as non-matches — the default is sized to accept any
#: input within DEFAULT_MAX_INPUT_BYTES (the binary runs on a deep stack).
DEFAULT_MAX_DEPTH = 200_000
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
    workers: int | None = None,
    telemetry_runner: TelemetryRunner | None = None,
    binary: str | Path = "mvs-telemetry",
    max_depth: int | None = DEFAULT_MAX_DEPTH,
    max_input_bytes: int | None = DEFAULT_MAX_INPUT_BYTES,
    timestamp: str | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Run the full sources → shards → telemetry → merged hits.json pipeline.

    Returns a summary dict with the sample result, the merged ``hits.json`` path,
    and per-shard totals. The merged hits are schema-validated and stamped with
    provenance derived from the corpus manifest. ``progress`` (optional) receives
    human-readable status lines for each phase; ``None`` is silent.
    """

    def _note(msg: str) -> None:
        if progress is not None:
            progress(msg)

    out = Path(out_dir)
    corpus_dir = out / "corpus"
    _note(f"sampling {len(strata)} stratum(s) → target {target_n:,} URIs")
    sample: SampleResult = stratified_sample(
        strata,
        target_n=target_n,
        workdir=workdir,
        out_dir=corpus_dir,
        seed=seed,
        domain_cap=domain_cap,
        num_shards=num_shards,
        num_output_shards=num_output_shards,
        workers=workers,
        progress=progress,
    )
    _note(f"corpus: {sample.total_written:,} URIs across {len(sample.shards)} shard(s)")

    runner = telemetry_runner or binary_telemetry_runner(
        ast_path, binary=binary, max_depth=max_depth, max_input_bytes=max_input_bytes
    )

    from mvs_pipeline.hitsmerge import merge_hits

    shard_hits: list[dict[str, Any]] = []
    for n, shard_name in enumerate(sample.shards, start=1):
        _note(f"telemetry shard {n}/{len(sample.shards)}")
        shard_hits.append(runner(corpus_dir / Path(shard_name).name))

    _note("merging shard hits + stamping provenance")
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


def parse_stratum_spec(spec: str) -> tuple[str, float, str]:
    """Parse a ``NAME=WEIGHT:PATH`` stratum spec into its parts.

    Raises ``ValueError`` on anything malformed so the CLI can report it.
    """
    name_weight, sep, path = spec.partition(":")
    name, eq, weight = name_weight.partition("=")
    if not sep or not eq or not name or not path:
        raise ValueError(f"expected NAME=WEIGHT:PATH, got {spec!r}")
    try:
        parsed_weight = float(weight)
    except ValueError:
        raise ValueError(f"weight must be a number in {spec!r}") from None
    return name, parsed_weight, path


def _main(argv: list[str] | None = None) -> int:
    import argparse

    from mvs_pipeline.collector.commoncrawl import (
        DEFAULT_SUBSET,
        DEFAULT_TRANSPORT,
        CommonCrawlUrlIndex,
    )
    from mvs_pipeline.collector.filelist import FileListSource
    from mvs_pipeline.collector.wat import CommonCrawlWat
    from mvs_pipeline.collector.wikipedia import WikipediaExternalLinks

    parser = argparse.ArgumentParser(
        description="Run the free-corpus collector end to end (T6.8).",
    )
    parser.add_argument("--ast", type=Path, required=True, help="the grammar AST")
    parser.add_argument(
        "--list",
        action="append",
        default=[],
        metavar="NAME=WEIGHT:PATH",
        help="a newline URI-list stratum, e.g. seed=1.0:corpus/seed.txt (repeatable)",
    )
    parser.add_argument(
        "--wat",
        action="append",
        default=[],
        metavar="NAME=WEIGHT:PATH",
        help="a Common Crawl WAT outlinks stratum (local .wat/.wat.gz, repeatable)",
    )
    parser.add_argument(
        "--wiki",
        action="append",
        default=[],
        metavar="NAME=WEIGHT:PATH",
        help="a Wikipedia externallinks stratum (local .sql/.sql.gz, repeatable)",
    )
    parser.add_argument(
        "--cc-crawl",
        default=None,
        metavar="CRAWL_ID",
        help="stream the Common Crawl URL index for a crawl, e.g. CC-MAIN-2024-10",
    )
    parser.add_argument(
        "--cc-weight", type=float, default=1.0, help="weight of the --cc-crawl stratum"
    )
    parser.add_argument(
        "--cc-subset", default=DEFAULT_SUBSET, help="cc-index subset (default: warc)"
    )
    parser.add_argument(
        "--cc-transport",
        choices=("https", "s3"),
        default=DEFAULT_TRANSPORT,
        help="read cc-index parquet over the https mirror (no creds) or anonymous s3",
    )
    parser.add_argument(
        "--cc-limit", type=int, default=None, help="max cc-index parquet files to read"
    )
    parser.add_argument(
        "--cc-sample-rate", type=float, default=1.0, help="fraction of cc-index URLs to keep"
    )
    parser.add_argument(
        "--wat-crawl",
        default=None,
        metavar="CRAWL_ID",
        help="stream a crawl's WAT outlinks over HTTPS (scheme-diverse links)",
    )
    parser.add_argument("--wat-weight", type=float, default=0.2, help="weight of --wat-crawl")
    parser.add_argument("--wat-limit", type=int, default=None, help="max WAT files to read")
    parser.add_argument(
        "--wat-sample-rate", type=float, default=1.0, help="fraction of WAT links to keep"
    )
    parser.add_argument(
        "--wiki-dump",
        default=None,
        metavar="WIKI",
        help="stream a Wikipedia externallinks dump over HTTPS, e.g. enwiki",
    )
    parser.add_argument("--wiki-date", default="latest", help="dump date (default: latest)")
    parser.add_argument("--wiki-weight", type=float, default=0.1, help="weight of --wiki-dump")
    parser.add_argument(
        "--wiki-sample-rate", type=float, default=1.0, help="fraction of Wikipedia URLs to keep"
    )
    parser.add_argument("--target", type=int, required=True, help="target corpus size N")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--domain-cap", type=int, default=1000)
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="processes for the per-URI dedup/partition step (default: serial)",
    )
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--binary", default="mvs-telemetry")
    parser.add_argument("--max-depth", type=int, default=DEFAULT_MAX_DEPTH)
    parser.add_argument("--max-input-bytes", type=int, default=DEFAULT_MAX_INPUT_BYTES)
    parser.add_argument("--timestamp", default=None)
    args = parser.parse_args(argv)

    import sys

    def _progress(msg: str) -> None:
        print(msg, file=sys.stderr, flush=True)

    strata: list[Stratum] = []
    try:
        for spec in args.list:
            name, weight, path = parse_stratum_spec(spec)
            strata.append(Stratum(FileListSource([path], name=name), weight, name=name))
        for spec in args.wat:
            name, weight, path = parse_stratum_spec(spec)
            strata.append(
                Stratum(CommonCrawlWat([path], crawl_id=args.cc_crawl), weight, name=name)
            )
        for spec in args.wiki:
            name, weight, path = parse_stratum_spec(spec)
            strata.append(Stratum(WikipediaExternalLinks([path]), weight, name=name))
    except ValueError as exc:
        parser.error(str(exc))
    if args.cc_crawl:
        _progress(
            f"resolving {args.cc_crawl} cc-index ({args.cc_subset} subset, {args.cc_transport})…"
        )
        cc = CommonCrawlUrlIndex.from_crawl(
            args.cc_crawl,
            subset=args.cc_subset,
            transport=args.cc_transport,
            limit=args.cc_limit,
            sample_rate=args.cc_sample_rate,
            seed=args.seed,
        )
        _progress(f"cc-index: {len(cc.paths):,} parquet file(s) to read")
        strata.append(Stratum(cc, args.cc_weight, name="cc-index"))
    if args.wat_crawl:
        _progress(f"resolving {args.wat_crawl} WAT files…")
        wat = CommonCrawlWat.from_crawl(
            args.wat_crawl,
            limit=args.wat_limit,
            sample_rate=args.wat_sample_rate,
            seed=args.seed,
        )
        _progress(f"wat: {len(wat.paths):,} WAT file(s) to read")
        strata.append(Stratum(wat, args.wat_weight, name="cc-outlinks"))
    if args.wiki_dump:
        wiki = WikipediaExternalLinks.from_dump(
            args.wiki_dump,
            date=args.wiki_date,
            sample_rate=args.wiki_sample_rate,
            seed=args.seed,
        )
        _progress(f"wikipedia: streaming {args.wiki_dump}-{args.wiki_date} externallinks dump")
        strata.append(Stratum(wiki, args.wiki_weight, name="wikipedia"))
    if not strata:
        parser.error(
            "provide at least one source "
            "(--list / --wat / --wiki / --cc-crawl / --wat-crawl / --wiki-dump)"
        )

    summary = run_collection(
        strata,
        args.ast,
        target_n=args.target,
        workdir=args.workdir,
        out_dir=args.out,
        seed=args.seed,
        domain_cap=args.domain_cap,
        workers=args.workers,
        binary=args.binary,
        max_depth=args.max_depth,
        max_input_bytes=args.max_input_bytes,
        timestamp=args.timestamp,
        progress=_progress,
    )
    print(
        f"grammar={summary['grammar']} corpus={summary['total_written']} "
        f"samples={summary['total_samples']} shards={summary['shards']} "
        f"hits={summary['hits_path']}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
