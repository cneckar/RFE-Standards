"""Corpus collection subsystem (Epic 6).

Free-source connectors that stream URIs for the telemetry engine, plus the
shared `Source` protocol and deterministic sampling used across them. See
`docs/CORPUS-PLAN.md` for the overall design.
"""

from mvs_pipeline.collector.base import Source, keep_sample, stable_fraction
from mvs_pipeline.collector.commoncrawl import CommonCrawlUrlIndex
from mvs_pipeline.collector.wat import CommonCrawlWat
from mvs_pipeline.collector.wikipedia import WikipediaExternalLinks

__all__ = [
    "CommonCrawlUrlIndex",
    "CommonCrawlWat",
    "Source",
    "WikipediaExternalLinks",
    "keep_sample",
    "stable_fraction",
]
