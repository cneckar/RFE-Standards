//! Typed Rust view of the MVS/RFE artifact spine (Task 0.2).
//!
//! These structs mirror the JSON Schemas in `schemas/` and are the Rust half of
//! the Python/Rust contract. Every type uses `deny_unknown_fields` so an
//! artifact carrying keys outside the schema fails to deserialize — the Rust
//! core validates structure by construction rather than by running a JSON Schema
//! validator. The authoritative JSON Schema validation lives on the Python side.

use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};

/// Stable identifier for a node: `<grammar>:<rule-path>#<hash8>`.
pub type NodeId = String;

/// Byte range into the source grammar text a node was extracted from.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Span {
    /// Start byte offset (inclusive).
    pub start: u64,
    /// End byte offset (exclusive).
    pub end: u64,
    /// Optional human-readable source label.
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub source: Option<String>,
}

/// Structural role of a node in the grammar/schema. Matches the `kind` enum in
/// `ast.schema.json`; an unknown kind fails to deserialize.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum Kind {
    /// A named grammar rule / production.
    Rule,
    /// A reference to another rule.
    Reference,
    /// A literal terminal.
    Terminal,
    /// An ordered concatenation.
    Sequence,
    /// A choice between alternatives.
    Alternation,
    /// An optional element.
    Optional,
    /// A repeated element.
    Repetition,
    /// A parenthesised grouping.
    Group,
    /// An ASN.1 tag.
    Tag,
    /// An ASN.1 string type.
    StringType,
    /// An ASN.1 named type.
    NamedType,
}

/// A single grammar rule / ASN.1 construct.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Node {
    /// Structural role of this node.
    pub kind: Kind,
    /// Human-readable rule/field name.
    pub name: String,
    /// Ordered node ids of direct constituents.
    #[serde(default)]
    pub children: Vec<NodeId>,
    /// Source span, when known.
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub span: Option<Span>,
}

/// Provenance of the grammar text an AST was extracted from.
#[derive(Debug, Clone, PartialEq, Eq, Default, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Source {
    /// RFC identifier, e.g. `"RFC 3986"`.
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub rfc: Option<String>,
    /// Canonical URI of the source document.
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub uri: Option<String>,
    /// Section within the document.
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub section: Option<String>,
}

/// A grammar decomposed into uniquely-identified nodes (`ast.schema.json`).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Ast {
    /// Contract version (currently always 1).
    pub schema_version: u32,
    /// Grammar identifier, e.g. `"rfc3986-uri"`.
    pub grammar: String,
    /// Provenance of the grammar text.
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub source: Option<Source>,
    /// Node id of the start symbol; must be a key in `nodes`.
    pub root: NodeId,
    /// Every node keyed by its unique id.
    pub nodes: BTreeMap<NodeId, Node>,
}

impl Ast {
    /// Check the referential integrity the JSON Schema cannot express: the root
    /// exists and every child reference points at a known node.
    pub fn validate_references(&self) -> Result<(), String> {
        if !self.nodes.contains_key(&self.root) {
            return Err(format!("root {} is not present in nodes", self.root));
        }
        for (id, node) in &self.nodes {
            for child in &node.children {
                if !self.nodes.contains_key(child) {
                    return Err(format!("node {id} references unknown child {child}"));
                }
            }
        }
        Ok(())
    }
}

/// Aggregated telemetry hits for a grammar (`hits.schema.json`).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Hits {
    /// Contract version.
    pub schema_version: u32,
    /// Grammar these hits were measured against.
    pub grammar: String,
    /// Number of inputs parsed — the usage-fraction denominator.
    pub total_samples: u64,
    /// Traversal count per node id (absent = 0).
    pub hits: BTreeMap<NodeId, u64>,
}

impl Hits {
    /// Share of samples that traversed `node` (0.0 for an empty corpus).
    pub fn usage_fraction(&self, node: &str) -> f64 {
        if self.total_samples == 0 {
            return 0.0;
        }
        self.hits.get(node).copied().unwrap_or(0) as f64 / self.total_samples as f64
    }
}

/// A single criticality-override record.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Override {
    /// Whether the node survives pruning regardless of usage.
    pub protected: bool,
    /// Why the node is protected (required, non-empty).
    pub justification: String,
    /// Accountable person or team.
    pub owner: String,
}

/// The Criticality Override Registry (`overrides.schema.json` / `overrides.yaml`).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Overrides {
    /// Contract version.
    pub schema_version: u32,
    /// Override records keyed by node id.
    pub overrides: BTreeMap<NodeId, Override>,
}

impl Overrides {
    /// Whether `node` is present and marked protected.
    pub fn is_protected(&self, node: &str) -> bool {
        self.overrides.get(node).is_some_and(|o| o.protected)
    }
}

