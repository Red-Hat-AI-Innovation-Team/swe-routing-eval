"""JSONL ingestion and schema validation for SWE-benchify instances (issue #3).

The schema is the cross-repo contract with SWE-benchify. Any record missing a
required field raises SchemaError immediately. Extra/unknown fields are silently
ignored — SWE-benchify may add new columns over time and that is not drift.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator


class SchemaError(Exception):
    """Raised when a JSONL record does not match the pinned schema."""


class SWEbenchInstance(BaseModel):
    # extra="ignore": SWE-benchify emits additional fields (FAIL_TO_PASS,
    # hints_text, flake_count, …) that are not part of our eval schema.
    # populate_by_name=True: accept both our field name and any alias.
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    # Base SWE-bench fields
    instance_id: str
    repo: str
    base_commit: str
    patch: str
    test_patch: str
    problem_statement: str

    # SWE-benchify additive columns
    repo_language: str
    product: str = ""
    fix_merge_date: str
    provenance: str
    link_confidence: float
    n_fail_to_pass: int
    patch_lines: int
    files_touched: int
    cross_file: bool
    env_spec_hash: str = ""
    image_name: str = ""
    # compiled is a validation-phase field; not all emitted instances carry it.
    compiled: bool = True

    # Validation-evidence columns
    n_runs: int
    quarantined_tests: list[str]
    # SWE-benchify emits this as "decontamination_overlap".
    decontam_overlap: bool = Field(alias="decontamination_overlap", default=False)

    # F2P / P2P test lists — may be JSON-encoded strings or plain lists.
    # Used by SwebenchifyGrader to know which tests to check.
    fail_to_pass: list[str] = Field(alias="FAIL_TO_PASS", default_factory=list)
    pass_to_pass: list[str] = Field(alias="PASS_TO_PASS", default_factory=list)

    @field_validator("product", "env_spec_hash", "image_name", mode="before")
    @classmethod
    def _coerce_nullable_str(cls, v: Any) -> str:
        return v if isinstance(v, str) else ""

    @model_validator(mode="after")
    def _product_from_repo(self) -> "SWEbenchInstance":
        self.product = self.repo
        return self

    @field_validator("fail_to_pass", "pass_to_pass", mode="before")
    @classmethod
    def _decode_test_list(cls, v: Any) -> list[str]:
        """Accept both JSON-encoded strings and plain lists."""
        if isinstance(v, str):
            try:
                decoded = json.loads(v)
                if isinstance(decoded, list):
                    return [str(x) for x in decoded]
            except json.JSONDecodeError:
                pass
            return []
        if isinstance(v, list):
            return [str(x) for x in v]
        return []


def filter_by_year(
    instances: list[SWEbenchInstance],
    years: list[int],
) -> list[SWEbenchInstance]:
    """Return only instances whose fix_merge_date falls in one of the given years.

    fix_merge_date is an ISO 8601 string (e.g. "2024-12-12T14:02:26Z").
    The year is extracted from the first four characters.
    """
    year_set = {str(y) for y in years}
    return [inst for inst in instances if inst.fix_merge_date[:4] in year_set]


def load(path: str | Path) -> list[SWEbenchInstance]:
    """Load and validate a SWE-benchify JSONL file.

    Raises SchemaError on the first record missing a required field.
    Extra fields present in the JSONL are silently ignored.
    Empty lines are skipped.
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
