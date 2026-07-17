"""Corpus collection subsystem (Epic 6).

Free-source connectors that stream URIs for the telemetry engine, plus the
shared `Source` protocol and deterministic sampling used across them. See
`docs/CORPUS-PLAN.md` for the overall design.
"""

from typing import TYPE_CHECKING, Any

from mvs_pipeline.collector.base import Source, keep_sample, stable_fraction
from mvs_pipeline.collector.commoncrawl import CommonCrawlUrlIndex, resolve_index_paths
from mvs_pipeline.collector.dedup import dedupe_and_cap
from mvs_pipeline.collector.filelist import FileListSource
from mvs_pipeline.collector.normalize import host_of, normalize_uri
from mvs_pipeline.collector.psl import registrable_domain
from mvs_pipeline.collector.sampler import Stratum, allocate_quotas, stratified_sample
from mvs_pipeline.collector.wat import CommonCrawlWat, resolve_wat_paths
from mvs_pipeline.collector.wikipedia import WikipediaExternalLinks, dump_url

# ``orchestrate`` is imported lazily (PEP 562): eagerly importing it here would put
# it in ``sys.modules`` before ``python -m mvs_pipeline.collector.orchestrate``
# runs it as ``__main__``, which makes runpy emit a RuntimeWarning — once per
# spawned dedup worker. Keeping it lazy silences that while preserving
# ``from mvs_pipeline.collector import run_collection``.
_LAZY_FROM_ORCHESTRATE = frozenset(
    {"binary_telemetry_runner", "parse_stratum_spec", "run_collection"}
)

if TYPE_CHECKING:  # for type checkers / IDEs only; no runtime import
    from mvs_pipeline.collector.orchestrate import (
        binary_telemetry_runner,
        parse_stratum_spec,
        run_collection,
    )


def __getattr__(name: str) -> Any:
    if name in _LAZY_FROM_ORCHESTRATE:
        import importlib

        module = importlib.import_module("mvs_pipeline.collector.orchestrate")
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "CommonCrawlUrlIndex",
    "CommonCrawlWat",
    "FileListSource",
    "Source",
    "Stratum",
    "WikipediaExternalLinks",
    "allocate_quotas",
    "binary_telemetry_runner",
    "dedupe_and_cap",
    "dump_url",
    "host_of",
    "keep_sample",
    "normalize_uri",
    "parse_stratum_spec",
    "registrable_domain",
    "resolve_index_paths",
    "resolve_wat_paths",
    "run_collection",
    "stable_fraction",
    "stratified_sample",
]