/// The pruner's output (`pruned.schema.json`).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Pruned {
    /// Contract version.
    pub schema_version: u32,
    /// Grammar identifier.
    pub grammar: String,
    /// The `MIN_USAGE_PERCENTAGE` applied (usage fraction, 0..1).
    pub threshold: f64,
    /// Node ids removed from the standard.
    pub pruned: Vec<NodeId>,
    /// Path/URI of the surviving minified standard.
    pub surviving_grammar: String,
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture(rel: &str) -> String {
        let path = concat!(env!("CARGO_MANIFEST_DIR"), "/../../../schemas/examples/");
        std::fs::read_to_string(format!("{path}{rel}"))
            .unwrap_or_else(|e| panic!("read fixture {rel}: {e}"))
    }

    #[test]
    fn ast_parses_roundtrips_and_is_consistent() {
        let ast: Ast = serde_json::from_str(&fixture("uri.ast.json")).unwrap();
        ast.validate_references().unwrap();
        assert_eq!(ast.schema_version, 1);
        assert_eq!(ast.root, "rfc3986-uri:URI#a1b2c3d4");
        assert_eq!(ast.nodes["rfc3986-uri:host#8e9f0a1b"].kind, Kind::Rule);

        // Round-trip: serialize then reparse must equal the original.
        let reparsed: Ast = serde_json::from_str(&serde_json::to_string(&ast).unwrap()).unwrap();
        assert_eq!(ast, reparsed);
    }

    #[test]
    fn hits_roundtrip_and_usage_fraction() {
        let hits: Hits = serde_json::from_str(&fixture("uri.hits.json")).unwrap();
        let reparsed: Hits = serde_json::from_str(&serde_json::to_string(&hits).unwrap()).unwrap();
        assert_eq!(hits, reparsed);

        assert_eq!(hits.usage_fraction("rfc3986-uri:URI#a1b2c3d4"), 1.0);
        // userinfo: 40 / 1_000_000
        assert!(hits.usage_fraction("rfc3986-uri:userinfo#4a5b6c7d") < 0.001);
        // absent node -> 0
        assert_eq!(hits.usage_fraction("rfc3986-uri:nope#00000000"), 0.0);
    }

    #[test]
    fn overrides_roundtrip_via_yaml() {
        let ov: Overrides = serde_yaml::from_str(&fixture("overrides.yaml")).unwrap();
        let reparsed: Overrides =
            serde_yaml::from_str(&serde_yaml::to_string(&ov).unwrap()).unwrap();
        assert_eq!(ov, reparsed);
        assert!(ov.is_protected("rfc3986-uri:pct-encoded#6a7b8c9d"));
    }

    #[test]
    fn pruned_roundtrips() {
        let pruned: Pruned = serde_json::from_str(&fixture("uri.pruned.json")).unwrap();
        let reparsed: Pruned =
            serde_json::from_str(&serde_json::to_string(&pruned).unwrap()).unwrap();
        assert_eq!(pruned, reparsed);
        assert_eq!(pruned.pruned, vec!["rfc3986-uri:userinfo#4a5b6c7d"]);
    }

    #[test]
    fn scenario_pruning_decision_is_coherent() {
        let hits: Hits = serde_json::from_str(&fixture("uri.hits.json")).unwrap();
        let overrides: Overrides = serde_yaml::from_str(&fixture("overrides.yaml")).unwrap();
        let pruned: Pruned = serde_json::from_str(&fixture("uri.pruned.json")).unwrap();

        // Everything pruned was below threshold and not protected.
        for id in &pruned.pruned {
            assert!(hits.usage_fraction(id) < pruned.threshold);
            assert!(!overrides.is_protected(id));
        }

        // pct-encoded is below threshold but protected -> not pruned.
        let pct = "rfc3986-uri:pct-encoded#6a7b8c9d";
        assert!(hits.usage_fraction(pct) < pruned.threshold);
        assert!(overrides.is_protected(pct));
        assert!(!pruned.pruned.contains(&pct.to_string()));
    }

    #[test]
    fn override_without_justification_is_rejected() {
        let bad = fixture("invalid/overrides-missing-justification.yaml");
        assert!(serde_yaml::from_str::<Overrides>(&bad).is_err());
    }

    #[test]
    fn unknown_kind_is_rejected() {
        let json = r#"{"kind":"macguffin","name":"x"}"#;
        assert!(serde_json::from_str::<Node>(json).is_err());
    }

    #[test]
    fn unknown_field_is_rejected() {
        let json = r#"{"kind":"rule","name":"x","bogus":true}"#;
        assert!(serde_json::from_str::<Node>(json).is_err());
    }
}
