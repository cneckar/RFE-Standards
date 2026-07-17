//! Instrumented parser core (Task 2.1).
//!
//! [`Grammar::compile`] turns a Phase-1 [`Ast`](mvs_schema::Ast) into an
//! executable recognizer. [`Grammar::parse`] then matches an input buffer
//! against the grammar and reports every AST node id traversed on the accepting
//! path, which [`HitAggregator`] tallies into a [`mvs_schema::Hits`] document.
//!
//! The matcher is a backtracking recognizer with a defunctionalized
//! continuation ([`Cont`]): alternations and optional/repetition elements retry
//! other branches when a later obligation fails, so it accepts the same language
//! the ABNF describes rather than a first-match approximation.

use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::fmt;

use mvs_schema::{Ast, Kind, NodeId};

/// A compiled, executable form of a grammar AST.
pub struct Grammar {
    root: NodeId,
    nodes: HashMap<NodeId, Compiled>,
}

/// Outcome of matching one input against a [`Grammar`].
#[derive(Debug, Clone)]
pub struct ParseResult {
    /// Whether the whole input was accepted by the grammar.
    pub matched: bool,
    /// Number of input bytes consumed on the accepting path (0 if unmatched).
    pub consumed: usize,
    /// AST node ids traversed on the accepting path (empty if unmatched).
    pub visited: Vec<NodeId>,
    /// Set when matching was abandoned because the recursion-depth bound was hit
    /// (see [`Grammar::parse_bounded`]); distinguishes a bounds failure from a
    /// plain non-match.
    pub depth_exceeded: bool,
}

/// Errors that can occur while compiling an [`Ast`] into a [`Grammar`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CompileError {
    /// A reference node names a rule that does not exist.
    UnresolvedReference(String),
    /// A node kind the text matcher does not support (e.g. an ASN.1 tag).
    UnsupportedKind(String),
    /// A terminal node's literal could not be parsed.
    BadTerminal(String),
    /// A repetition node's bound could not be parsed.
    BadRepeat(String),
    /// A node that requires a child has none.
    MissingChild(String),
    /// The AST's declared root is absent from its nodes.
    MissingRoot(String),
}

impl fmt::Display for CompileError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            CompileError::UnresolvedReference(n) => write!(f, "unresolved reference to {n}"),
            CompileError::UnsupportedKind(k) => write!(f, "unsupported node kind {k}"),
            CompileError::BadTerminal(t) => write!(f, "bad terminal {t}"),
            CompileError::BadRepeat(r) => write!(f, "bad repeat spec {r}"),
            CompileError::MissingChild(n) => write!(f, "node {n} is missing a child"),
            CompileError::MissingRoot(r) => write!(f, "root {r} is not present in nodes"),
        }
    }
}

impl std::error::Error for CompileError {}

/// A precompiled terminal matcher.
enum Term {
    /// Case-insensitive literal (ABNF char-val).
    Literal(Vec<u8>),
    /// Inclusive byte range (ABNF num-val `%xNN-MM`).
    Range(u8, u8),
    /// Exact byte sequence (ABNF num-val `%xNN` / `%xNN.MM`).
    Bytes(Vec<u8>),
    /// Prose (`<...>`) — never matches; only appears under a 0-repetition.
    Prose,
}

impl Term {
    fn match_at(&self, input: &[u8], pos: usize) -> Option<usize> {
        match self {
            Term::Literal(bytes) => {
                let end = pos + bytes.len();
                if end <= input.len()
                    && input[pos..end]
                        .iter()
                        .zip(bytes)
                        .all(|(a, b)| a.eq_ignore_ascii_case(b))
                {
                    Some(end)
                } else {
                    None
                }
            }
            Term::Range(lo, hi) => match input.get(pos) {
                Some(&b) if b >= *lo && b <= *hi => Some(pos + 1),
                _ => None,
            },
            Term::Bytes(seq) => {
                let end = pos + seq.len();
                if end <= input.len() && &input[pos..end] == seq.as_slice() {
                    Some(end)
                } else {
                    None
                }
            }
            Term::Prose => None,
        }
    }
}

/// A repetition's element and bounds.
struct RepSpec {
    child: NodeId,
    min: u32,
    max: Option<u32>,
}

