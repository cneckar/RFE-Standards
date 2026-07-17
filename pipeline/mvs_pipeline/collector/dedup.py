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
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable, Iterator
from functools import lru_cache
from pathlib import Path

from mvs_pipeline.collector.normalize import host_of, normalize_uri
from mvs_pipeline.collector.psl import registrable_domain

# Sentinel bucket for URIs without a registrable domain (cap does not apply).
_NO_DOMAIN = ""


@lru_cache(maxsize=1 << 16)
def _shard_index(domain: str, num_shards: int) -> int:
    """Stable shard for ``domain`` (stable across processes, unlike ``hash``).

    Memoized: called once per URI but keyed on the (repeating, adjacency-sorted)
    domain, so the blake2b runs roughly once per distinct domain.
    """
    digest = hashlib.blake2b(domain.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") % num_shards


def dedupe_and_cap(
    uris: Iterable[str],
    *,
    workdir: str | Path,
    domain_cap: int | None = 1000,
    num_shards: int = 16,
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
        # Phase 1: partition by registrable-domain hash.
        for raw in uris:
            read += 1
            if progress is not None and read % progress_every == 0:
                progress(f"read {read:,} URIs")
            uri = normalize_uri(raw)
            # Skip empties; normalize_uri already stripped tabs/newlines (control
            # chars), so the tab/newline-delimited shard format stays intact.
            if uri is None:
                continue
            host = host_of(uri)
            domain = registrable_domain(host) if host else None
            bucket = domain if domain is not None else _NO_DOMAIN
            idx = _shard_index(bucket, num_shards)
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
