//! `mvs-telemetry` — run the instrumented parser core over a text corpus.
//!
//! Reads a Phase-1 AST and a newline-delimited corpus, parses every input
//! against the grammar while recording traversed node ids, and writes an
//! aggregated `hits.json` (conforming to `schemas/hits.schema.json`).
//!
//! ```text
//! mvs-telemetry --ast <ast.json> --corpus <file|-> --out <hits.json|->
//! ```
//!
//! Blank lines and lines beginning with `#` in the corpus are skipped. This is
//! the throughput end of the URI corpus ingestor (Task 2.3): a Python front-end
//! prepares the corpus, this native binary does the parsing.

use std::fs;
use std::io::{Read, Write};
use std::process::ExitCode;

use mvs_core::{Grammar, HitAggregator};
use mvs_schema::Ast;

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    match run(&args) {
        Ok(summary) => {
            eprintln!("{summary}");
            ExitCode::SUCCESS
        }
        Err(err) => {
            eprintln!("mvs-telemetry: error: {err}");
            ExitCode::FAILURE
        }
    }
}

struct Options {
    ast: String,
    corpus: String,
    out: String,
}

fn parse_args(args: &[String]) -> Result<Options, String> {
    let mut ast = None;
    let mut corpus = None;
    let mut out = None;
    let mut it = args.iter();
    while let Some(flag) = it.next() {
        match flag.as_str() {
            "--ast" => ast = Some(next_value(&mut it, "--ast")?),
            "--corpus" => corpus = Some(next_value(&mut it, "--corpus")?),
            "--out" => out = Some(next_value(&mut it, "--out")?),
            "-h" | "--help" => {
                return Err(
                    "usage: mvs-telemetry --ast <path> --corpus <file|-> --out <file|->"
                        .to_string(),
                );
            }
            other => return Err(format!("unknown argument {other}")),
        }
    }
    Ok(Options {
        ast: ast.ok_or("missing --ast")?,
        corpus: corpus.ok_or("missing --corpus")?,
        out: out.ok_or("missing --out")?,
    })
}

fn next_value(it: &mut std::slice::Iter<'_, String>, flag: &str) -> Result<String, String> {
    it.next()
        .cloned()
        .ok_or_else(|| format!("{flag} requires a value"))
}

fn read_source(path: &str) -> Result<String, String> {
    if path == "-" {
        let mut buf = String::new();
        std::io::stdin()
            .read_to_string(&mut buf)
            .map_err(|e| format!("reading stdin: {e}"))?;
        Ok(buf)
    } else {
        fs::read_to_string(path).map_err(|e| format!("reading {path}: {e}"))
    }
}

fn write_sink(path: &str, contents: &str) -> Result<(), String> {
    if path == "-" {
        std::io::stdout()
            .write_all(contents.as_bytes())
            .map_err(|e| format!("writing stdout: {e}"))
    } else {
        fs::write(path, contents).map_err(|e| format!("writing {path}: {e}"))
    }
}

fn run(args: &[String]) -> Result<String, String> {
    let opts = parse_args(args)?;

    let ast_text = read_source(&opts.ast)?;
    let ast: Ast = serde_json::from_str(&ast_text).map_err(|e| format!("parsing AST: {e}"))?;
    let grammar = Grammar::compile(&ast).map_err(|e| format!("compiling grammar: {e}"))?;

    let corpus = read_source(&opts.corpus)?;
    let mut agg = HitAggregator::new();
    let mut matched = 0u64;
    for line in corpus.lines() {
        let input = line.trim();
        if input.is_empty() || input.starts_with('#') {
            continue;
        }
        let result = grammar.parse(input.as_bytes());
        if result.matched {
            matched += 1;
        }
        agg.record(&result);
    }

    let total = agg.total_samples();
    let hits = agg.into_hits(&ast.grammar);
    let json = serde_json::to_string_pretty(&hits).map_err(|e| format!("serializing hits: {e}"))?;
    write_sink(&opts.out, &format!("{json}\n"))?;

    Ok(format!(
        "grammar={} samples={total} matched={matched} nodes={}",
        ast.grammar,
        hits.hits.len()
    ))
}
