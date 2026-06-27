"""Declarative language configuration registry.

Each ``LanguageConfig`` bundles every language-specific detail needed by the
scaffold (system prompt), grader (test-file detection, env-spec defaults),
and anti-reward-hacking checks. Adding a new language is a single
``register()`` call — no if/else branches anywhere.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass


@dataclass(frozen=True)
class LanguageConfig:
    """Language-specific configuration consumed by scaffold and grader."""

    name: str
    system_prompt: str
    test_file_globs: tuple[str, ...]
    test_dir_markers: tuple[str, ...] = ()
    default_language_version: str = ""
    package_manager: str = ""
    install_cmd: str = ""
    test_cmd: str = ""

    def is_test_file(self, path: str) -> bool:
        """Return True if *path* matches any test-file pattern for this language."""
        basename = path.rsplit("/", 1)[-1]
        for glob in self.test_file_globs:
            if fnmatch.fnmatch(basename, glob):
                return True
        for marker in self.test_dir_markers:
            if marker in path:
                return True
        return False


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_CONFIGS: dict[str, LanguageConfig] = {}
_DEFAULT_LANGUAGE = "go"


def register(config: LanguageConfig) -> None:
    _CONFIGS[config.name] = config


def get_config(language: str) -> LanguageConfig:
    """Look up a language config. Raises ``KeyError`` for unknown languages."""
    return _CONFIGS[language]


def get_config_or_default(language: str) -> LanguageConfig:
    """Look up a language config, falling back to Go for unknown languages."""
    return _CONFIGS.get(language, _CONFIGS[_DEFAULT_LANGUAGE])


# ---------------------------------------------------------------------------
# Go
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT_GO = """\
You are an expert Go software engineer. You will be given a bug description and \
access to a Git repository checked out at the buggy commit.

Your task is to produce a minimal patch that fixes the described bug.

Workflow:
1. Use `bash` to explore the repository and understand the relevant code.
2. Make targeted edits with `bash` (e.g. using sed, patch, or direct file writes).
3. Verify the code compiles: `go build ./...`
4. Call `finish` when done.

Rules:
- Do NOT modify test files (*_test.go or anything under testdata/ directories).
- Keep the fix minimal — change only what is necessary.
- Do not add new external dependencies.
"""

register(LanguageConfig(
    name="go",
    system_prompt=_SYSTEM_PROMPT_GO,
    test_file_globs=("*_test.go",),
    test_dir_markers=("/testdata/",),
    default_language_version="1.22",
    package_manager="go modules",
    install_cmd="",
    test_cmd="go test -json -count=1",
))


# ---------------------------------------------------------------------------
# Python
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT_PYTHON = """\
You are an expert Python software engineer. You will be given a bug description and \
access to a Git repository checked out at the buggy commit.

Your task is to produce a minimal patch that fixes the described bug.

Workflow:
1. Use `bash` to explore the repository and understand the relevant code.
2. Make targeted edits with `bash` (e.g. using sed, patch, or direct file writes).
3. Verify the code runs: `python -c "import <package>"` or run a quick sanity check.
4. Call `finish` when done.

Rules:
- Do NOT modify test files (test_*.py, *_test.py, conftest.py).
- Keep the fix minimal — change only what is necessary.
- Do not add new external dependencies.
"""

register(LanguageConfig(
    name="python",
    system_prompt=_SYSTEM_PROMPT_PYTHON,
    test_file_globs=("test_*.py", "*_test.py", "conftest.py"),
    default_language_version="3.11",
    package_manager="pip",
    install_cmd="pip install -e .",
    test_cmd="pytest",
))
