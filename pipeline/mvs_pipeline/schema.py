"""Load and validate the shared artifact-spine documents (Task 0.2).

This module is the Python side of the Python/Rust contract. It resolves the JSON
Schemas and validates in-memory artifacts (AST, telemetry hits, the override
registry, pruned-node sets) against them. Later phases import :func:`validate`
rather than re-implementing schema checks.

The schema definitions are vendored *inside* the package
(``mvs_pipeline/schemas/``, like the grammars and the Public Suffix List) so
they resolve adjacent to this module in any layout — a source checkout, an
editable install, or a plain ``pip install`` wheel. (Example fixtures stay under
the repo-root ``schemas/examples/`` since they are shared with the Rust crate.)
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

# Vendored alongside this module: <pkg>/mvs_pipeline/schemas/*.schema.json.
_SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"

# Logical artifact kind -> schema filename.
_SCHEMAS = {
    "ast": "ast.schema.json",
    "hits": "hits.schema.json",
    "overrides": "overrides.schema.json",
    "pruned": "pruned.schema.json",
    "rfe": "rfe-submission.schema.json",
}


def schema_dir() -> Path:
    """Directory holding the vendored JSON Schema definitions."""
    return _SCHEMA_DIR


@cache
def _validator(kind: str) -> Draft202012Validator:
    try:
        filename = _SCHEMAS[kind]
    except KeyError:
        raise ValueError(
            f"unknown artifact kind {kind!r}; expected one of {sorted(_SCHEMAS)}"
        ) from None
    schema = json.loads((_SCHEMA_DIR / filename).read_text())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def load_document(path: str | Path) -> Any:
    """Parse a JSON or YAML artifact from disk into Python data."""
    path = Path(path)
    text = path.read_text()
    if path.suffix in {".yaml", ".yml"}:
        return yaml.safe_load(text)
    return json.loads(text)


def is_valid(kind: str, document: Any) -> bool:
    """Return whether ``document`` satisfies the schema for ``kind``."""
    return _validator(kind).is_valid(document)


def validate(kind: str, document: Any) -> None:
    """Raise ``jsonschema.ValidationError`` if ``document`` violates the schema.

    Reports the first error by JSON path so failures are actionable.
    """
    errors = sorted(_validator(kind).iter_errors(document), key=lambda e: e.path)
    if errors:
        raise errors[0]
