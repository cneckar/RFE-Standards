"""MVS/RFE pipeline package.

Dev-time tooling: grammar-to-AST extraction (Phase 1), corpus ingestion
(Phase 2), and the MVS pruner (Phase 3). Each phase reads and writes the shared
JSON artifact spine defined in Task 0.2.
"""

__all__ = ["usage_fraction"]


def usage_fraction(hits: int, total_samples: int) -> float:
    """Return the share of samples that traversed a node.

    This is the core quantity the pruner (Task 3.1) thresholds against
    ``MIN_USAGE_PERCENTAGE``. A node never seen in a non-empty corpus has a
    usage fraction of ``0.0``; an empty corpus yields ``0.0`` rather than a
    division error.
    """
    if total_samples <= 0:
        return 0.0
    return hits / total_samples
