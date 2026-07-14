//! Instrumented parser core for the MVS/RFE framework.
//!
//! The [`telemetry`] module houses the throughput-critical parser that traces
//! execution paths against a Phase-1 AST and tallies node-hit rates (Task 2.1).
//! This root module defines the foundational node-identity type that the
//! Python↔Rust artifact contract (Task 0.2) is built around.

pub mod telemetry;

pub use telemetry::{CompileError, Grammar, HitAggregator, ParseResult};

/// Stable identifier for a single grammar/schema node in an AST.
///
/// The concrete construction (rule name + structural path hash) is frozen in
/// Task 0.2; this type is the shared handle the telemetry engine keys hit
/// counts on.
pub type NodeId = String;

/// A tally of how many times each AST node was traversed while parsing a corpus.
#[derive(Debug, Default, Clone, PartialEq, Eq)]
pub struct HitCounts {
    counts: std::collections::BTreeMap<NodeId, u64>,
    /// Total number of inputs (samples) fed through the parser.
    pub total_samples: u64,
}

impl HitCounts {
    /// Create an empty tally.
    pub fn new() -> Self {
        Self::default()
    }

    /// Record that `node` was traversed once.
    pub fn record(&mut self, node: impl Into<NodeId>) {
        *self.counts.entry(node.into()).or_insert(0) += 1;
    }

    /// Mark that one input sample has been fully processed.
    pub fn finish_sample(&mut self) {
        self.total_samples += 1;
    }

    /// Hit count for a given node (0 if never traversed).
    pub fn get(&self, node: &str) -> u64 {
        self.counts.get(node).copied().unwrap_or(0)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn records_and_totals() {
        let mut hits = HitCounts::new();
        hits.record("uri.host");
        hits.record("uri.host");
        hits.record("uri.userinfo");
        hits.finish_sample();

        assert_eq!(hits.get("uri.host"), 2);
        assert_eq!(hits.get("uri.userinfo"), 1);
        assert_eq!(hits.get("uri.pct-encoded"), 0);
        assert_eq!(hits.total_samples, 1);
    }
}