/// A node compiled to its executable shape.
enum Compiled {
    /// Pass through to a single child (rule / group).
    Pass(NodeId),
    /// Resolved reference to a rule node.
    Ref(NodeId),
    /// Ordered concatenation.
    Seq(Vec<NodeId>),
    /// Ordered choice.
    Alt(Vec<NodeId>),
    /// Optional element.
    Opt(NodeId),
    /// Repetition.
    Rep(RepSpec),
    /// Terminal.
    Term(Term),
}

/// A defunctionalized continuation: what remains to match after the current node.
///
/// `'g` is the grammar's lifetime (borrowed rule/child data); `'p` is the
/// parent-chain lifetime (stack-allocated continuation frames), which is shorter
/// and distinct so nested frames can reference their parents.
#[derive(Clone, Copy)]
enum Cont<'g, 'p> {
    /// Accept iff the input is fully consumed.
    Done,
    /// Match `children[idx..]` in order, then the parent continuation.
    Seq(&'g [NodeId], usize, &'p Cont<'g, 'p>),
    /// Continue a repetition (given count so far and the position it started at).
    Rep(&'g RepSpec, u32, usize, &'p Cont<'g, 'p>),
}

impl Grammar {
    /// Compile a Phase-1 AST into an executable recognizer.
    pub fn compile(ast: &Ast) -> Result<Grammar, CompileError> {
        let mut rule_by_name: HashMap<&str, &NodeId> = HashMap::new();
        for (id, node) in &ast.nodes {
            if node.kind == Kind::Rule {
                rule_by_name.insert(node.name.as_str(), id);
            }
        }

        let mut nodes: HashMap<NodeId, Compiled> = HashMap::with_capacity(ast.nodes.len());
        for (id, node) in &ast.nodes {
            let first_child = || node.children.first().cloned();
            let compiled = match node.kind {
                Kind::Rule | Kind::Group => Compiled::Pass(
                    first_child().ok_or_else(|| CompileError::MissingChild(id.clone()))?,
                ),
                Kind::Reference => {
                    let target = rule_by_name
                        .get(node.name.as_str())
                        .ok_or_else(|| CompileError::UnresolvedReference(node.name.clone()))?;
                    Compiled::Ref((*target).clone())
                }
                Kind::Sequence => Compiled::Seq(node.children.clone()),
                Kind::Alternation => Compiled::Alt(node.children.clone()),
                Kind::Optional => Compiled::Opt(
                    first_child().ok_or_else(|| CompileError::MissingChild(id.clone()))?,
                ),
                Kind::Repetition => {
                    let (min, max) = parse_repeat(&node.name)?;
                    let child =
                        first_child().ok_or_else(|| CompileError::MissingChild(id.clone()))?;
                    Compiled::Rep(RepSpec { child, min, max })
                }
                Kind::Terminal => Compiled::Term(parse_term(&node.name)?),
                Kind::Tag | Kind::StringType | Kind::NamedType => {
                    return Err(CompileError::UnsupportedKind(format!("{:?}", node.kind)));
                }
            };
            nodes.insert(id.clone(), compiled);
        }

        if !nodes.contains_key(&ast.root) {
            return Err(CompileError::MissingRoot(ast.root.clone()));
        }
        Ok(Grammar {
            root: ast.root.clone(),
            nodes,
        })
    }

    /// Match `input` against the grammar, reporting the traversed node ids.
    pub fn parse(&self, input: &[u8]) -> ParseResult {
        self.parse_bounded(input, usize::MAX)
    }

    /// Like [`Grammar::parse`], but abandon matching once the recursion depth
    /// exceeds `max_depth`.
    ///
    /// The recognizer descends one frame per matched element, so depth grows
    /// roughly **linearly with the input length** (each repetition step nests).
    /// `max_depth` is therefore a bound on *how long an input can be*, not just
    /// on grammar nesting — set it (and the caller's stack) large enough to
    /// accept the longest legitimate input, or long-but-valid URLs are recorded
    /// as non-matches (`depth_exceeded`). Callers cap input length up front
    /// (`--max-input-bytes`) and run on a deep stack; `max_depth` is the final
    /// backstop against unbounded recursion.
    pub fn parse_bounded(&self, input: &[u8], max_depth: usize) -> ParseResult {
        let mut m = Matcher {
            grammar: self,
            input,
            visited: Vec::new(),
            depth: 0,
            max_depth,
            depth_exceeded: false,
        };
        let matched = m.m(self.root.as_str(), 0, &Cont::Done);
        if matched {
            let visited = m.visited.iter().map(|s| (*s).to_string()).collect();
            ParseResult {
                matched: true,
                consumed: input.len(),
                visited,
                depth_exceeded: false,
            }
        } else {
            ParseResult {
                matched: false,
                consumed: 0,
                visited: Vec::new(),
                depth_exceeded: m.depth_exceeded,
            }
        }
    }
}

