"""RFE service package.

Server-side tooling for Phase 5: the validation webhook (Task 5.1) that accepts
consumer-submitted corpora, and the telemetry re-run pipeline (Task 5.2) that
evaluates whether a submission justifies restoring a pruned node.
"""

__all__ = ["meets_threshold"]


def meets_threshold(hits: int, total_samples: int, min_usage: float) -> bool:
    """Whether a corpus pushes a node's usage at or above ``min_usage``.

    Used by the RFE re-run (Task 5.2) to decide if a submission is strong enough
    to warrant an ``overrides.yaml`` PR. An empty corpus never meets a positive
    threshold.
    """
    if total_samples <= 0:
        return False
    return hits / total_samples >= min_usage
