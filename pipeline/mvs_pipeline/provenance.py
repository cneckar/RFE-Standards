"""Corpus provenance blocks for hits/pruned artifacts (T6.7).

An *optional, additive* ``provenance`` block records where a ``hits.json`` (and
the ``pruned.json`` derived from it) came from: the corpus manifest it was built
from, the crawl/dump ids behind it, the sampling seed and sample size, and when
it was produced. It makes every pruning decision traceable to its evidence
without changing any existing artifact — the block is never required, so older
hits/pruned documents stay valid.

Timestamps are passed in by the caller (never read from the clock here) so
callers that need reproducible output can control them.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

# The fields a provenance block may carry (all optional). Kept in sync with the
# `provenance` $def in hits.schema.json and pruned.schema.json.
PROVENANCE_FIELDS = (
    "manifest",
    "crawl_ids",
    "dump_dates",
    "seed",
    "sample_size",
    "timestamp",
)


def make_provenance(
    *,
    manifest: str | None = None,
    crawl_ids: Sequence[str] | None = None,
    dump_dates: Sequence[str] | None = None,
    seed: int | None = None,
    sample_size: int | None = None,
    timestamp: str | None = None,
) -> dict[str, Any]:
    """Build a provenance block, omitting any field left unset."""
    block: dict[str, Any] = {}
    if manifest is not None:
        block["manifest"] = manifest
    if crawl_ids:
        block["crawl_ids"] = list(crawl_ids)
    if dump_dates:
        block["dump_dates"] = list(dump_dates)
    if seed is not None:
        block["seed"] = seed
    if sample_size is not None:
        block["sample_size"] = sample_size
    if timestamp is not None:
        block["timestamp"] = timestamp
    return block


def provenance_from_manifest(
    manifest: dict[str, Any],
    *,
    manifest_ref: str | None = None,
    timestamp: str | None = None,
) -> dict[str, Any]:
    """Derive a provenance block from a T6.5 corpus ``manifest.json``.

    Pulls the seed and sample size from the manifest and gathers any
    ``crawl_id``/``dump_dates`` a stratum's source recorded in its provenance.
    """
    crawl_ids: list[str] = []
    dump_dates: list[str] = []
    for stratum in manifest.get("strata", []):
        prov = stratum.get("provenance", {})
        crawl_id = prov.get("crawl_id")
        if crawl_id:
            crawl_ids.append(crawl_id)
        dump_dates.extend(prov.get("dump_dates", []))
    return make_provenance(
        manifest=manifest_ref,
        crawl_ids=crawl_ids or None,
        dump_dates=dump_dates or None,
        seed=manifest.get("seed"),
        sample_size=manifest.get("total_written"),
        timestamp=timestamp,
    )
