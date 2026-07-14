//! `mvs-validate` — validate an input against the Minimum Viable Standard.
//!
//! ```text
//! mvs-validate --ast <ast.json> --pruned <pruned.json> --uri <string>
//! mvs-validate --ast <ast.json> --pruned <pruned.json> --der <file>
//! ```
//!
//! Exits 0 and prints `accept` when the input conforms to the MVS; exits 1 and
//! prints the specific `ERR_MVS_*` reason otherwise.

use std::process::ExitCode;

use mvs_refparse::{MvsCertParser, MvsTextParser};
use mvs_schema::{Ast, Pruned};

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    match run(&args) {
        Ok(true) => {
            println!("accept");
            ExitCode::SUCCESS
        }
        Ok(false) => ExitCode::FAILURE,
        Err(err) => {
            eprintln!("mvs-validate: error: {err}");
            ExitCode::from(2)
        }
    }
}

fn flag(args: &[String], name: &str) -> Option<String> {
    args.iter()
        .position(|a| a == name)
        .and_then(|i| args.get(i + 1).cloned())
}

fn run(args: &[String]) -> Result<bool, String> {
    let ast_path = flag(args, "--ast").ok_or("missing --ast")?;
    let pruned_path = flag(args, "--pruned").ok_or("missing --pruned")?;
    let ast: Ast = serde_json::from_str(&read(&ast_path)?).map_err(|e| format!("AST: {e}"))?;
    let pruned: Pruned =
        serde_json::from_str(&read(&pruned_path)?).map_err(|e| format!("pruned: {e}"))?;

    if let Some(uri) = flag(args, "--uri") {
        let parser = MvsTextParser::compile(&ast, &pruned.pruned)
            .map_err(|e| format!("compiling grammar: {e}"))?;
        Ok(report(parser.validate(uri.as_bytes())))
    } else if let Some(path) = flag(args, "--der") {
        let der = std::fs::read(&path).map_err(|e| format!("reading {path}: {e}"))?;
        let parser = MvsCertParser::new(ast, &pruned.pruned);
        Ok(report(parser.validate(&der)))
    } else {
        Err("provide one of --uri <string> or --der <file>".to_string())
    }
}

fn report(outcome: Result<Vec<String>, mvs_refparse::MvsError>) -> bool {
    match outcome {
        Ok(_) => true,
        Err(err) => {
            println!("{err}");
            false
        }
    }
}

fn read(path: &str) -> Result<String, String> {
    std::fs::read_to_string(path).map_err(|e| format!("reading {path}: {e}"))
}
