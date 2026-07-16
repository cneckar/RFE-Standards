"""Corpus collection subsystem (Epic 6).

Free-source connectors that stream URIs for the telemetry engine, plus the
shared `Source` protocol and deterministic sampling used across them. See
`docs/CORPUS-PLAN.md` for the overall design.
"""

from mvs_pipeline.collector.base import Source, keep_sample, stable_fraction
from mvs_pipeline.collector.commoncrawl import CommonCrawlUrlIndex

__all__ = ["CommonCrawlUrlIndex", "Source", "keep_sample", "stable_fraction"]
