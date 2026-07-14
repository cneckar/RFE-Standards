//! Reference parser scaffolding for the MVS/RFE framework.
//!
//! Phase 4 auto-generates native, cross-platform parsers from the minified
//! standard. Per ADR 0001 these are the shipped deliverable, so this crate is
//! built for every target in the CI cross-compilation matrix. It also anchors
//! the strict failure model (Task 4.2): the MVS fails cleanly on legacy bloat
//! rather than attempting partial recovery.

use mvs_core::NodeId;

/// Errors a reference parser raises. The MVS refuses to degrade gracefully into
/// legacy formats — it fails fast with a specific reason.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum MvsError {
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
            MvsError::UnsupportedNode(node) => {
                write!(f, "ERR_MVS_UNSUPPORTED_NODE: {node}")
            }
            MvsError::BoundsExceeded { bound } => {
                write!(f, "ERR_MVS_BOUNDS_EXCEEDED: {bound}")
            }
        }
    }
}

impl std::error::Error for MvsError {}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn unsupported_node_message_is_specific() {
        let err = MvsError::UnsupportedNode("x509.ext.legacy-netscape-cert-type".into());
        assert_eq!(
            err.to_string(),
            "ERR_MVS_UNSUPPORTED_NODE: x509.ext.legacy-netscape-cert-type"
        );
    }

    #[test]
    fn bounds_message_is_specific() {
        let err = MvsError::BoundsExceeded { bound: "max_depth" };
        assert_eq!(err.to_string(), "ERR_MVS_BOUNDS_EXCEEDED: max_depth");
    }
}
