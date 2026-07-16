"""Corpus collection subsystem (Epic 6).

Free-source connectors that stream URIs for the telemetry engine, plus the
shared `Source` protocol and deterministic sampling used across them. See
`docs/CORPUS-PLAN.md` for the overall design.
"""

from mvs_pipeline.collector.base import Source, keep_sample, stable_fraction
from mvs_pipeline.collector.commoncrawl import CommonCrawlUrlIndex
from mvs_pipeline.collector.dedup import dedupe_and_cap
from mvs_pipeline.collector.normalize import host_of, normalize_uri
from mvs_pipeline.collector.psl import registrable_domain
from mvs_pipeline.collector.sampler import Stratum, allocate_quotas, stratified_sample
from mvs_pipeline.collector.wat import CommonCrawlWat
from mvs_pipeline.collector.wikipedia import WikipediaExternalLinks

__all__ = [
    "CommonCrawlUrlIndex",
    "CommonCrawlWat",
    "Source",
    "Stratum",
    "WikipediaExternalLinks",
    "allocate_quotas",
    "dedupe_and_cap",
    "host_of",
    "keep_sample",
    "normalize_uri",
    "registrable_domain",
    "stable_fraction",
    "stratified_sample",
]
