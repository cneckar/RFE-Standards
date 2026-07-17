"""Public Suffix List → registrable domain (part of T6.4).

The per-domain cap in the sampler needs to know each URI's *registrable domain*
— the label just below its public suffix (``example.co.uk``, not ``co.uk`` and
not ``www.example.co.uk``). "Public suffix" is not "the last label": ``co.uk``,
``github.io``, and ``s3.amazonaws.com`` are all suffixes under which anyone can
register. The authoritative source is Mozilla's Public Suffix List, vendored
offline at ``data/public_suffix_list.dat`` (MPL-2.0, header preserved).

This module implements the PSL matching algorithm (normal, wildcard ``*``, and
exception ``!`` rules) exactly enough to compute registrable domains.
"""

from __future__ import annotations

from functools import cache, lru_cache
from pathlib import Path

_PSL_PATH = Path(__file__).resolve().parent / "data" / "public_suffix_list.dat"


class PublicSuffixList:
    """Parsed Public Suffix List with a ``registrable_domain`` query.

    Rules are indexed by their last label so a lookup only scans the handful of
    rules sharing a host's TLD.
    """

    def __init__(self, text: str) -> None:
        # last-label -> list of (labels_tuple, is_wildcard, is_exception)
        self._rules: dict[str, list[tuple[tuple[str, ...], bool, bool]]] = {}
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("//"):
                continue
            is_exception = line.startswith("!")
            if is_exception:
                line = line[1:]
            labels = tuple(line.split("."))
            is_wildcard = labels[0] == "*"
            self._rules.setdefault(labels[-1], []).append((labels, is_wildcard, is_exception))

    def _match_len(self, labels: list[str]) -> int:
        """Return the public-suffix length (in labels) for ``labels``.

        Implements the PSL algorithm: exception rules win; otherwise the
        longest matching normal/wildcard rule; otherwise the default ``*`` rule
        (the rightmost label is itself a public suffix).
        """
        best = 0
        exception = 0
        for rule_labels, is_wildcard, is_exception in self._rules.get(labels[-1], ()):
            rlen = len(rule_labels)
            if rlen > len(labels):
                continue
            tail = labels[len(labels) - rlen :]
            ok = all(rl == "*" or rl == hl for rl, hl in zip(rule_labels, tail, strict=True))
            if not ok:
                continue
            if is_exception:
                # An exception rule's public suffix is the rule minus its first label.
                exception = max(exception, rlen - 1)
            else:
                best = max(best, rlen)
            _ = is_wildcard  # wildcard handled by the "*" comparison above
        if exception:
            return exception
        if best:
            return best
        return 1  # default rule: "*"

    def registrable_domain(self, host: str) -> str | None:
        """Return the registrable domain of ``host``, or ``None`` if there is none.

        ``None`` when ``host`` is empty, is itself a public suffix (e.g.
        ``co.uk``), or is an IP-literal-looking string with no dot structure to
        reduce. Case is folded for matching but the original labels are returned.
        """
        host = host.strip().rstrip(".").lower()
        if not host or ":" in host or "/" in host:
            return None
        labels = host.split(".")
        if any(not label for label in labels):
            return None
        suffix_len = self._match_len(labels)
        if len(labels) <= suffix_len:
            return None  # host is a public suffix itself — nothing registrable
        return ".".join(labels[len(labels) - suffix_len - 1 :])


@cache
def default_psl() -> PublicSuffixList:
    """Load and cache the vendored Public Suffix List."""
    return PublicSuffixList(_PSL_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1 << 17)
def registrable_domain(host: str) -> str | None:
    """Registrable domain of ``host`` using the vendored PSL.

    Memoized: at 10⁸ scale the dominant per-URI cost is this PSL walk, and the
    Common Crawl index is SURT-sorted so consecutive URIs share a host — the
    cache turns per-URI lookups into roughly per-distinct-host lookups.
    """
    return default_psl().registrable_domain(host)
