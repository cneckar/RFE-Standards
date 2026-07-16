"""Tests for the stratified sampler, quota controller, and manifest (T6.5)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from mvs_pipeline.collector.sampler import (
    Stratum,
    allocate_quotas,
    stratified_sample,
)


class ListSource:
    """A trivial in-memory Source for tests."""

    def __init__(self, name: str, uris: list[str]) -> None:
        self.name = name
        self._uris = uris

    def iter_uris(self) -> Iterator[str]:
        yield from self._uris

    def provenance(self) -> dict:
        return {"source": self.name, "count": len(self._uris)}


# --- quota allocation ------------------------------------------------------


def test_allocate_quotas_sums_exactly() -> None:
    assert sum(allocate_quotas([0.7, 0.2, 0.1], 100)) == 100
    assert allocate_quotas([0.7, 0.2, 0.1], 100) == [70, 20, 10]


def test_allocate_quotas_largest_remainder() -> None:
    # 10 units, equal thirds → 4/3/3 (remainder to earliest ties).
    assert allocate_quotas([1, 1, 1], 10) == [4, 3, 3]


def test_allocate_quotas_rejects_zero_total() -> None:
    with pytest.raises(ValueError, match="positive"):
        allocate_quotas([0, 0], 5)


# --- stratified sampling ---------------------------------------------------


def _strata() -> list[Stratum]:
    a = ListSource("alpha", [f"https://a{i}.test/{i}" for i in range(100)])
    b = ListSource("beta", [f"https://b{i}.test/{i}" for i in range(100)])
    return [Stratum(a, 0.7), Stratum(b, 0.3)]


def test_hits_exact_target_and_quotas(tmp_path: Path) -> None:
    res = stratified_sample(
        _strata(),
        target_n=50,
        workdir=tmp_path / "w",
        out_dir=tmp_path / "o",
        seed=1,
        domain_cap=None,
    )
    assert res.total_written == 50
    assert [s.kept for s in res.strata] == [35, 15]  # 70/30 of 50


def test_reproducible_same_seed(tmp_path: Path) -> None:
    r1 = stratified_sample(
        _strata(), target_n=40, workdir=tmp_path / "w1", out_dir=tmp_path / "o1", seed=7
    )
    r2 = stratified_sample(
        _strata(), target_n=40, workdir=tmp_path / "w2", out_dir=tmp_path / "o2", seed=7
    )
    c1 = (tmp_path / "o1" / "corpus-000.txt").read_text()
    c2 = (tmp_path / "o2" / "corpus-000.txt").read_text()
    assert c1 == c2
    assert r1.total_written == r2.total_written == 40


def test_different_seed_changes_selection(tmp_path: Path) -> None:
    stratified_sample(
        _strata(), target_n=40, workdir=tmp_path / "w1", out_dir=tmp_path / "o1", seed=1
    )
    stratified_sample(
        _strata(), target_n=40, workdir=tmp_path / "w2", out_dir=tmp_path / "o2", seed=2
    )
    a = _all_uris(tmp_path / "o1")
    b = _all_uris(tmp_path / "o2")
    assert a != b


def test_writes_shards_and_manifest(tmp_path: Path) -> None:
    res = stratified_sample(
        _strata(),
        target_n=20,
        workdir=tmp_path / "w",
        out_dir=tmp_path / "o",
        seed=3,
        num_output_shards=4,
        domain_cap=None,
    )
    # All target URIs are distributed across the shard files.
    assert len(res.shards) == 4
    assert len(_all_uris(tmp_path / "o")) == 20

    manifest = json.loads((tmp_path / "o" / "manifest.json").read_text())
    assert manifest["schema_version"] == 1
    assert manifest["seed"] == 3
    assert manifest["target_n"] == 20
    assert manifest["total_written"] == 20
    assert [s["name"] for s in manifest["strata"]] == ["alpha", "beta"]
    assert manifest["strata"][0]["provenance"]["source"] == "alpha"
    assert sum(s["kept"] for s in manifest["strata"]) == 20


def test_domain_cap_limits_a_mega_site(tmp_path: Path) -> None:
    # One domain dominates; cap should stop it from filling the whole quota.
    big = ListSource("mega", [f"https://mega.test/{i}" for i in range(100)])
    small = ListSource("div", [f"https://div{i}.test/{i}" for i in range(100)])
    stratified_sample(
        [Stratum(big, 0.5), Stratum(small, 0.5)],
        target_n=40,
        workdir=tmp_path / "w",
        out_dir=tmp_path / "o",
        seed=5,
        domain_cap=5,
    )
    kept_mega = [u for u in _all_uris(tmp_path / "o") if "mega.test" in u]
    assert len(kept_mega) <= 5


def _all_uris(out_dir: Path) -> list[str]:
    uris: list[str] = []
    for p in sorted(out_dir.glob("corpus-*.txt")):
        uris += [ln for ln in p.read_text().splitlines() if ln]
    return uris
