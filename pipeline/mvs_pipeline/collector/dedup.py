"""Bounded-memory exact dedup + per-domain cap at 10⁸ scale (T6.4).

Deduplicating 10⁸ URIs with an in-memory set would need many GB. Instead we do
a two-phase disk shuffle:

1. **Partition.** Stream every (normalized) URI into one of ``num_shards`` files,
   chosen by a stable hash of its **registrable domain**. Two properties fall
   out: identical URIs land in the same shard (so exact dedup is shard-local),
   and *all* URIs of a domain land in the same shard (so the per-domain cap is
   shard-local too).
2. **Reduce.** Process one shard at a time: a per-shard ``set`` drops exact
   duplicates and a per-shard counter enforces the per-registrable-domain cap.

Peak memory is one shard's worth of distinct URIs, not the whole corpus — tune
``num_shards`` to the machine. Output order is deterministic for a given input
order and shard count. URIs with no registrable domain (``mailto:``, IPv6, ...)
are exempt from the cap but still deduplicated.

The per-URI partition work (normalize + registrable-domain lookup + shard hash)
is the CPU cost at 10⁸ scale and is pure Python, so ``workers > 1`` fans it out
across processes. Results are consumed **in input order** (an ordered pool map),
so the shard files — and therefore which URIs survive the per-domain cap — are
bit-identical to the serial path. Determinism does not depend on ``workers``.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable, Iterator
from functools import lru_cache, partial
from pathlib import Path

from mvs_pipeline.collector.normalize import host_of, normalize_uri
from mvs_pipeline.collector.psl import registrable_domain

# Sentinel bucket for URIs without a registrable domain (cap does not apply).
_NO_DOMAIN = ""
# URIs per task when fanning partition work across worker processes. Big enough
# to amortize pickling/IPC, small enough to keep every worker fed.
_WORKER_CHUNKSIZE = 8192


@lru_cache(maxsize=1 << 16)
def _shard_index(domain: str, num_shards: int) -> int:
    """Stable shard for ``domain`` (stable across processes, unlike ``hash``).

    Memoized: called once per URI but keyed on the (repeating, adjacency-sorted)
    domain, so the blake2b runs roughly once per distinct domain.
    """
    digest = hashlib.blake2b(domain.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") % num_shards


def _classify(raw: str, num_shards: int) -> tuple[int, str, str] | None:
    """Map a raw URI to ``(shard_index, bucket, normalized_uri)``, or ``None`` to drop.

    Pure and top-level (so it is picklable and can run in a worker process). The
    ``(bucket, uri)`` pair is exactly what phase 1 writes; ``shard_index`` picks
    the file. ``None`` means the URI normalized away (empty/invalid).
    """
    uri = normalize_uri(raw)
    if uri is None:
        return None
    host = host_of(uri)
    domain = registrable_domain(host) if host else None
    bucket = domain if domain is not None else _NO_DOMAIN
    return _shard_index(bucket, num_shards), bucket, uri


def _iter_classified(
    uris: Iterable[str], num_shards: int, workers: int | None
) -> Iterator[tuple[int, str, str] | None]:
    """Yield ``_classify`` results for every input URI, in input order.

    Serial for ``workers`` in ``(None, 1)`` (no process-pool cost for small runs
    and tests); otherwise fans the pure per-URI work across a process pool whose
    ordered ``map`` preserves input order, keeping the output byte-identical.
    """
    if workers is None or workers <= 1:
        for raw in uris:
            yield _classify(raw, num_shards)
        return
    from concurrent.futures import ProcessPoolExecutor

    with ProcessPoolExecutor(max_workers=workers) as pool:
        yield from pool.map(
            partial(_classify, num_shards=num_shards), uris, chunksize=_WORKER_CHUNKSIZE
        )


def dedupe_and_cap(
    uris: Iterable[str],
    *,
    workdir: str | Path,
    domain_cap: int | None = 1000,
    num_shards: int = 16,
    workers: int | None = None,
    progress: Callable[[str], None] | None = None,
    progress_every: int = 250_000,
) -> Iterator[str]:
    """Yield normalized, exactly-deduplicated URIs under a per-domain cap.

    Parameters
    ----------
    workdir:
        Directory for the intermediate shard files (created if missing).
    domain_cap:
        Max URIs kept per registrable domain; ``None`` disables the cap.
    num_shards:
        Number of on-disk partitions; higher = lower peak memory.
    workers:
        Processes to fan the per-URI partition work across. ``None``/``1`` runs
        serially (default). Higher parallelizes the CPU-bound normalize/PSL step
        while preserving input order, so the output is unchanged.
    progress:
        Optional callback invoked every ``progress_every`` input URIs during the
        (long) partition phase, so a caller can show a heartbeat. ``None`` is
        silent — the default for library/test use.
    """
    if num_shards < 1:
        raise ValueError("num_shards must be >= 1")
    work = Path(workdir)
    work.mkdir(parents=True, exist_ok=True)

    shard_paths = [work / f"shard-{i:04d}.tsv" for i in range(num_shards)]
    handles = [p.open("w", encoding="utf-8") for p in shard_paths]
    read = 0
    try:
        # Phase 1: partition by registrable-domain hash. The per-URI classify may
        # run across worker processes, but results arrive in input order, so the
        # tab/newline-delimited shard format and cap outcome are unchanged.
        for classified in _iter_classified(uris, num_shards, workers):
            read += 1
            if progress is not None and read % progress_every == 0:
                progress(f"read {read:,} URIs")
            # None => normalize_uri stripped it to empty (control chars already
            # gone, so the shard format stays intact); skip it.
            if classified is None:
                continue
            idx, bucket, uri = classified
            handles[idx].write(f"{bucket}\t{uri}\n")
    finally:
        for h in handles:
            h.close()
    if progress is not None:
        progress(f"read {read:,} URIs total; deduplicating")

    # Phase 2: reduce each shard independently, in order.
    for path in shard_paths:
        seen: set[str] = set()
        per_domain: dict[str, int] = {}
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                bucket, _, uri = line.rstrip("\n").partition("\t")
                if uri in seen:
                    continue
                seen.add(uri)
                if domain_cap is not None and bucket != _NO_DOMAIN:
                    count = per_domain.get(bucket, 0)
                    if count >= domain_cap:
                        continue
                    per_domain[bucket] = count + 1
                yield uri