struct Matcher<'g> {
    grammar: &'g Grammar,
    input: &'g [u8],
    visited: Vec<&'g str>,
    depth: usize,
    max_depth: usize,
    depth_exceeded: bool,
}

impl<'g> Matcher<'g> {
    /// Match `node` at `pos`, then the continuation. On success the node (and its
    /// accepted subtree) is appended to `visited`; on failure `visited` is rolled
    /// back to its prior length.
    fn m(&mut self, node: &'g str, pos: usize, cont: &Cont<'g, '_>) -> bool {
        if self.depth >= self.max_depth {
            self.depth_exceeded = true;
            return false;
        }
        self.depth += 1;
        let compiled = self.grammar.nodes.get(node).expect("node id exists");
        let mark = self.visited.len();
        let ok = match compiled {
            Compiled::Pass(child) | Compiled::Ref(child) => self.m(child.as_str(), pos, cont),
            Compiled::Term(term) => match term.match_at(self.input, pos) {
                Some(np) => self.run_cont(cont, np),
                None => false,
            },
            Compiled::Seq(children) => {
                if children.is_empty() {
                    self.run_cont(cont, pos)
                } else {
                    let next = Cont::Seq(children, 1, cont);
                    self.m(children[0].as_str(), pos, &next)
                }
            }
            Compiled::Alt(children) => {
                let mut matched = false;
                for alt in children {
                    let branch_mark = self.visited.len();
                    if self.m(alt.as_str(), pos, cont) {
                        matched = true;
                        break;
                    }
                    self.visited.truncate(branch_mark);
                }
                matched
            }
            Compiled::Opt(child) => {
                let branch_mark = self.visited.len();
                if self.m(child.as_str(), pos, cont) {
                    true
                } else {
                    self.visited.truncate(branch_mark);
                    self.run_cont(cont, pos)
                }
            }
            Compiled::Rep(rep) => self.rep_step(rep, 0, pos, cont),
        };
        self.depth -= 1;
        if ok {
            self.visited.push(node);
            true
        } else {
            self.visited.truncate(mark);
            false
        }
    }

    fn run_cont(&mut self, cont: &Cont<'g, '_>, pos: usize) -> bool {
        match *cont {
            Cont::Done => pos == self.input.len(),
            Cont::Seq(children, idx, parent) => {
                if idx == children.len() {
                    self.run_cont(parent, pos)
                } else {
                    let next = Cont::Seq(children, idx + 1, parent);
                    self.m(children[idx].as_str(), pos, &next)
                }
            }
            Cont::Rep(rep, count, start, parent) => {
                if pos == start {
                    // Zero-width match: stop to avoid looping forever.
                    if count >= rep.min {
                        self.run_cont(parent, pos)
                    } else {
                        false
                    }
                } else {
                    self.rep_step(rep, count, pos, parent)
                }
            }
        }
    }

    fn rep_step(&mut self, rep: &'g RepSpec, count: u32, pos: usize, cont: &Cont<'g, '_>) -> bool {
        let under_max = rep.max.is_none_or(|mx| count < mx);
        if under_max {
            let mark = self.visited.len();
            let next = Cont::Rep(rep, count + 1, pos, cont);
            if self.m(rep.child.as_str(), pos, &next) {
                return true;
            }
            self.visited.truncate(mark);
        }
        if count >= rep.min {
            self.run_cont(cont, pos)
        } else {
            false
        }
    }
}

