"""JSONL ingestion and schema validation for SWE-benchify instances (issue #3).

The schema is the cross-repo contract with SWE-benchify. Any record with a
missing required field or an unexpected field raises SchemaError immediately —
this repo fails loud on drift rather than silently accepting a changed schema.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError


class SchemaError(Exception):
    """Raised when a JSONL record does not match the pinned schema."""


class SWEbenchInstance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Base SWE-bench fields
    instance_id: str
    repo: str
    base_commit: str
    patch: str
    test_patch: str
    problem_statement: str

    # SWE-benchify additive columns
    repo_language: str
    product: str
    fix_merge_date: str
    provenance: str
    link_confidence: float
    n_fail_to_pass: int
    patch_lines: int
    files_touched: int
    cross_file: bool
    env_spec_hash: str
    image_name: str
    compiled: bool

    # Validation-evidence columns
    n_runs: int
    quarantined_tests: list[str]
    decontam_overlap: bool


def load(path: str | Path) -> list[SWEbenchInstance]:
    """Load and validate a SWE-benchify JSONL file.

    Raises SchemaError on the first record with a missing required field or an
    unexpected (drifted) field. Empty lines are skipped.
    """
    instances: list[SWEbenchInstance] = []
    with Path(path).open() as f:
        for lineno, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                raw: dict[str, Any] = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SchemaError(f"line {lineno}: invalid JSON: {exc}") from exc
            try:
                instances.append(SWEbenchInstance.model_validate(raw))
            except ValidationError as exc:
                raise SchemaError(f"line {lineno}: schema mismatch: {exc}") from exc
    return instances
