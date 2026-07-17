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

use mvs_core::{DerWalker, Grammar, HitAggregator};
use mvs_schema::Ast;

/// Worker stack size. The recognizer recurses roughly one frame per matched
/// input byte (see `parse_bounded`), so matching a long-but-legal URL needs a
/// deep stack. `--max-input-bytes` bounds the input length; this stack is sized
/// to hold that recursion comfortably.
const WORKER_STACK_BYTES: usize = 512 * 1024 * 1024;

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    // Run the whole job on a large-stack thread: the default 8 MiB main stack
    // would overflow on legitimately long inputs.
    let result = std::thread::Builder::new()
        .stack_size(WORKER_STACK_BYTES)
        .spawn(move || run(&args))
        .expect("spawn worker thread")
        .join()
        .expect("worker thread panicked");
    match result {
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
    corpus: Option<String>,
    der_dir: Option<String>,
    out: String,
    /// T4.2 bound: abandon a parse that descends deeper than this many frames.
    max_depth: Option<usize>,
    /// T4.2 bound: skip a corpus line longer than this many bytes.
    max_input_bytes: Option<usize>,
}

fn parse_args(args: &[String]) -> Result<Options, String> {
    let mut ast = None;
    let mut corpus = None;
    let mut der_dir = None;
    let mut out = None;
    let mut max_depth = None;
    let mut max_input_bytes = None;
    let mut it = args.iter();
    while let Some(flag) = it.next() {
        match flag.as_str() {
            "--ast" => ast = Some(next_value(&mut it, "--ast")?),
            "--corpus" => corpus = Some(next_value(&mut it, "--corpus")?),
            "--der-dir" => der_dir = Some(next_value(&mut it, "--der-dir")?),
            "--out" => out = Some(next_value(&mut it, "--out")?),
            "--max-depth" => max_depth = Some(parse_usize(&mut it, "--max-depth")?),
            "--max-input-bytes" => {
                max_input_bytes = Some(parse_usize(&mut it, "--max-input-bytes")?)
            }
            "-h" | "--help" => {
                return Err("usage: mvs-telemetry --ast <path> (--corpus <file|-> | \
                            --der-dir <dir>) --out <file|-> [--max-depth N] \
                            [--max-input-bytes N]"
                    .to_string());
            }
            other => return Err(format!("unknown argument {other}")),
        }
    }
    if corpus.is_some() == der_dir.is_some() {
        return Err("provide exactly one of --corpus or --der-dir".to_string());
    }
    Ok(Options {
        ast: ast.ok_or("missing --ast")?,
        corpus,
        der_dir,
        out: out.ok_or("missing --out")?,
        max_depth,
        max_input_bytes,
    })
}

fn parse_usize(it: &mut std::slice::Iter<'_, String>, flag: &str) -> Result<usize, String> {
    let value = next_value(it, flag)?;
    value
        .parse::<usize>()
        .map_err(|_| format!("{flag} requires a non-negative integer, got {value:?}"))
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

    let mut agg = HitAggregator::new();
    let mut matched = 0u64;
    let mut skipped = 0u64;

    if let Some(dir) = &opts.der_dir {
        let walker = DerWalker::new(&ast);
        for path in der_files(dir)? {
            let bytes = fs::read(&path).map_err(|e| format!("reading {}: {e}", path.display()))?;
            let result = walker.walk(&bytes);
            if result.matched {
                matched += 1;
            }
            agg.record_visited(result.matched, &result.visited);
        }
    } else {
        let grammar = Grammar::compile(&ast).map_err(|e| format!("compiling grammar: {e}"))?;
        let corpus = read_source(opts.corpus.as_deref().unwrap_or("-"))?;
        let max_depth = opts.max_depth.unwrap_or(usize::MAX);
        for line in corpus.lines() {
            let input = line.trim();
            if input.is_empty() || input.starts_with('#') {
                continue;
            }
            // T4.2 bound: a single pathological URL in a 10^8 corpus must not be
            // able to stall a shard, so over-long lines are skipped up front.
            if opts
                .max_input_bytes
                .is_some_and(|limit| input.len() > limit)
            {
                skipped += 1;
                continue;
            }
            let result = grammar.parse_bounded(input.as_bytes(), max_depth);
            if result.matched {
                matched += 1;
            }
            agg.record(&result);
        }
    }

    let total = agg.total_samples();
    let hits = agg.into_hits(&ast.grammar);
    let json = serde_json::to_string_pretty(&hits).map_err(|e| format!("serializing hits: {e}"))?;
    write_sink(&opts.out, &format!("{json}\n"))?;

    Ok(format!(
        "grammar={} samples={total} matched={matched} skipped={skipped} nodes={}",
        ast.grammar,
        hits.hits.len()
    ))
}

/// Sorted list of `*.der` files in a directory (sorted for reproducible output).
fn der_files(dir: &str) -> Result<Vec<std::path::PathBuf>, String> {
    let mut paths: Vec<_> = fs::read_dir(dir)
        .map_err(|e| format!("reading dir {dir}: {e}"))?
        .filter_map(|entry| entry.ok().map(|e| e.path()))
        .filter(|p| p.extension().is_some_and(|ext| ext == "der"))
        .collect();
    paths.sort();
    Ok(paths)
}