fn parse_repeat(name: &str) -> Result<(u32, Option<u32>), CompileError> {
    // Repetition nodes are named "<repeat>(...)", e.g. "1*4(...)".
    let spec = name.split('(').next().unwrap_or(name);
    let bad = || CompileError::BadRepeat(name.to_string());
    if let Some(star) = spec.find('*') {
        let (left, right) = spec.split_at(star);
        let right = &right[1..];
        let min = if left.is_empty() {
            0
        } else {
            left.parse().map_err(|_| bad())?
        };
        let max = if right.is_empty() {
            None
        } else {
            Some(right.parse().map_err(|_| bad())?)
        };
        Ok((min, max))
    } else {
        let n: u32 = spec.parse().map_err(|_| bad())?;
        Ok((n, Some(n)))
    }
}

fn parse_term(name: &str) -> Result<Term, CompileError> {
    let bad = || CompileError::BadTerminal(name.to_string());
    if name.starts_with('"') && name.ends_with('"') && name.len() >= 2 {
        return Ok(Term::Literal(name.as_bytes()[1..name.len() - 1].to_vec()));
    }
    if name.starts_with('<') {
        return Ok(Term::Prose);
    }
    if let Some(rest) = name.strip_prefix('%') {
        let base = rest.chars().next().ok_or_else(bad)?;
        let radix = match base.to_ascii_lowercase() {
            'x' => 16,
            'd' => 10,
            'b' => 2,
            _ => return Err(bad()),
        };
        let digits = &rest[1..];
        if let Some(dash) = digits.find('-') {
            let lo = u8::from_str_radix(&digits[..dash], radix).map_err(|_| bad())?;
            let hi = u8::from_str_radix(&digits[dash + 1..], radix).map_err(|_| bad())?;
            return Ok(Term::Range(lo, hi));
        }
        if digits.contains('.') {
            let mut bytes = Vec::new();
            for part in digits.split('.') {
                bytes.push(u8::from_str_radix(part, radix).map_err(|_| bad())?);
            }
            return Ok(Term::Bytes(bytes));
        }
        return Ok(Term::Bytes(vec![
            u8::from_str_radix(digits, radix).map_err(|_| bad())?
        ]));
    }
    Err(bad())
}

/// Aggregates per-sample traversal into a [`mvs_schema::Hits`] document.
///
/// Each input counts once toward `total_samples`; a node is credited at most
/// once per matched input, so `hits[node] / total_samples` is the fraction of
/// sampled inputs that exercised the node.
#[derive(Debug, Default)]
pub struct HitAggregator {
    counts: BTreeMap<NodeId, u64>,
    total: u64,
}

impl HitAggregator {
    /// A fresh, empty aggregator.
    pub fn new() -> Self {
        Self::default()
    }

    /// Fold one already-computed [`ParseResult`] into the tally.
    pub fn record(&mut self, result: &ParseResult) {
        self.record_visited(result.matched, &result.visited);
    }

    /// Fold a `(matched, visited)` sample into the tally. Each input counts once
    /// toward `total_samples`; a node is credited at most once per matched input.
    /// This is the grammar-agnostic entry point (text or DER).
    pub fn record_visited(&mut self, matched: bool, visited: &[NodeId]) {
        if matched {
            let unique: BTreeSet<&str> = visited.iter().map(String::as_str).collect();
            for node in unique {
                *self.counts.entry(node.to_string()).or_insert(0) += 1;
            }
        }
        self.total += 1;
    }

    /// Parse `input` with `grammar` and fold the result into the tally.
    pub fn record_input(&mut self, grammar: &Grammar, input: &[u8]) {
        let result = grammar.parse(input);
        self.record(&result);
    }

    /// Number of inputs processed so far (the usage-fraction denominator).
    pub fn total_samples(&self) -> u64 {
        self.total
    }

    /// Finalize into a schema-conforming [`mvs_schema::Hits`] document.
    pub fn into_hits(self, grammar_id: &str) -> mvs_schema::Hits {
        mvs_schema::Hits {
            schema_version: 1,
            grammar: grammar_id.to_string(),
            total_samples: self.total,
            hits: self.counts.into_iter().collect(),
            // Provenance is stamped later by the Python merge/pruner (T6.7).
            provenance: None,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn uri_grammar() -> Grammar {
        let json = include_str!(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../../artifacts/rfc3986-uri.ast.json"
        ));
        let ast: Ast = serde_json::from_str(json).unwrap();
        Grammar::compile(&ast).unwrap()
    }

    fn visited_names(g: &Grammar, ast: &Ast, input: &str) -> BTreeSet<String> {
        let r = g.parse(input.as_bytes());
        assert!(r.matched, "expected {input:?} to match");
        r.visited
            .iter()
            .map(|id| ast.nodes[id].name.clone())
            .collect()
    }

    fn load_ast() -> Ast {
        let json = include_str!(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../../artifacts/rfc3986-uri.ast.json"
        ));
        serde_json::from_str(json).unwrap()
    }

