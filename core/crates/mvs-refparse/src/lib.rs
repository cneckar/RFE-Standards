//! Native reference parsers for the MVS/RFE framework (Phase 4).
//!
//! A reference parser accepts exactly the Minimum Viable Standard: an input is
//! valid only if it can be recognized *without* exercising any node that the
//! pruner removed. The parser compiles the full Phase-1 AST (so it can still
//! recognize the shape of legacy input) but treats the pruned set as a deny
//! list, so encountering a pruned feature fails fast with a specific
//! [`MvsError::UnsupportedNode`] rather than silently accepting legacy bloat.
//!
//! These parsers are the shipped deliverable: they build for every target in the
//! CI cross-compilation matrix and depend only on `std`.

use std::collections::HashSet;

use mvs_core::{DerWalker, Grammar, NodeId};
use mvs_schema::Ast;

/// Errors a reference parser raises. The MVS refuses to degrade gracefully into
/// legacy formats — it fails fast with a specific reason.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum MvsError {
    /// The input could not be recognized by the grammar at all.
    Malformed,
    /// The input exercised a grammar/schema node that was pruned from the MVS.
    UnsupportedNode(NodeId),
    /// The input exceeded a strict bound (length, depth, count).
    BoundsExceeded {
        /// Which bound was violated (e.g. `"max_depth"`).
        bound: &'static str,
    },
}

impl std::fmt::Display for MvsError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            MvsError::Malformed => write!(f, "ERR_MVS_MALFORMED"),
            MvsError::UnsupportedNode(node) => write!(f, "ERR_MVS_UNSUPPORTED_NODE: {node}"),
            MvsError::BoundsExceeded { bound } => write!(f, "ERR_MVS_BOUNDS_EXCEEDED: {bound}"),
        }
    }
}

impl std::error::Error for MvsError {}

/// Strict resource bounds a reference parser enforces before and during parsing.
///
/// The MVS rejects oversized or pathologically-nested input up front rather than
/// attempting to parse it — no partial recovery, no fallback.
#[derive(Debug, Clone, Copy)]
pub struct Limits {
    /// Maximum accepted input length in bytes.
    pub max_input_len: usize,
    /// Maximum recognizer recursion / value-nesting depth.
    pub max_depth: usize,
}

impl Limits {
    /// Defaults for text grammars (URIs): 8 KiB input, depth 16384.
    pub const TEXT: Limits = Limits {
        max_input_len: 8 * 1024,
        max_depth: 16_384,
    };
    /// Defaults for DER schemas (certificates): 64 KiB input, nesting depth 1024.
    pub const DER: Limits = Limits {
        max_input_len: 64 * 1024,
        max_depth: 1024,
    };
}

/// Shared logic: given the nodes a successful parse traversed, reject the input
/// if any of them was pruned from the MVS.
fn first_pruned<'a>(visited: &'a [NodeId], pruned: &HashSet<NodeId>) -> Option<&'a NodeId> {
    visited.iter().find(|node| pruned.contains(*node))
}

/// A native reference parser for a text grammar (e.g. RFC 3986 URIs).
pub struct MvsTextParser {
    grammar: Grammar,
    pruned: HashSet<NodeId>,
    limits: Limits,
}

impl MvsTextParser {
    /// Compile a reference parser with the default text [`Limits`].
    pub fn compile(ast: &Ast, pruned: &[NodeId]) -> Result<Self, mvs_core::CompileError> {
        Self::compile_with(ast, pruned, Limits::TEXT)
    }

    /// Compile a reference parser with explicit resource bounds.
    pub fn compile_with(
        ast: &Ast,
        pruned: &[NodeId],
        limits: Limits,
    ) -> Result<Self, mvs_core::CompileError> {
        Ok(Self {
            grammar: Grammar::compile(ast)?,
            pruned: pruned.iter().cloned().collect(),
            limits,
        })
    }

    /// Validate `input` against the MVS. On success, returns the traversed nodes.
    pub fn validate(&self, input: &[u8]) -> Result<Vec<NodeId>, MvsError> {
        if input.len() > self.limits.max_input_len {
            return Err(MvsError::BoundsExceeded {
                bound: "max_input_len",
            });
        }
        let result = self.grammar.parse_bounded(input, self.limits.max_depth);
        if result.depth_exceeded {
            return Err(MvsError::BoundsExceeded { bound: "max_depth" });
        }
        if !result.matched {
            return Err(MvsError::Malformed);
        }
        if let Some(node) = first_pruned(&result.visited, &self.pruned) {
            return Err(MvsError::UnsupportedNode(node.clone()));
        }
        Ok(result.visited)
    }
}

/// A native reference parser for a DER schema (e.g. RFC 5280 certificates).
pub struct MvsCertParser {
    ast: Ast,
    pruned: HashSet<NodeId>,
    limits: Limits,
}

impl MvsCertParser {
    /// Build a reference parser with the default DER [`Limits`].
    pub fn new(ast: Ast, pruned: &[NodeId]) -> Self {
        Self::with_limits(ast, pruned, Limits::DER)
    }

