"""RFE telemetry re-run pipeline (Task 5.2).

Given a validated RFE submission, run its corpus through the instrumented parser
against the **full** RFC AST (not the MVS), then decide whether the dataset
provides enough hits to push any pruned node over ``MIN_USAGE_PERCENTAGE``. If it
does, assemble an ``overrides.yaml`` update (with the submission as
justification) and the metadata for a pull request restoring those nodes.

The heavy parsing is delegated to the native ``mvs-telemetry`` binary (the
language boundary is the corpus, per ADR 0001); the evaluation and
override-assembly logic is pure and independently tested.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from mvs_pipeline import corpus, ct, schema
from mvs_pipeline.pruner import MIN_USAGE_PERCENTAGE
from rfe_service.ingest import Submission


@dataclass
class RestorableNode:
    """A pruned node the submission's dataset exercises above the threshold."""

    node_id: str
    hits: int
    usage: float


@dataclass
class RfeReport:
    """The outcome of evaluating a submission against the pruned set."""

    grammar: str
    threshold: float
    total_samples: int
    considered: int
    restorable: list[RestorableNode]

    @property
    def has_evidence(self) -> bool:
        """Whether the submission justifies restoring at least one node."""
        return bool(self.restorable)


def evaluate_report(
    pruned_doc: dict[str, Any],
    hits: dict[str, Any],
    threshold: float = MIN_USAGE_PERCENTAGE,
) -> RfeReport:
    """Which pruned nodes does ``hits`` (the submission's telemetry) restore?

    A pruned node is restorable when the submission's own usage fraction for it
    is at or above ``threshold`` — i.e. the submitter has shown it is used in
    their domain.
    """
    total = hits["total_samples"]
    hitmap = hits["hits"]
    restorable: list[RestorableNode] = []
    for node in pruned_doc["pruned"]:
        count = hitmap.get(node, 0)
        usage = (count / total) if total > 0 else 0.0
        if count > 0 and usage >= threshold:
            restorable.append(RestorableNode(node, count, usage))
    restorable.sort(key=lambda r: (-r.usage, r.node_id))
    return RfeReport(
        grammar=pruned_doc["grammar"],
        threshold=threshold,
        total_samples=total,
        considered=len(pruned_doc["pruned"]),
        restorable=restorable,
    )


def apply_to_overrides(
    overrides_doc: dict[str, Any],
    report: RfeReport,
    submission: Submission,
) -> tuple[dict[str, Any], list[str]]:
    """Return an updated override registry plus the node ids newly added.

    Each restorable node gets a protected entry justified by the submission's
    rationale and the observed hit rate. Already-protected nodes are left alone.
    """
    updated: dict[str, Any] = {
        "schema_version": 1,
        "overrides": dict(overrides_doc.get("overrides", {})),
    }
    added: list[str] = []
    for node in report.restorable:
        if node.node_id in updated["overrides"]:
            continue
        updated["overrides"][node.node_id] = {
            "protected": True,
            "justification": (
                f"RFE evidence: {submission.meta['rationale']} "
                f"Observed {node.hits}/{report.total_samples} samples "
                f"({node.usage:.4%}), at or above the {report.threshold:.4%} threshold."
            ),
            "owner": f"rfe:{submission.meta['submitter']}",
        }
        added.append(node.node_id)
    schema.validate("overrides", updated)
    return updated, added


def pr_metadata(report: RfeReport, submission: Submission, added: list[str]) -> dict[str, str]:
    """Branch name, title, and body for the auto-generated overrides PR."""
    branch = f"rfe/restore-{report.grammar}-{len(added)}-node"
    title = f"RFE: restore {len(added)} pruned node(s) in {report.grammar}"
    lines = [
        f"Automated RFE from **{submission.meta['submitter']}**.",
        "",
        f"> {submission.meta['rationale']}",
        "",
        f"Re-running the submitted {report.grammar} corpus "
        f"({report.total_samples} samples) against the full RFC AST cleared the "
        f"{report.threshold:.4%} usage threshold for these pruned nodes:",
        "",
    ]
    lines += [
        f"- `{n.node_id}` — {n.hits}/{report.total_samples} ({n.usage:.4%})"
        for n in report.restorable
    ]
    return {"branch": branch, "title": title, "body": "\n".join(lines)}


