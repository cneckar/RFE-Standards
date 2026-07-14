//! Instrumented DER walker (Task 2.2).
//!
//! [`DerWalker`] decodes a DER-encoded value (an X.509 certificate) and walks it
//! against an ASN.1 [`Ast`](mvs_schema::Ast), recording every AST node id whose
//! structure the encoding exercised. This is the X.509 counterpart to the text
//! matcher in [`crate::telemetry`], feeding the same [`HitAggregator`].
//!
//! It is schema-directed and deliberately lenient: it follows the schema to
//! credit fields precisely, but where the encoding out-runs the (minimal) model
//! — an attribute value of a string type the schema does not list, an extension
//! body left opaque — it consumes the value and keeps its place rather than
//! failing the whole certificate. Telemetry measures; it does not validate.

use std::cell::Cell;
use std::collections::HashMap;

use mvs_schema::{Ast, Kind, NodeId};

/// Outcome of walking one DER value against an ASN.1 AST.
#[derive(Debug, Clone)]
pub struct DerResult {
    /// Whether the top-level value was decoded and consumed in full.
    pub matched: bool,
    /// AST node ids exercised by the encoding.
    pub visited: Vec<NodeId>,
    /// Set when the walk was abandoned because the nesting-depth bound was hit.
    pub depth_exceeded: bool,
}

/// A single DER tag-length-value triple.
struct Tlv<'a> {
    /// Tag class: 0 universal, 1 application, 2 context, 3 private.
    class: u8,
    constructed: bool,
    tag: u32,
    content: &'a [u8],
    /// Total bytes the TLV occupies (header + content).
    total: usize,
}

fn read_tlv(input: &[u8]) -> Option<Tlv<'_>> {
    let first = *input.first()?;
    let class = first >> 6;
    let constructed = first & 0x20 != 0;
    let mut idx = 1;
    let tag = if first & 0x1f == 0x1f {
        let mut t: u32 = 0;
        loop {
            let b = *input.get(idx)?;
            idx += 1;
            t = (t << 7) | u32::from(b & 0x7f);
            if b & 0x80 == 0 {
                break;
            }
        }
        t
    } else {
        u32::from(first & 0x1f)
    };
    let len_byte = *input.get(idx)?;
    idx += 1;
    let length = if len_byte & 0x80 == 0 {
        usize::from(len_byte)
    } else {
        let n = usize::from(len_byte & 0x7f);
        if n == 0 || n > 4 {
            return None; // indefinite or absurd lengths are not valid DER here
        }
        let mut l = 0usize;
        for _ in 0..n {
            l = (l << 8) | usize::from(*input.get(idx)?);
            idx += 1;
        }
        l
    };
    let end = idx.checked_add(length)?;
    if end > input.len() {
        return None;
    }
    Some(Tlv {
        class,
        constructed,
        tag,
        content: &input[idx..end],
        total: end,
    })
}

// Universal tag numbers used by the certificate core.
const T_BOOLEAN: u32 = 1;
const T_INTEGER: u32 = 2;
const T_BIT_STRING: u32 = 3;
const T_OCTET_STRING: u32 = 4;
const T_NULL: u32 = 5;
const T_OID: u32 = 6;
const T_SEQUENCE: u32 = 16;
const T_SET: u32 = 17;

fn string_type_tag(name: &str) -> Option<u32> {
    Some(match name {
        "UTF8String" => 12,
        "NumericString" => 18,
        "PrintableString" => 19,
        "TeletexString" | "T61String" => 20,
        "VideotexString" => 21,
        "IA5String" => 22,
        "GraphicString" => 25,
        "VisibleString" | "ISO646String" => 26,
        "GeneralString" => 27,
        "UniversalString" => 28,
        "BMPString" => 30,
        _ => return None,
    })
}

