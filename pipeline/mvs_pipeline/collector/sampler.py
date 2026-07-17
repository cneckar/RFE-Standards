"""Stratified sampler, quota controller, and provenance manifest (T6.5).

The final collection stage: combine the free sources under documented per-stratum
quotas, dedup and cap them (T6.4), sample each stratum down to an exact target
count, and emit sharded corpus files plus a provenance manifest. Sampling is
deterministic in the seed — *same seed → same corpus* — so every downstream
pruning decision is traceable to a reproducible evidence set.

Selection is deterministic **bottom-k**: each URI is scored by the stable
``stable_fraction`` hash and the ``target`` lowest-scoring URIs are kept. This is
a uniform sample, independent of arrival order, and reproducible across runs and
machines. The manifest records the seed, per-stratum quotas and realized counts,
and each source's own provenance (crawl/dump ids, files, sampling).
"""

from __future__ import annotations

import heapq
import json
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mvs_pipeline.collector.base import Source, stable_fraction
from mvs_pipeline.collector.dedup import dedupe_and_cap

MANIFEST_SCHEMA_VERSION = 1


@dataclass
class Stratum:
    """One source with a relative sampling weight in the combined corpus."""

    source: Source
    weight: float
    name: str = ""

    def __post_init__(self) -> None:
        if self.weight < 0:
            raise ValueError("stratum weight must be non-negative")
        if not self.name:
            self.name = self.source.name


@dataclass
class StratumResult:
    name: str
    weight: float
    target: int
    kept: int
    provenance: dict[str, Any]


@dataclass
class SampleResult:
    """Outcome of a stratified sampling run."""

    seed: int
    target_n: int
    total_written: int
    shards: list[str]
    strata: list[StratumResult] = field(default_factory=list)


def allocate_quotas(weights: Sequence[float], target_n: int) -> list[int]:
    """Split ``target_n`` across ``weights`` so the parts sum to exactly ``target_n``.

    Largest-remainder (Hamilton) apportionment: proportional floors, then the
    leftover units go to the largest fractional remainders. Deterministic.
    """
    total = sum(weights)
    if total <= 0:
        raise ValueError("total stratum weight must be positive")
    exact = [target_n * w / total for w in weights]
    floors = [int(x) for x in exact]
    remainder = target_n - sum(floors)
    # Distribute the remaining units to the largest fractional parts (ties by index).
    order = sorted(range(len(weights)), key=lambda i: (-(exact[i] - floors[i]), i))
    for i in order[:remainder]:
        floors[i] += 1
    return floors


def _bottom_k(items: Iterable[str], k: int, seed: int) -> list[str]:
    """Return the ``k`` URIs with the smallest stable fraction, deterministically.

    Holds at most ``k`` items (a bounded max-heap keyed by fraction). Ties on the
    fraction break by the URI string so the result is fully determined.
    """
    if k <= 0:
        return []
    heap: list[tuple[float, str]] = []  # max-heap via negated key
    for uri in items:
        key = stable_fraction(uri, seed)
        entry = (-key, uri)
        if len(heap) < k:
            heapq.heappush(heap, entry)
        elif entry > heap[0]:
            heapq.heapreplace(heap, entry)
    # Smallest fraction first, then lexicographic — stable, reproducible order.
    return [uri for negkey, uri in sorted(heap, key=lambda e: (-e[0], e[1]))]


def stratified_sample(
    strata: Sequence[Stratum],
    *,
    target_n: int,
    workdir: str | Path,
    out_dir: str | Path,
    seed: int = 0,
    domain_cap: int | None = 1000,
    num_shards: int = 16,
    num_output_shards: int = 4,
    progress: Callable[[str], None] | None = None,
) -> SampleResult:
    """Sample ``strata`` to ``target_n`` URIs; write corpus shards + manifest.

    Each stratum is deduped and domain-capped (T6.4), then sampled to its quota.
    Sampled strata are combined with a final cross-stratum exact dedup (first
    occurrence wins, in stratum order), then written round-robin to
    ``num_output_shards`` newline corpus files. A ``manifest.json`` capturing the
    seed, quotas, realized counts, and per-source provenance is written too.

    ``progress`` (optional) is called with human-readable status lines as each
    stratum is read and sampled; ``None`` is silent.
    """
    work = Path(workdir)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    targets = allocate_quotas([s.weight for s in strata], target_n)

    combined: list[str] = []
    seen: set[str] = set()
    results: list[StratumResult] = []
    for i, (stratum, target) in enumerate(zip(strata, targets, strict=True)):
        if progress is not None:
            progress(f"stratum {i + 1}/{len(strata)} '{stratum.name}': reading (target {target:,})")
        stratum_progress = (
            (lambda m, name=stratum.name: progress(f"  [{name}] {m}"))
            if progress is not None
            else None
        )
        deduped = dedupe_and_cap(
            stratum.source.iter_uris(),
            workdir=work / f"stratum-{i:02d}",
            domain_cap=domain_cap,
            num_shards=num_shards,
            progress=stratum_progress,
        )
        # Per-stratum seed keeps strata from selecting correlated subsets.
        sampled = _bottom_k(deduped, target, seed + i)
        kept = 0
        for uri in sampled:
            if uri in seen:
                continue
            seen.add(uri)
            combined.append(uri)
            kept += 1
        if progress is not None:
            progress(f"stratum '{stratum.name}': kept {kept:,}")
        results.append(
            StratumResult(
                name=stratum.name,
                weight=stratum.weight,
                target=target,
                kept=kept,
                provenance=stratum.source.provenance(),
            )
        )

    shard_paths = _write_shards(combined, out, num_output_shards)
    result = SampleResult(
        seed=seed,
        target_n=target_n,
        total_written=len(combined),
        shards=[str(p) for p in shard_paths],
        strata=results,
    )
    (out / "manifest.json").write_text(json.dumps(_manifest_dict(result), indent=2) + "\n")
    return result


def _write_shards(uris: list[str], out_dir: Path, num_output_shards: int) -> list[Path]:
    """Write ``uris`` round-robin into ``num_output_shards`` newline corpus files."""
    if num_output_shards < 1:
        raise ValueError("num_output_shards must be >= 1")
    paths = [out_dir / f"corpus-{i:03d}.txt" for i in range(num_output_shards)]
    buckets: list[list[str]] = [[] for _ in range(num_output_shards)]
    for idx, uri in enumerate(uris):
        buckets[idx % num_output_shards].append(uri)
    for path, bucket in zip(paths, buckets, strict=True):
        text = "\n".join(bucket)
        path.write_text(text + "\n" if bucket else "")
    return paths


def _manifest_dict(result: SampleResult) -> dict[str, Any]:
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "seed": result.seed,
        "target_n": result.target_n,
        "total_written": result.total_written,
        "shards": [Path(s).name for s in result.shards],
        "strata": [
            {
                "name": s.name,
                "weight": s.weight,
                "target": s.target,
                "kept": s.kept,
                "provenance": s.provenance,
            }
            for s in result.strata
        ],
    }


__all__ = [
    "Stratum",
    "SampleResult",
    "StratumResult",
    "Source",
    "allocate_quotas",
    "stratified_sample",
]
