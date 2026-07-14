import shutil
from pathlib import Path

import pytest

from rfe_service import rerun
from rfe_service.ingest import Submission

ROOT = Path(__file__).resolve().parents[2]

A = "rfc3986-uri:a#00000001"
B = "rfc3986-uri:b#00000002"
PRUNED = {"grammar": "rfc3986-uri", "pruned": [A, B]}


def _submission(**over):
    meta = {
        "schema_version": 1,
        "grammar": "rfc3986-uri",
        "kind": "uri",
        "submitter": "Ada <ada@example.org>",
        "rationale": "Our traffic exercises this feature.",
        **over,
    }
    return Submission(meta=meta, uris=["http://a.example/"])


# --- evaluation ------------------------------------------------------------ #


def test_node_above_threshold_is_restorable():
    hits = {"total_samples": 1000, "hits": {A: 5}}  # 0.5% >= 0.1%
    report = rerun.evaluate_report(PRUNED, hits)
    assert report.has_evidence
    assert [r.node_id for r in report.restorable] == [A]
    assert report.restorable[0].hits == 5
    assert report.considered == 2


def test_node_below_threshold_is_not_restorable():
    hits = {"total_samples": 1000, "hits": {A: 5}}
    report = rerun.evaluate_report(PRUNED, hits, threshold=0.01)  # 1% > 0.5%
    assert not report.has_evidence


def test_empty_corpus_restores_nothing():
    report = rerun.evaluate_report(PRUNED, {"total_samples": 0, "hits": {}})
    assert report.restorable == []


# --- overrides assembly ---------------------------------------------------- #


def test_apply_to_overrides_adds_justified_entries():
    hits = {"total_samples": 1000, "hits": {A: 50}}
    report = rerun.evaluate_report(PRUNED, hits)
    updated, added = rerun.apply_to_overrides(
        {"schema_version": 1, "overrides": {}}, report, _submission()
    )
    assert added == [A]
    entry = updated["overrides"][A]
    assert entry["protected"] is True
    assert "RFE evidence" in entry["justification"]
    assert "Our traffic" in entry["justification"]
    assert entry["owner"].startswith("rfe:")
    # Must remain schema-valid (this is what the auto-PR commits).
    from mvs_pipeline import schema

    schema.validate("overrides", updated)


def test_apply_skips_already_protected():
    hits = {"total_samples": 1000, "hits": {A: 50}}
    report = rerun.evaluate_report(PRUNED, hits)
    existing = {
        "schema_version": 1,
        "overrides": {A: {"protected": True, "justification": "prior", "owner": "sec"}},
    }
    updated, added = rerun.apply_to_overrides(existing, report, _submission())
    assert added == []
    assert updated["overrides"][A]["justification"] == "prior"


def test_pr_metadata_and_yaml_dump():
    hits = {"total_samples": 1000, "hits": {A: 50}}
    report = rerun.evaluate_report(PRUNED, hits)
    updated, added = rerun.apply_to_overrides(
        {"schema_version": 1, "overrides": {}}, report, _submission()
    )
    meta = rerun.pr_metadata(report, _submission(), added)
    assert meta["branch"].startswith("rfe/restore-rfc3986-uri")
    assert "restore 1" in meta["title"]
    assert A in meta["body"]

    text = rerun.dump_overrides_yaml(updated)
    assert "schema_version" in text
    assert A in text


# --- end-to-end through the native binary (skipped if not built) ----------- #


def _telemetry_binary() -> Path | None:
    for profile in ("release", "debug"):
        for name in ("mvs-telemetry", "mvs-telemetry.exe"):
            candidate = ROOT / "core" / "target" / profile / name
            if candidate.exists():
                return candidate
    found = shutil.which("mvs-telemetry")
    return Path(found) if found else None


def test_end_to_end_restores_a_pruned_node():
    binary = _telemetry_binary()
    if binary is None:
        pytest.skip("mvs-telemetry binary not built")

    import json

    ast_path = ROOT / "artifacts" / "rfc3986-uri.ast.json"
    pruned_doc = json.loads((ROOT / "artifacts" / "rfc3986-uri.pruned.json").read_text())
    # A digit in the scheme exercises a node the alpha-only sample corpus pruned.
    submission = Submission(
        meta={
            "schema_version": 1,
            "grammar": "rfc3986-uri",
            "kind": "uri",
            "submitter": "team@example.org",
            "rationale": "Our schemes contain digits (e.g. h2t).",
        },
        uris=["h2t://example.com/", "s3x://bucket/key"],
    )
    hits = rerun.run_submission_telemetry(submission, ast_path, binary=binary)
    report = rerun.evaluate_report(pruned_doc, hits)
    assert report.has_evidence
    # Everything restorable was actually in the pruned set.
    assert {r.node_id for r in report.restorable} <= set(pruned_doc["pruned"])

    updated, added = rerun.apply_to_overrides(
        {"schema_version": 1, "overrides": {}}, report, submission
    )
    assert added
