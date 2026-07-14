//! Integration test for the `mvs-telemetry` binary: run it over the committed
//! sample corpus and check the emitted hits, including reproducibility of the
//! committed `artifacts/rfc3986-uri.hits.json`.

use std::path::PathBuf;
use std::process::Command;

use mvs_schema::Hits;

fn repo_path(rel: &str) -> PathBuf {
    PathBuf::from(concat!(env!("CARGO_MANIFEST_DIR"), "/../../../")).join(rel)
}

fn run_cli(out: &PathBuf) -> Hits {
    let status = Command::new(env!("CARGO_BIN_EXE_mvs-telemetry"))
        .arg("--ast")
        .arg(repo_path("artifacts/rfc3986-uri.ast.json"))
        .arg("--corpus")
        .arg(repo_path("corpus/uri-sample.txt"))
        .arg("--out")
        .arg(out)
        .status()
        .expect("run mvs-telemetry");
    assert!(status.success(), "cli exited with failure");
    let text = std::fs::read_to_string(out).expect("read hits output");
    serde_json::from_str(&text).expect("parse hits output")
}

#[test]
fn emits_valid_hits_over_sample_corpus() {
    let out = std::env::temp_dir().join(format!("mvs_hits_{}.json", std::process::id()));
    let hits = run_cli(&out);

    // The sample corpus has 14 active (non-comment) lines, all valid URIs.
    assert_eq!(hits.schema_version, 1);
    assert_eq!(hits.grammar, "rfc3986-uri");
    assert_eq!(hits.total_samples, 14);
    assert!(!hits.hits.is_empty());
    // No node can be credited more than once per sample.
    assert!(hits.hits.values().all(|&c| c <= hits.total_samples));

    // The corpus includes "%2e%2e" so the pct-encoded rule must have been hit.
    let pct_hits: u64 = hits
        .hits
        .iter()
        .filter(|(id, _)| id.contains("pct-encoded"))
        .map(|(_, &c)| c)
        .sum();
    assert!(pct_hits >= 1, "expected pct-encoded to be exercised");

    let _ = std::fs::remove_file(&out);
}

#[test]
fn committed_hits_artifact_is_reproducible() {
    let out = std::env::temp_dir().join(format!("mvs_hits_repro_{}.json", std::process::id()));
    let fresh = run_cli(&out);

    let committed_text = std::fs::read_to_string(repo_path("artifacts/rfc3986-uri.hits.json"))
        .expect("read committed hits");
    let committed: Hits = serde_json::from_str(&committed_text).expect("parse committed hits");

    assert_eq!(committed.total_samples, fresh.total_samples);
    assert_eq!(committed.grammar, fresh.grammar);
    assert_eq!(committed.hits, fresh.hits);

    let _ = std::fs::remove_file(&out);
}