    /// Build a reference parser with explicit resource bounds.
    pub fn with_limits(ast: Ast, pruned: &[NodeId], limits: Limits) -> Self {
        Self {
            ast,
            pruned: pruned.iter().cloned().collect(),
            limits,
        }
    }

    /// Validate a DER encoding against the MVS. On success, returns the nodes.
    pub fn validate(&self, der: &[u8]) -> Result<Vec<NodeId>, MvsError> {
        if der.len() > self.limits.max_input_len {
            return Err(MvsError::BoundsExceeded {
                bound: "max_input_len",
            });
        }
        let result = DerWalker::with_max_depth(&self.ast, self.limits.max_depth).walk(der);
        if result.depth_exceeded {
            return Err(MvsError::BoundsExceeded { bound: "max_depth" });
        }
        if !result.matched {
            return Err(MvsError::Malformed);
        }
        if let Some(node) = first_pruned(&result.visited, &self.pruned) {
            return Err(MvsError::UnsupportedNode(node.clone()));
        }
        Ok(result.visited)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn uri_parser() -> MvsTextParser {
        let ast: Ast = serde_json::from_str(include_str!(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../../artifacts/rfc3986-uri.ast.json"
        )))
        .unwrap();
        let pruned: mvs_schema::Pruned = serde_json::from_str(include_str!(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../../artifacts/rfc3986-uri.pruned.json"
        )))
        .unwrap();
        MvsTextParser::compile(&ast, &pruned.pruned).unwrap()
    }

    #[test]
    fn accepts_mvs_conforming_uri() {
        assert!(uri_parser().validate(b"http://example.com/").is_ok());
    }

    #[test]
    fn rejects_legacy_feature_with_specific_error() {
        // The sample corpus had only alphabetic schemes, so a digit in the
        // scheme exercises a pruned alternative -> ERR_MVS_UNSUPPORTED_NODE.
        let err = uri_parser().validate(b"http2://example.com/").unwrap_err();
        match err {
            MvsError::UnsupportedNode(node) => assert!(node.starts_with("rfc3986-uri:")),
            other => panic!("expected UnsupportedNode, got {other}"),
        }
    }

    #[test]
    fn rejects_malformed_input() {
        assert_eq!(
            uri_parser().validate(b"has spaces").unwrap_err(),
            MvsError::Malformed
        );
    }

    fn uri_parser_with(limits: Limits) -> MvsTextParser {
        let ast: Ast = serde_json::from_str(include_str!(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../../artifacts/rfc3986-uri.ast.json"
        )))
        .unwrap();
        let pruned: mvs_schema::Pruned = serde_json::from_str(include_str!(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../../artifacts/rfc3986-uri.pruned.json"
        )))
        .unwrap();
        MvsTextParser::compile_with(&ast, &pruned.pruned, limits).unwrap()
    }

    #[test]
    fn rejects_oversized_input() {
        let parser = uri_parser_with(Limits {
            max_input_len: 10,
            max_depth: 16_384,
        });
        assert_eq!(
            parser.validate(b"http://example.com/").unwrap_err(),
            MvsError::BoundsExceeded {
                bound: "max_input_len"
            }
        );
    }

    #[test]
    fn rejects_excessive_depth() {
        // The URI grammar nests deeper than 5 frames, so a tiny depth bound trips
        // before the input can be recognized.
        let parser = uri_parser_with(Limits {
            max_input_len: 8192,
            max_depth: 5,
        });
        assert_eq!(
            parser.validate(b"http://a/").unwrap_err(),
            MvsError::BoundsExceeded { bound: "max_depth" }
        );
    }

    #[test]
    fn default_limits_accept_a_normal_uri() {
        let parser = uri_parser_with(Limits::TEXT);
        assert!(parser
            .validate(b"https://user@host.example:8443/a/b?q=1#f")
            .is_ok());
    }

    #[test]
    fn cert_parser_accepts_sample_certificate() {
        let ast: Ast = serde_json::from_str(include_str!(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../../artifacts/rfc5280-x509.ast.json"
        )))
        .unwrap();
        let pruned: mvs_schema::Pruned = serde_json::from_str(include_str!(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../../artifacts/rfc5280-x509.pruned.json"
        )))
        .unwrap();
        let parser = MvsCertParser::new(ast, &pruned.pruned);
        let der = include_bytes!(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../../corpus/certs/sample-cert.der"
        ));
        assert!(parser.validate(der).is_ok());
    }

    #[test]
    fn error_messages_are_specific() {
        assert_eq!(MvsError::Malformed.to_string(), "ERR_MVS_MALFORMED");
        assert_eq!(
            MvsError::UnsupportedNode("rfc3986-uri:DIGIT#00000000".into()).to_string(),
            "ERR_MVS_UNSUPPORTED_NODE: rfc3986-uri:DIGIT#00000000"
        );
        assert_eq!(
            MvsError::BoundsExceeded { bound: "max_depth" }.to_string(),
            "ERR_MVS_BOUNDS_EXCEEDED: max_depth"
        );
    }
}
