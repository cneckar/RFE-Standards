"""Criticality Override Registry (Task 2.4).

Loads and validates ``overrides.yaml`` — the manual protection layer the MVS
pruner (Phase 3) consults so that low-usage but critical nodes survive pruning.
The registry schema is ``schemas/overrides.schema.json`` (frozen in Task 0.2),
which requires every override to carry a non-empty ``justification`` and an
``owner``; see ``docs/overrides.md`` for the governance around that requirement.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mvs_pipeline import schema
from mvs_pipeline.schema import load_document

# Vendored alongside this module (<pkg>/mvs_pipeline/overrides.yaml), like the
# JSON Schemas and the PSL, so it resolves under any install layout — not just a
# source checkout. A wheel install has no repo root; a package-relative path does.
_DEFAULT_PATH = Path(__file__).resolve().parent / "overrides.yaml"


def default_path() -> Path:
    """Path to the packaged canonical ``overrides.yaml``."""
    return _DEFAULT_PATH


def load_overrides(path: str | Path | None = None) -> dict[str, Any]:
    """Load and schema-validate the override registry.

    Raises ``jsonschema.ValidationError`` if the file violates the schema (for
    example, an override missing its justification).
    """
    doc = load_document(Path(path) if path is not None else _DEFAULT_PATH)
    schema.validate("overrides", doc)
    return doc


def protected_nodes(doc: dict[str, Any]) -> set[str]:
    """Set of node ids that are protected from pruning."""
    return {nid for nid, rec in doc["overrides"].items() if rec["protected"]}


def is_protected(doc: dict[str, Any], node_id: str) -> bool:
    """Whether ``node_id`` is present in the registry and marked protected."""
    rec = doc["overrides"].get(node_id)
    return bool(rec and rec["protected"])