fn terminal_tag(name: &str) -> Option<u32> {
    Some(match name {
        "INTEGER" => T_INTEGER,
        "BOOLEAN" => T_BOOLEAN,
        "BIT STRING" => T_BIT_STRING,
        "OCTET STRING" => T_OCTET_STRING,
        "OBJECT IDENTIFIER" => T_OID,
        "NULL" => T_NULL,
        "UTCTime" => 23,
        "GeneralizedTime" => 24,
        _ => return None, // e.g. "ANY" — matches any tag
    })
}

/// Parse the context tag number out of a tag node name like `"[3] EXPLICIT"`.
fn tag_number(name: &str) -> Option<u32> {
    let open = name.find('[')?;
    let close = name[open..].find(']')? + open;
    name[open + 1..close].trim().parse().ok()
}

/// Walks DER encodings against an ASN.1 AST, recording node hits.
pub struct DerWalker<'a> {
    ast: &'a Ast,
    rule_by_name: HashMap<&'a str, &'a NodeId>,
    max_depth: usize,
    depth: Cell<usize>,
    depth_exceeded: Cell<bool>,
}

impl<'a> DerWalker<'a> {
    /// Build a walker for the given ASN.1 AST (no nesting-depth bound).
    pub fn new(ast: &'a Ast) -> Self {
        Self::with_max_depth(ast, usize::MAX)
    }

    /// Build a walker that abandons the walk once value nesting exceeds `max_depth`.
    pub fn with_max_depth(ast: &'a Ast, max_depth: usize) -> Self {
        let mut rule_by_name = HashMap::new();
        for (id, node) in &ast.nodes {
            if node.kind == Kind::Rule {
                rule_by_name.insert(node.name.as_str(), id);
            }
        }
        Self {
            ast,
            rule_by_name,
            max_depth,
            depth: Cell::new(0),
            depth_exceeded: Cell::new(false),
        }
    }

    /// Decode `der` and record the AST nodes its structure exercises.
    pub fn walk(&self, der: &[u8]) -> DerResult {
        self.depth.set(0);
        self.depth_exceeded.set(false);
        let mut visited = Vec::new();
        match self.match_type(&self.ast.root, der, 0, &mut visited) {
            Some(consumed) => DerResult {
                matched: consumed == der.len(),
                visited,
                depth_exceeded: self.depth_exceeded.get(),
            },
            None => DerResult {
                matched: false,
                visited: Vec::new(),
                depth_exceeded: self.depth_exceeded.get(),
            },
        }
    }

