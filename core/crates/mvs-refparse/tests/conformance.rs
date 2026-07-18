//! Conformance of the reference parser to the *published* MVS spec.
//!
//! Compiles `MvsTextParser` from the shipped `spec/rfc3986-uri/` artifacts (not
//! the small test fixtures under `artifacts/`) and pins the exact boundary the
//! 9.3M-URI corpus drew: what a conformant URI parser must accept, and what it
//! must reject — and reject with a *specific* reason, never by degrading.

use mvs_refparse::{MvsError, MvsTextParser};
use mvs_schema::{Ast, Pruned};

fn spec_parser() -> MvsTextParser {
    let ast: Ast = serde_json::from_str(include_str!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../../artifacts/rfc3986-uri.ast.json"
    )))
    .unwrap();
    let pruned: Pruned = serde_json::from_str(include_str!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../../spec/rfc3986-uri/pruned.json"
    )))
    .unwrap();
    MvsTextParser::compile(&ast, &pruned.pruned).unwrap()
}

/// URIs the MVS must accept — the shape and features the corpus (and the override
/// floor) established as in-spec.
#[test]
fn spec_accepts_in_scope_uris() {
    let p = spec_parser();
    for uri in [
        "https://user@host.example:8443/a/b?q=1#f", // userinfo + port + fragment
        "s3://bucket/key",                          // digit scheme (boundary: kept)
        "ms-word://open/doc",                       // hyphen scheme (boundary: kept)
        "http://[::1]/",                            // IPv6 literal host (override floor)
        "http://192.168.0.1/",                      // IPv4 literal host (override floor)
        "https://example.com/~user/file",           // "~" unreserved
        "https://example.com/p%2Fq",                // pct-encoded
    ] {
        assert!(
            p.validate(uri.as_bytes()).is_ok(),
            "expected accept for {uri:?}"
        );
    }
}

/// Legacy-but-unused features RFC 3986 allows: the MVS rejects them, and does so
/// with `ERR_MVS_UNSUPPORTED_NODE` naming the pruned node — not a vague failure.
#[test]
fn spec_rejects_pruned_features_with_specific_node() {
    let p = spec_parser();
    for uri in [
        "foo+bar://example.com/", // "+" in scheme — excluded by the scheme boundary
        "http://example.com/a!b", // "!" sub-delim — excluded (unobserved)
    ] {
        match p.validate(uri.as_bytes()) {
            Err(MvsError::UnsupportedNode(node)) => {
                assert!(node.starts_with("rfc3986-uri:"), "unexpected node {node}");
            }
            other => panic!("expected UnsupportedNode for {uri:?}, got {other:?}"),
        }
    }
}

/// Inputs outside the (absolute-URI) grammar are rejected as malformed: the MVS
/// dropped the relative-reference machinery, so a schemeless reference does not
/// parse at all.
#[test]
fn spec_rejects_out_of_grammar_input() {
    let p = spec_parser();
    for uri in ["/just/a/path", "has spaces", "example.com/no-scheme"] {
        assert_eq!(
            p.validate(uri.as_bytes()),
            Err(MvsError::Malformed),
            "expected malformed for {uri:?}"
        );
    }
}
