"""Minimal URI normalization and host extraction (part of T6.4).

Normalization here is deliberately *non-lossy*: it removes only what is invalid
or unsafe to carry through the pipeline — control characters and surrounding
whitespace — and never rewrites case, percent-encoding, or structure. The
telemetry engine measures real usage, so canonicalizing away the variation we
want to measure would defeat the point. Host extraction is just enough to feed
the Public Suffix List for the per-domain cap.
"""

from __future__ import annotations

# C0 controls (0x00–0x1F), DEL (0x7F), and C1 controls (0x80–0x9F): never valid
# in a URI and a common source of junk in scraped data.
_CONTROL = set(range(0x00, 0x20)) | {0x7F} | set(range(0x80, 0xA0))
_STRIP_TABLE = dict.fromkeys(_CONTROL)


def normalize_uri(value: str) -> str | None:
    """Trim surrounding whitespace and drop control chars; ``None`` if empty.

    No lossy rewriting: scheme/host case, percent-encoding, and path structure
    are preserved exactly so downstream usage measurement is faithful.
    """
    cleaned = value.strip().translate(_STRIP_TABLE)
    return cleaned or None


def host_of(uri: str) -> str | None:
    """Extract the host from ``uri``'s authority, or ``None`` if it has none.

    Handles ``scheme://user:pass@host:port/...`` by stripping userinfo and
    port. Schemes without an authority (``mailto:``, ``tel:``, ``urn:``) and
    bracketed IPv6 literals return ``None`` — they carry no registrable domain
    and so are exempt from the per-domain cap.
    """
    scheme_sep = uri.find("://")
    if scheme_sep == -1:
        return None
    rest = uri[scheme_sep + 3 :]
    # Authority ends at the first '/', '?' or '#'.
    for i, ch in enumerate(rest):
        if ch in "/?#":
            rest = rest[:i]
            break
    if "@" in rest:  # strip userinfo
        rest = rest.rsplit("@", 1)[1]
    if rest.startswith("["):  # IPv6 literal — no registrable domain
        return None
    if ":" in rest:  # strip port
        rest = rest.split(":", 1)[0]
    return rest or None