    fn node(&self, id: &str) -> &'a mvs_schema::Node {
        self.ast.nodes.get(id).expect("node id exists")
    }

    fn child0<'n>(&self, node: &'n mvs_schema::Node) -> Option<&'n NodeId> {
        node.children.first()
    }

    /// Depth-guarding wrapper around [`Self::match_type_inner`].
    fn match_type(
        &self,
        node_id: &str,
        der: &[u8],
        pos: usize,
        visited: &mut Vec<NodeId>,
    ) -> Option<usize> {
        let d = self.depth.get();
        if d >= self.max_depth {
            self.depth_exceeded.set(true);
            return None;
        }
        self.depth.set(d + 1);
        let result = self.match_type_inner(node_id, der, pos, visited);
        self.depth.set(d);
        result
    }

    /// Match `node` against one DER value at `der[pos..]`, returning the new
    /// position on success. Rolls back recorded nodes on failure.
    fn match_type_inner(
        &self,
        node_id: &str,
        der: &[u8],
        pos: usize,
        visited: &mut Vec<NodeId>,
    ) -> Option<usize> {
        let node = self.node(node_id);
        let mark = visited.len();

        // Pass-through wrappers correspond to the same single value as their child.
        let result = match node.kind {
            Kind::Rule | Kind::NamedType | Kind::Group | Kind::Optional => {
                let child = self.child0(node)?;
                self.match_type(child, der, pos, visited)
            }
            Kind::Reference => {
                let target = self.rule_by_name.get(node.name.as_str())?;
                self.match_type(target, der, pos, visited)
            }
            _ => {
                // Value-consuming kinds read exactly one TLV.
                let tlv = read_tlv(&der[pos..])?;
                let ok = match node.kind {
                    Kind::Sequence => self.match_sequence(node, &tlv, visited),
                    Kind::Alternation => self.match_choice(node, &tlv, visited),
                    Kind::Repetition => self.match_repetition(node, &tlv, visited),
                    Kind::Tag => self.match_tag(node, &tlv, visited),
                    Kind::Terminal | Kind::StringType => self.match_leaf(node, &tlv),
                    _ => false,
                };
                if ok {
                    Some(pos + tlv.total)
                } else {
                    None
                }
            }
        };

        match result {
            Some(new_pos) => {
                visited.push(node_id.to_string());
                Some(new_pos)
            }
            None => {
                visited.truncate(mark);
                None
            }
        }
    }

    fn match_sequence(
        &self,
        node: &mvs_schema::Node,
        tlv: &Tlv<'_>,
        visited: &mut Vec<NodeId>,
    ) -> bool {
        let expected = if node.name == "SET" {
            T_SET
        } else {
            T_SEQUENCE
        };
        if tlv.class != 0 || !tlv.constructed || tlv.tag != expected {
            return false;
        }
        self.walk_components(&node.children, tlv.content, visited);
        true
    }

    fn walk_components(&self, components: &[NodeId], content: &[u8], visited: &mut Vec<NodeId>) {
        let mut p = 0;
        for comp in components {
            if p >= content.len() {
                break;
            }
            match self.match_type(comp, content, p, visited) {
                Some(np) => p = np,
                None => {
                    // Field absent (OPTIONAL) or of an unmodeled shape: if it is
                    // not optional, skip one value opaquely to stay aligned.
                    if !self.is_optional(comp) {
                        match read_tlv(&content[p..]) {
                            Some(tlv) => p += tlv.total,
                            None => break,
                        }
                    }
                }
            }
        }
    }

    fn is_optional(&self, comp_id: &str) -> bool {
        // A component is `named-type -> optional -> ...` when OPTIONAL/DEFAULT.
        let comp = self.node(comp_id);
        self.child0(comp)
            .map(|c| self.node(c).kind == Kind::Optional)
            .unwrap_or(false)
    }

    fn match_choice(
        &self,
        node: &mvs_schema::Node,
        tlv: &Tlv<'_>,
        visited: &mut Vec<NodeId>,
    ) -> bool {
        // Reconstruct the single TLV as a standalone buffer isn't needed: each
        // alternative re-reads the same value. Try them in order.
        for alt in &node.children {
            let mark = visited.len();
            // Rebuild a der slice starting at this value: the caller already has
            // it as tlv; match the alternative against a one-value buffer.
            if self.match_value(alt, tlv, visited) {
                return true;
            }
            visited.truncate(mark);
        }
        // Unmodeled alternative: consume opaquely, still counting the CHOICE.
        true
    }

    /// Match a type node that is known to correspond to `tlv` (already read).
    fn match_value(&self, node_id: &str, tlv: &Tlv<'_>, visited: &mut Vec<NodeId>) -> bool {
        let node = self.node(node_id);
        let mark = visited.len();
        let ok = match node.kind {
            Kind::Rule | Kind::NamedType | Kind::Group | Kind::Optional => self
                .child0(node)
                .map(|c| self.match_value(c, tlv, visited))
                .unwrap_or(false),
            Kind::Reference => self
                .rule_by_name
                .get(node.name.as_str())
                .map(|t| self.match_value(t, tlv, visited))
                .unwrap_or(false),
            Kind::Sequence => self.match_sequence(node, tlv, visited),
            Kind::Repetition => self.match_repetition(node, tlv, visited),
            Kind::Tag => self.match_tag(node, tlv, visited),
            Kind::Terminal | Kind::StringType => self.match_leaf(node, tlv),
            Kind::Alternation => self.match_choice(node, tlv, visited),
        };
        if ok {
            visited.push(node_id.to_string());
            true
        } else {
            visited.truncate(mark);
            false
        }
    }

    fn match_repetition(
        &self,
        node: &mvs_schema::Node,
        tlv: &Tlv<'_>,
        visited: &mut Vec<NodeId>,
    ) -> bool {
        let expected = if node.name == "SET OF" {
            T_SET
        } else {
            T_SEQUENCE
        };
        if tlv.class != 0 || !tlv.constructed || tlv.tag != expected {
            return false;
        }
        let Some(element) = self.child0(node) else {
            return false;
        };
        let mut p = 0;
        while p < tlv.content.len() {
            match self.match_type(element, tlv.content, p, visited) {
                Some(np) if np > p => p = np,
                _ => match read_tlv(&tlv.content[p..]) {
                    Some(inner) => p += inner.total,
                    None => break,
                },
            }
        }
        true
    }

    fn match_tag(&self, node: &mvs_schema::Node, tlv: &Tlv<'_>, visited: &mut Vec<NodeId>) -> bool {
        let Some(n) = tag_number(&node.name) else {
            return false;
        };
        if tlv.class != 2 || tlv.tag != n {
            return false;
        }
        let Some(inner_id) = self.child0(node) else {
            return false;
        };
        let explicit = if node.name.contains("IMPLICIT") {
            false
        } else {
            // EXPLICIT (stated or, for a constructed value, assumed).
            node.name.contains("EXPLICIT") || tlv.constructed
        };
        if explicit {
            // The context wrapper encloses the real value.
            self.match_type(inner_id, tlv.content, 0, visited).is_some()
        } else {
            // IMPLICIT: the inner type is present but re-tagged. Credit the inner
            // node without re-decoding its (context-tagged) body.
            visited.push(inner_id.clone());
            true
        }
    }

    fn match_leaf(&self, node: &mvs_schema::Node, tlv: &Tlv<'_>) -> bool {
        if node.kind == Kind::StringType {
            return string_type_tag(&node.name)
                .map(|t| tlv.class == 0 && tlv.tag == t)
                .unwrap_or(false);
        }
        match terminal_tag(&node.name) {
            Some(t) => tlv.class == 0 && tlv.tag == t,
            None => true, // ANY / unmodeled terminal: accept any single value
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn x509_ast() -> Ast {
        let json = include_str!(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../../artifacts/rfc5280-x509.ast.json"
        ));
        serde_json::from_str(json).unwrap()
    }

    fn sample_cert() -> Vec<u8> {
        include_bytes!(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../../corpus/certs/sample-cert.der"
        ))
        .to_vec()
    }

    #[test]
    fn walks_real_certificate() {
        let ast = x509_ast();
        let walker = DerWalker::new(&ast);
        let der = sample_cert();
        let result = walker.walk(&der);
        assert!(result.matched, "should consume the whole certificate");
        assert!(!result.visited.is_empty());

        let names: std::collections::BTreeSet<&str> = result
            .visited
            .iter()
            .map(|id| ast.nodes[id].name.as_str())
            .collect();
        for expected in [
            "Certificate",
            "TBSCertificate",
            "Validity",
            "SubjectPublicKeyInfo",
            "AlgorithmIdentifier",
            "Name",
            "Extension",
        ] {
            assert!(names.contains(expected), "expected to traverse {expected}");
        }
    }

    #[test]
    fn visited_ids_are_real_nodes() {
        let ast = x509_ast();
        let walker = DerWalker::new(&ast);
        let result = walker.walk(&sample_cert());
        for id in &result.visited {
            assert!(ast.nodes.contains_key(id), "unknown node id {id}");
        }
    }

    #[test]
    fn rejects_non_der() {
        let ast = x509_ast();
        let walker = DerWalker::new(&ast);
        assert!(!walker.walk(b"not der at all").matched);
        assert!(!walker.walk(&[]).matched);
    }
}