    #[test]
    fn tiny_depth_bound_clips_a_long_but_valid_url() {
        // A long, perfectly valid URL is rejected purely because the recursion
        // depth exceeds a small bound — recorded as depth_exceeded, not a plain
        // non-match. This is the bias the default bound must not introduce.
        let g = uri_grammar();
        let url = format!("http://example.com/{}", "a".repeat(4000));
        let r = g.parse_bounded(url.as_bytes(), 100);
        assert!(!r.matched);
        assert!(
            r.depth_exceeded,
            "long valid URL clipped by depth, not invalidity"
        );
    }

    #[test]
    fn generous_depth_accepts_a_long_url() {
        // With a bound sized for the input length (and a deep stack, as the CLI
        // provides), the same long URL matches. Run on a big stack because the
        // recognizer recurses ~one frame per input byte.
        std::thread::Builder::new()
            .stack_size(256 * 1024 * 1024)
            .spawn(|| {
                let g = uri_grammar();
                let url = format!("http://example.com/{}", "a".repeat(4000));
                let r = g.parse_bounded(url.as_bytes(), 200_000);
                assert!(
                    r.matched,
                    "long valid URL should match under a generous bound"
                );
                assert!(!r.depth_exceeded);
                assert_eq!(r.consumed, url.len());
            })
            .unwrap()
            .join()
            .unwrap();
    }

    #[test]
    fn accepts_common_uris() {
        let g = uri_grammar();
        for uri in [
            "http://example.com",
            "http://example.com/path?q=1#frag",
            "https://user@host.example:8443/a/b",
            "urn:isbn:0451450523",
            "mailto:a@b.example",
            "http://1.2.3.4/",
        ] {
            let r = g.parse(uri.as_bytes());
            assert!(r.matched, "should accept {uri:?}");
            assert_eq!(r.consumed, uri.len());
        }
    }

    #[test]
    fn rejects_non_uris() {
        let g = uri_grammar();
        for bad in ["has spaces", "://noscheme", "1http://x"] {
            assert!(!g.parse(bad.as_bytes()).matched, "should reject {bad:?}");
        }
    }

    #[test]
    fn records_expected_nodes() {
        let g = uri_grammar();
        let ast = load_ast();
        let names = visited_names(&g, &ast, "https://user@host.example:8443/p?q#f");
        for expected in [
            "URI",
            "scheme",
            "authority",
            "userinfo",
            "host",
            "port",
            "query",
            "fragment",
        ] {
            assert!(names.contains(expected), "expected to traverse {expected}");
        }
    }

    #[test]
    fn visited_ids_are_real_nodes() {
        let g = uri_grammar();
        let ast = load_ast();
        let r = g.parse(b"http://example.com/");
        assert!(r.matched);
        for id in &r.visited {
            assert!(ast.nodes.contains_key(id), "unknown node id {id}");
        }
    }

    #[test]
    fn aggregates_into_valid_hits() {
        let g = uri_grammar();
        let mut agg = HitAggregator::new();
        let corpus = [
            "http://example.com/",
            "https://a.example/p?q=1",
            "http://b.example#frag",
            "not a uri", // unmatched: counts toward total, contributes no hits
        ];
        for uri in corpus {
            agg.record_input(&g, uri.as_bytes());
        }
        assert_eq!(agg.total_samples(), 4);

        let hits = agg.into_hits("rfc3986-uri");
        assert_eq!(hits.total_samples, 4);
        // scheme is traversed by all three well-formed URIs.
        let ast = load_ast();
        let scheme_id = ast
            .nodes
            .iter()
            .find(|(_, n)| n.kind == Kind::Rule && n.name == "scheme")
            .map(|(id, _)| id.clone())
            .unwrap();
        assert_eq!(hits.hits.get(&scheme_id).copied(), Some(3));
        // Every counted node stays within total_samples.
        assert!(hits.hits.values().all(|&c| c <= hits.total_samples));
    }
}