def dump_overrides_yaml(overrides_doc: dict[str, Any]) -> str:
    """Serialize an override registry to YAML for committing to overrides.yaml."""
    return yaml.safe_dump(overrides_doc, sort_keys=False, default_flow_style=False)


def run_submission_telemetry(
    submission: Submission,
    ast_path: str | Path,
    *,
    binary: str | Path = "mvs-telemetry",
    workdir: str | Path | None = None,
) -> dict[str, Any]:
    """Run the submission corpus against ``ast_path`` via ``mvs-telemetry``.

    Returns the parsed ``hits.json``. Raises ``subprocess.CalledProcessError`` if
    the binary fails.
    """
    tmp = Path(workdir) if workdir is not None else Path(tempfile.mkdtemp(prefix="rfe-rerun-"))
    out = tmp / "hits.json"
    if submission.kind == "uri":
        corpus_path = tmp / "uris.txt"
        corpus.write_corpus(submission.uris, corpus_path, deduplicate=False)
        argv = corpus.telemetry_argv(binary, ast_path, corpus_path, out)
    else:
        cert_dir = tmp / "certs"
        ct.write_cert_dir(submission.certs, cert_dir)
        argv = ct.telemetry_der_argv(binary, ast_path, cert_dir, out)
    subprocess.run(argv, check=True, capture_output=True)
    return json.loads(out.read_text())


def report_to_dict(report: RfeReport) -> dict[str, Any]:
    """Serialize an [`RfeReport`] for writing to disk."""
    return {
        "grammar": report.grammar,
        "threshold": report.threshold,
        "total_samples": report.total_samples,
        "considered": report.considered,
        "has_evidence": report.has_evidence,
        "restorable": [
            {"node_id": r.node_id, "hits": r.hits, "usage": r.usage} for r in report.restorable
        ],
    }


def _main(argv: list[str] | None = None) -> int:
    import argparse

    from mvs_pipeline import overrides as overrides_mod
    from mvs_pipeline.schema import load_document
    from rfe_service.ingest import load_submission

    parser = argparse.ArgumentParser(description="Evaluate an RFE submission.")
    parser.add_argument("--submission", type=Path, required=True, help="submission zip")
    parser.add_argument("--ast", type=Path, required=True, help="the FULL RFC AST")
    parser.add_argument("--pruned", type=Path, required=True)
    parser.add_argument("--overrides", type=Path, default=None)
    parser.add_argument("--threshold", type=float, default=MIN_USAGE_PERCENTAGE)
    parser.add_argument("--binary", default="mvs-telemetry")
    parser.add_argument("--out-report", type=Path, required=True)
    parser.add_argument("--out-overrides", type=Path, default=None)
    parser.add_argument("--out-pr", type=Path, default=None)
    args = parser.parse_args(argv)

    submission = load_submission(args.submission)
    pruned_doc = load_document(args.pruned)
    hits = run_submission_telemetry(submission, args.ast, binary=args.binary)
    report = evaluate_report(pruned_doc, hits, args.threshold)

    args.out_report.parent.mkdir(parents=True, exist_ok=True)
    args.out_report.write_text(json.dumps(report_to_dict(report), indent=2, sort_keys=True) + "\n")
    print(f"grammar={report.grammar} restorable={len(report.restorable)}/{report.considered}")

    if report.has_evidence and args.out_overrides is not None:
        overrides_doc = overrides_mod.load_overrides(args.overrides)
        updated, added = apply_to_overrides(overrides_doc, report, submission)
        args.out_overrides.write_text(dump_overrides_yaml(updated))
        meta = pr_metadata(report, submission, added)
        if args.out_pr is not None:
            args.out_pr.write_text(json.dumps(meta, indent=2) + "\n")
        print(f"proposed PR: {meta['branch']} — {meta['title']}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
