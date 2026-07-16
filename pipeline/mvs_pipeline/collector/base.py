"""Shared collector plumbing: the `Source` protocol and deterministic sampling.

Every free-source connector (Common Crawl, Wikipedia, ...) is a `Source`: it
streams URIs and can describe its provenance. Sampling is deterministic — the
same ``(value, seed)`` always maps to the same fraction — so a corpus is
reproducible from a seed alone, independent of ordering or parallelism.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Source(Protocol):
    """A free URI source that streams URIs and reports its provenance.

    Connectors implement this so the sampler/orchestrator can treat every
    source uniformly. ``iter_uris`` must be safe to call once (it may consume a
    stream); ``provenance`` returns a JSON-serializable record of exactly what
    was read (crawl/dump ids, files, sample rate) for the manifest.
    """

    #: Short stable stratum name, e.g. ``"commoncrawl-index"``.
    name: str

    def iter_uris(self) -> Iterator[str]:
        """Yield URIs from this source (post-sampling, if a rate is set)."""
        ...

    def provenance(self) -> dict[str, Any]:
        """Return a JSON-serializable record of what this source read."""
        ...


def stable_fraction(value: str, seed: int) -> float:
    """Map ``value`` to a stable fraction in ``[0, 1)`` under ``seed``.

    Uses blake2b rather than the built-in ``hash`` so the result is stable
    across processes and runs (``hash`` is salted per-interpreter). This is the
    primitive behind reproducible sampling: the same URI and seed always land at
    the same point, so independent shards sample a consistent subset.
    """
    digest = hashlib.blake2b(f"{seed}:{value}".encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big") / (1 << 64)


def keep_sample(value: str, sample_rate: float, seed: int) -> bool:
    """Whether to keep ``value`` when sampling at ``sample_rate`` under ``seed``.

    Deterministic Bernoulli sampling: keeps roughly ``sample_rate`` of a large
    stream, always the *same* subset for a given seed. ``rate >= 1`` keeps all;
    ``rate <= 0`` keeps none.
    """
    if sample_rate >= 1.0:
        return True
    if sample_rate <= 0.0:
        return False
    return stable_fraction(value, seed) < sample_rate
