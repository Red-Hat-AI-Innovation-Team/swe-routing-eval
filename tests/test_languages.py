"""Tests for the declarative language configuration registry."""

from __future__ import annotations

import pytest

from swe_routing_eval.languages import (
    LanguageConfig,
    get_config,
    get_config_or_default,
)


class TestRegistry:
    def test_go_registered(self) -> None:
        config = get_config("go")
        assert config.name == "go"

    def test_python_registered(self) -> None:
        config = get_config("python")
        assert config.name == "python"

    def test_unknown_raises(self) -> None:
        with pytest.raises(KeyError):
            get_config("java")

    def test_unknown_falls_back_to_go(self) -> None:
        config = get_config_or_default("java")
        assert config.name == "go"


class TestIsTestFile:
    # Go
    def test_go_test_file(self) -> None:
        config = get_config("go")
        assert config.is_test_file("pkg/cmd/foo_test.go") is True

    def test_go_testdata_dir(self) -> None:
        config = get_config("go")
        assert config.is_test_file("pkg/testdata/fixture.yaml") is True

    def test_go_implementation_file(self) -> None:
        config = get_config("go")
        assert config.is_test_file("pkg/cmd/foo.go") is False

    def test_go_ignores_python_test(self) -> None:
        config = get_config("go")
        assert config.is_test_file("tests/test_utils.py") is False

    # Python
    def test_python_test_prefix(self) -> None:
        config = get_config("python")
        assert config.is_test_file("tests/test_utils.py") is True

    def test_python_test_suffix(self) -> None:
        config = get_config("python")
        assert config.is_test_file("src/widget_test.py") is True

    def test_python_conftest(self) -> None:
        config = get_config("python")
        assert config.is_test_file("tests/conftest.py") is True

    def test_python_implementation_file(self) -> None:
        config = get_config("python")
        assert config.is_test_file("src/utils.py") is False

    def test_python_ignores_go_test(self) -> None:
        config = get_config("python")
        assert config.is_test_file("pkg/cmd/foo_test.go") is False

    def test_python_non_test_with_test_in_name(self) -> None:
        config = get_config("python")
        assert config.is_test_file("src/testing_helpers.py") is False


class TestSystemPrompt:
    def test_go_prompt_mentions_go(self) -> None:
        config = get_config("go")
        assert "Go" in config.system_prompt
        assert "_test.go" in config.system_prompt

    def test_python_prompt_mentions_python(self) -> None:
        config = get_config("python")
        assert "Python" in config.system_prompt
        assert "test_*.py" in config.system_prompt

    def test_prompts_differ(self) -> None:
        go = get_config("go")
        py = get_config("python")
        assert go.system_prompt != py.system_prompt


class TestDefaults:
    def test_go_defaults(self) -> None:
        config = get_config("go")
        assert config.default_language_version == "1.22"
        assert config.package_manager == "go modules"

    def test_python_defaults(self) -> None:
        config = get_config("python")
        assert config.default_language_version == "3.11"
        assert config.package_manager == "pip"
        assert config.install_cmd == "pip install -e ."
        assert config.test_cmd == "pytest"
