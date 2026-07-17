"""Tests for normalization, the PSL, and bounded-memory dedup + domain cap (T6.4)."""

from __future__ import annotations

from pathlib import Path

from mvs_pipeline.collector.dedup import dedupe_and_cap
from mvs_pipeline.collector.normalize import host_of, normalize_uri
from mvs_pipeline.collector.psl import registrable_domain

# --- normalization ---------------------------------------------------------


def test_normalize_strips_whitespace_and_controls() -> None:
    assert normalize_uri("  https://x.test/a\t\n") == "https://x.test/a"
    assert normalize_uri("https://x.test/\x00\x07b") == "https://x.test/b"


def test_normalize_is_non_lossy() -> None:
    # Case and percent-encoding are preserved exactly.
    assert normalize_uri("HTTPS://X.TEST/A%2Fb") == "HTTPS://X.TEST/A%2Fb"


def test_normalize_empty_is_none() -> None:
    assert normalize_uri("   \x00 ") is None
    assert normalize_uri("") is None


# --- host extraction -------------------------------------------------------


def test_host_of_strips_userinfo_and_port() -> None:
    assert host_of("https://user:pw@www.example.com:8443/path?q#f") == "www.example.com"


def test_host_of_no_authority() -> None:
    assert host_of("mailto:a@b.test") is None
    assert host_of("tel:+15551234") is None


def test_host_of_ipv6_is_none() -> None:
    assert host_of("https://[2001:db8::1]/x") is None


# --- public suffix list ----------------------------------------------------


def test_registrable_domain_simple_and_multilevel() -> None:
    assert registrable_domain("www.example.com") == "example.com"
    assert registrable_domain("a.b.example.co.uk") == "example.co.uk"
    assert registrable_domain("foo.bar.github.io") == "bar.github.io"


def test_registrable_domain_unknown_tld_uses_default_rule() -> None:
    # No rule for '.zzz' → default '*' → last two labels.
    assert registrable_domain("host.example.zzz") == "example.zzz"


def test_registrable_domain_of_public_suffix_is_none() -> None:
    assert registrable_domain("co.uk") is None
    assert registrable_domain("com") is None


# --- dedup + domain cap ----------------------------------------------------


def test_exact_dedup(tmp_path: Path) -> None:
    uris = ["https://x.test/a", "https://x.test/a", "https://x.test/b"]
    out = list(dedupe_and_cap(uris, workdir=tmp_path, domain_cap=None, num_shards=4))
    assert sorted(out) == ["https://x.test/a", "https://x.test/b"]


def test_per_domain_cap(tmp_path: Path) -> None:
    uris = [f"https://sub{i}.example.com/p" for i in range(10)]
    uris += [f"https://other.org/{i}" for i in range(3)]
    out = list(dedupe_and_cap(uris, workdir=tmp_path, domain_cap=4, num_shards=8))
    example = [u for u in out if "example.com" in u]
    other = [u for u in out if "other.org" in u]
    assert len(example) == 4  # capped
    assert len(other) == 3  # under cap, all kept


def test_cap_is_per_registrable_domain_not_host(tmp_path: Path) -> None:
    # Different subdomains share one registrable domain and one cap budget.
    uris = [
        "https://a.example.com/1",
        "https://b.example.com/2",
        "https://c.example.com/3",
    ]
    out = list(dedupe_and_cap(uris, workdir=tmp_path, domain_cap=2, num_shards=4))
    assert len(out) == 2


def test_no_domain_uris_exempt_from_cap(tmp_path: Path) -> None:
    uris = [f"mailto:user{i}@example.com" for i in range(10)]
    out = list(dedupe_and_cap(uris, workdir=tmp_path, domain_cap=2, num_shards=4))
    assert len(out) == 10  # mailto has no registrable domain → not capped


def test_deterministic_across_shard_counts(tmp_path: Path) -> None:
    uris = [f"https://d{i % 5}.test/{i}" for i in range(50)]
    a = list(dedupe_and_cap(uris, workdir=tmp_path / "a", domain_cap=3, num_shards=4))
    b = list(dedupe_and_cap(uris, workdir=tmp_path / "b", domain_cap=3, num_shards=16))
    # Same set kept regardless of shard count (order may differ across counts).
    assert set(a) == set(b)
    assert len(a) == len(b)


def test_memory_bounded_many_shards(tmp_path: Path) -> None:
    # A large-ish run over many shards exercises the disk-shuffle path.
    uris = [f"https://host{i % 200}.test/{i}" for i in range(5000)]
    out = list(dedupe_and_cap(uris, workdir=tmp_path, domain_cap=10, num_shards=64))
    # 200 domains × cap 10 = 2000 kept.
    assert len(out) == 2000


def test_workers_output_is_identical_to_serial(tmp_path: Path) -> None:
    # Parallel partitioning must be byte-for-byte identical to serial, including
    # the order-sensitive per-domain cap outcome. Mix capped domains, an
    # over-cap domain, dupes, and cap-exempt (mailto) URIs, in a fixed order.
    uris: list[str] = []
    for i in range(500):
        uris.append(f"https://sub{i % 7}.example.com/{i}")  # one domain, over cap
        uris.append(f"https://other{i % 3}.org/{i}")
        uris.append(f"mailto:user{i}@example.net")  # cap-exempt
        if i % 4 == 0:
            uris.append("https://example.com/dupe")  # exact duplicate
    serial = list(dedupe_and_cap(uris, workdir=tmp_path / "s", domain_cap=20, num_shards=8))
    parallel = list(
        dedupe_and_cap(uris, workdir=tmp_path / "p", domain_cap=20, num_shards=8, workers=4)
    )
    assert parallel == serial  # exact order, not just set equality
