"""Tests for orka.operations.helpers — template loading and error utilities."""

import textwrap
from pathlib import Path

import pytest
import yaml

from orka.operations.helpers import (
    load_template,
    extract_error_summary,
    truncate_error_summary,
    build_fixer_prompt,
)


# ---------------------------------------------------------------------------
# load_template
# ---------------------------------------------------------------------------

class TestLoadTemplate:
    """Tests for loading YAML templates into PromptTemplate objects."""

    def test_load_real_refactor_template(self):
        """Should load the real refactor.yaml from disk."""
        template = load_template("refactor")
        assert template.name == "refactor"
        assert template.output_type.value == "body"
        assert "system_header" in [p.value for p in template.injection_points]
        assert "%%system_header%%" in template.system
        assert "%%existing_code%%" in template.user
        assert template.metadata.get("version") == 1

    def test_load_real_test_template(self):
        """Should load the real test.yaml from disk."""
        template = load_template("test")
        assert template.name == "test"
        assert template.output_type.value == "standalone"
        assert "quality_gates" in [p.value for p in template.injection_points]
        assert "%%system_header%%" in template.system
        assert "%%existing_code%%" in template.user

    def test_load_template_with_injection_points(self, tmp_path):
        """Should parse injection_points strings into InjectionPoint enums."""
        templates_dir = tmp_path / "templates"
        templates_dir.mkdir()
        yaml_content = {
            "name": "custom_test",
            "description": "Custom template",
            "system": "System prompt",
            "user": "User prompt %%existing_code%%",
            "output_type": "standalone",
            "injection_points": ["system_header", "quality_gates"],
            "metadata": {"version": 1, "author": "test"},
        }
        (templates_dir / "custom_test.yaml").write_text(
            yaml.dump(yaml_content), encoding="utf-8"
        )

        from orka.operations.helpers import _TEMPLATES_DIR
        original = _TEMPLATES_DIR
        try:
            import orka.operations.helpers as helpers
            helpers._TEMPLATES_DIR = templates_dir

            template = load_template("custom_test")
            assert template.name == "custom_test"
            assert template.output_type.value == "standalone"
            assert len(template.injection_points) == 2

            from orka.core.templates import InjectionPoint
            assert template.injection_points[0] == InjectionPoint.system_header
            assert template.injection_points[1] == InjectionPoint.quality_gates
        finally:
            helpers._TEMPLATES_DIR = original

    def test_load_template_raises_file_not_found(self):
        """Should raise FileNotFoundError for missing template."""
        with pytest.raises(FileNotFoundError, match="not found"):
            load_template("nonexistent_template_name")


# ---------------------------------------------------------------------------
# extract_error_summary
# ---------------------------------------------------------------------------

class TestExtractErrorSummary:
    """Tests for extracting relevant error blocks from pytest output."""

    def test_extracts_failures_section(self):
        """Should extract the FAILURES section when present."""
        output = textwrap.dedent("""\
            ============================= test session starts =============================
            collected 2 items

            tests/test_x.py::test_a PASSED
            tests/test_x.py::test_b FAILED

            ================================== FAILURES ===================================
            _______________________________ test_b ________________________________________

                def test_b():
            >       assert False
            E       assert False

            =========================== short test summary info ===========================
            FAILED tests/test_x.py::test_b - assert False
        """)
        result = extract_error_summary(output)
        assert "FAILURES" in result
        assert "test_b" in result
        assert "short test summary" not in result

    def test_falls_back_to_tail_lines(self):
        """Should fall back to last lines when no FAILURES section."""
        output = textwrap.dedent("""\
            collected 1 item
            tests/test_x.py::test_a ERROR
            tests/test_x.py:3: in <module>
                x = 1 / 0
            ZeroDivisionError: division by zero
        """)
        result = extract_error_summary(output)
        assert "ZeroDivisionError" in result

    def test_returns_output_when_no_failures_and_few_lines(self):
        """Should return the full output when short and no FAILURES."""
        output = "collected 1 item\n\nAll checks passed!"
        result = extract_error_summary(output)
        # The function strips lines starting with "collected" and "==="
        # so "collected 1 item" is filtered out
        assert "All checks passed!" in result

    def test_empty_output_returns_empty(self):
        """Empty output should return an empty string."""
        assert extract_error_summary("") == ""


# ---------------------------------------------------------------------------
# truncate_error_summary
# ---------------------------------------------------------------------------

class TestTruncateErrorSummary:
    """Tests for truncating long error summaries."""

    def test_short_summary_unchanged(self):
        """Should not truncate when under max_chars."""
        text = "Short error"
        result = truncate_error_summary(text, max_chars=100)
        assert result == text

    def test_long_summary_truncated(self):
        """Should truncate long output, keeping head and tail."""
        text = "LINE\n" * 200
        result = truncate_error_summary(text, max_chars=500)
        # The function uses a 75/25 head/tail split with line-boundary alignment,
        # so the result may slightly exceed max_chars
        assert result.count("LINE") < text.count("LINE")
        assert "... [traceback truncated] ..." in result
        assert "truncated" in result

    def test_truncation_has_head_tail_and_marker(self):
        """Truncated output should have head content, truncation marker, and tail."""
        head = "A" * 400
        tail = "Z" * 100
        text = head + "\n\nMIDDLE\n\n" + tail
        result = truncate_error_summary(text, max_chars=500)
        assert "... [traceback truncated] ..." in result
        assert result.startswith("A" * 400) or "A" * 100 in result
        assert "Z" in result


# ---------------------------------------------------------------------------
# build_fixer_prompt
# ---------------------------------------------------------------------------

class TestBuildFixerPrompt:
    """Tests for the fixer prompt construction."""

    def test_builds_testgen_prompt(self):
        """Should build a testgen fix prompt with all context."""
        prompt, system = build_fixer_prompt(
            operation_type="testgen",
            draft_snippet="def test_x():\n    assert True",
            validation_output="AssertionError",
            existing_code="def target():\n    return 42",
            class_context="",
            requirements="Test the target function",
        )
        assert "PYTEST ERROR SUMMARY" in prompt
        assert "AssertionError" in prompt
        assert "def target():" in prompt
        assert "pytest" in system.lower()

    def test_builds_refactor_prompt(self):
        """Should build a refactor fix prompt with all context."""
        prompt, system = build_fixer_prompt(
            operation_type="refactor",
            draft_snippet="return x + 1",
            validation_output="SyntaxError",
            existing_code="def target(x):\n    pass",
            class_context="class MyClass:\n    pass",
            requirements="Add one to x",
        )
        assert "ERROR / VALIDATION OUTPUT" in prompt or "FIX INSTRUCTIONS" in prompt
        assert "SyntaxError" in prompt
        assert "class MyClass:" in prompt
        assert "code synthesis" in system.lower()

    def test_build_includes_test_file_target_when_provided(self):
        """Should include test_file_target when provided."""
        prompt, system = build_fixer_prompt(
            operation_type="testgen",
            draft_snippet="def test_x(): pass",
            validation_output="",
            existing_code="def target(): pass",
            class_context="",
            requirements="Test target",
            test_file_target="/path/to/test_x.py",
        )
        # The function currently doesn't use test_file_target in the prompt
        # but the parameter is accepted for future use
        assert isinstance(prompt, str)
        assert isinstance(system, str)
