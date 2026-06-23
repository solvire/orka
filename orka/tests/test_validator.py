"""Tests for orka.core.validator — syntax validation utilities."""

import textwrap
from pathlib import Path

import pytest

from orka.config import settings
from orka.core.validator import (
    ValidationResult,
    validate_code_snippet,
    validate_file,
    validate_four_gates,
    _indent_body,
)


# ---------------------------------------------------------------------------
# _indent_body
# ---------------------------------------------------------------------------

class TestIndentBody:
    def test_indents_each_line(self):
        result = _indent_body("return x\nx = 1")
        assert result == "    return x\n    x = 1"

    def test_handles_empty_string(self):
        assert _indent_body("") == ""

    def test_preserves_blank_lines(self):
        code = "return x\n\nx = 1"
        result = _indent_body(code)
        assert result == "    return x\n\n    x = 1"

    def test_strips_no_leading_whitespace_from_input(self):
        """The caller is expected to .strip() first; our job is just to indent."""
        code = "  return x"
        result = _indent_body(code)
        assert result == "      return x"


# ---------------------------------------------------------------------------
# validate_code_snippet
# ---------------------------------------------------------------------------

class TestValidateCodeSnippet:
    def test_valid_simple_return(self):
        """A plain return statement should be valid."""
        result = validate_code_snippet("return True", label="test")
        assert result.passed is True
        assert result.error is None

    def test_valid_multi_line_body(self):
        """Multi-line method body with expressions."""
        code = textwrap.dedent("""\
            result = some_function()
            if result:
                logger.info("Success")
            return result
        """)
        result = validate_code_snippet(code, label="test")
        assert result.passed is True

    def test_valid_with_docstring(self):
        """Method body with a docstring should parse."""
        code = textwrap.dedent('''\
            """Process the order."""
            logger.info("Processing")
            return True
        ''')
        result = validate_code_snippet(code, label="test")
        assert result.passed is True

    def test_invalid_syntax(self):
        """Bare syntax error should be caught."""
        code = "if True"
        result = validate_code_snippet(code, label="test")
        assert result.passed is False
        assert result.error is not None
        assert result.lineno is not None

    def test_invalid_indented_block_mismatch(self):
        """A mismatched indentation error."""
        code = textwrap.dedent("""\
            if True:
            return False
        """)
        result = validate_code_snippet(code, label="test")
        assert result.passed is False

    def test_empty_string(self):
        """Empty code should fail."""
        result = validate_code_snippet("", label="test")
        assert result.passed is False
        assert "Empty" in result.error

    def test_whitespace_only(self):
        """Whitespace-only code should fail."""
        result = validate_code_snippet("   \n  \n", label="test")
        assert result.passed is False
        assert "Empty" in result.error

    def test_uses_label_in_error(self):
        """The label should appear in the error message."""
        result = validate_code_snippet("break away", label="MyClass.do_thing")
        assert result.passed is False
        assert "MyClass.do_thing" in result.error

    def test_valid_complex_code(self):
        """A realistic block with try/except and context managers."""
        code = textwrap.dedent("""\
            try:
                with open(path) as f:
                    data = f.read()
                return process(data)
            except OSError as e:
                logger.error(f"Failed: {e}")
                return None
        """)
        result = validate_code_snippet(code, label="test")
        assert result.passed is True

    def test_async_code(self):
        """Async/await syntax should parse correctly."""
        code = textwrap.dedent("""\
            result = await fetch_data()
            await cache.set(key, result)
            return result
        """)
        result = validate_code_snippet(code, label="test")
        assert result.passed is True

    def test_walrus_operator(self):
        """:= (walrus operator) is valid modern Python."""
        code = textwrap.dedent("""\
            if (n := len(items)) > 0:
                logger.info(f"Found {n} items")
            return n
        """)
        result = validate_code_snippet(code, label="test")
        assert result.passed is True

    def test_type_annotations_in_body(self):
        """Type annotations in assignments (e.g., x: int = 1) are valid."""
        code = textwrap.dedent("""\
            x: int = some_function()
            y: Optional[str] = None
            return x
        """)
        result = validate_code_snippet(code, label="test")
        assert result.passed is True

    def test_fstring_with_braces(self):
        """f-strings containing braces should not confuse the parser."""
        code = textwrap.dedent("""\
            template = f"{{{{ {key} }}}}"
            return template
        """)
        result = validate_code_snippet(code, label="test")
        assert result.passed is True


# ---------------------------------------------------------------------------
# validate_file
# ---------------------------------------------------------------------------

class TestValidateFile:
    def test_valid_file(self, tmp_path):
        """A syntactically valid Python file should pass."""
        path = tmp_path / "good.py"
        path.write_text("x = 1\nprint(x)\n")
        result = validate_file(path)
        assert result.passed is True

    def test_file_with_syntax_error(self, tmp_path):
        """A file with a syntax error should fail."""
        path = tmp_path / "bad.py"
        path.write_text("x = 1\ny = {\n")
        result = validate_file(path)
        assert result.passed is False
        assert result.lineno is not None

    def test_nonexistent_file(self):
        """A missing file should fail with a clear message."""
        result = validate_file("/nonexistent/path.py")
        assert result.passed is False
        assert "not found" in result.error

    def test_empty_file(self, tmp_path):
        """An empty file is technically valid Python."""
        path = tmp_path / "empty.py"
        path.write_text("")
        result = validate_file(path)
        assert result.passed is True

    def test_file_with_only_comment(self, tmp_path):
        """A file containing only a comment is valid."""
        path = tmp_path / "comment.py"
        path.write_text("# This is a comment\n")
        result = validate_file(path)
        assert result.passed is True

    def test_file_with_class_and_methods(self, tmp_path):
        """A realistic file with a class."""
        code = textwrap.dedent("""\
            import logging
            from django.db import transaction

            class OrderController:
                @transaction.atomic
                def process(self, order_id: str) -> bool:
                    logger.info(f"Processing {order_id}")
                    return True
        """)
        path = tmp_path / "controller.py"
        path.write_text(code)
        result = validate_file(path)
        assert result.passed is True

    def test_file_with_unicode(self, tmp_path):
        """Files with Unicode characters should be handled."""
        path = tmp_path / "unicode.py"
        path.write_text("# © 2024\nx = 'héllo'\n")
        result = validate_file(path)
        assert result.passed is True


# ---------------------------------------------------------------------------
# ValidationResult
# ---------------------------------------------------------------------------

class TestValidationResult:
    def test_bool_true_when_passed(self):
        assert bool(ValidationResult(passed=True)) is True

    def test_bool_false_when_not_passed(self):
        assert bool(ValidationResult(passed=False)) is False

    def test_passed_repr(self):
        r = ValidationResult(passed=True)
        assert "PASSED" in repr(r)

    def test_failed_repr(self):
        r = ValidationResult(passed=False, lineno=5, msg="invalid syntax")
        assert "FAILED" in repr(r)
        assert "line 5" in repr(r)
        assert "invalid syntax" in repr(r)


# ---------------------------------------------------------------------------
# validate_four_gates
# ---------------------------------------------------------------------------


class TestValidateFourGates:
    """Tests for the unified 4-gate validation pipeline.

    Return contract: ``(passed, output_message, assembled_content)`` where
    *assembled_content* is ``None`` whenever assembly (Gate 2) never succeeds.
    """

    def test_empty_snippet(self):
        """An empty snippet short-circuits before any gate runs."""
        passed, output, assembled = validate_four_gates(
            snippet="",
            source_file="dummy.py",
            target_file="dummy.py",
            target_node_id="x",
        )
        assert passed is False
        assert "No draft" in output
        assert assembled is None

    def test_gate1_invalid_snippet_syntax(self, tmp_path):
        """Gate 1: a syntactically invalid snippet fails immediately."""
        passed, output, assembled = validate_four_gates(
            snippet="if True",
            source_file=str(tmp_path / "src.py"),
            target_file=str(tmp_path / "src.py"),
            target_node_id="add",
            operation_type="refactor",
            method_name="add",
        )
        assert passed is False
        assert "Syntax error" in output
        assert assembled is None

    def test_gate2_target_method_not_found(self, tmp_path):
        """Gate 2: a valid snippet whose target method is missing fails assembly."""
        src = tmp_path / "calc.py"
        src.write_text("def add(a, b):\n    return a + b\n")
        passed, output, assembled = validate_four_gates(
            snippet="return a * b",
            source_file=str(src),
            target_file=str(src),
            target_node_id="nonexistent",
            operation_type="refactor",
            method_name="nonexistent",
        )
        assert passed is False
        assert "Failed to assemble" in output
        assert assembled is None

    def test_gate3_assembled_file_syntax_error(self, tmp_path):
        """Gate 3: a snippet whose assembled form is a parse error.

        The snippet ``"    x = 1"`` has leading indentation, so it parses
        fine as a function body (Gate 1 re-indents it inside the wrapper)
        but is an ``IndentationError`` at module level once prepended with
        the test import (Gate 3).  Note: ``ast.parse`` is lenient about
        ``return``/``break`` outside functions, so an indented statement is
        used to produce a genuine parser-level error.
        """
        passed, output, assembled = validate_four_gates(
            snippet="    x = 1",
            source_file="calc.py",
            target_file=str(tmp_path / "test_calc.py"),
            target_node_id="Calculator",
            operation_type="test",
            class_name="Calculator",
        )
        assert passed is False
        assert "Syntax error in assembled file" in output
        assert assembled is not None
        assert "x = 1" in assembled

    def test_dry_run_skips_pytest(self, tmp_path):
        """Dry-run stops after Gate 3 — no disk write, no pytest."""
        src = tmp_path / "calc.py"
        src.write_text("def add(a, b):\n    return 0\n")
        passed, output, assembled = validate_four_gates(
            snippet="return a + b",
            source_file=str(src),
            target_file=str(src),
            target_node_id="add",
            operation_type="refactor",
            method_name="add",
            dry_run=True,
        )
        assert passed is True
        assert "Dry" in output
        assert assembled is not None
        assert "return a + b" in assembled
        # The file must NOT have been written — original body is intact.
        assert "return 0" in src.read_text()

    def test_full_pass_refactor(self, tmp_path):
        """Full refactor pass: snippet patches the method, pytest passes."""
        src = tmp_path / "calc.py"
        src.write_text(
            "def add(a, b):\n"
            "    return 0\n"
            "\n"
            "\n"
            "def test_add():\n"
            "    assert add(2, 3) == 5\n"
        )
        passed, output, assembled = validate_four_gates(
            snippet="return a + b",
            source_file=str(src),
            target_file=str(src),
            target_node_id="add",
            operation_type="refactor",
            method_name="add",
        )
        assert passed is True
        assert output == ""
        assert assembled is not None
        assert "return a + b" in assembled

    def test_full_pass_test(self, tmp_path, monkeypatch):
        """Full test pass: assembled test file imports the target and passes."""
        # Point the resolver's workspace at tmp_path so the source file's
        # module path resolves to a bare, importable name ("calc").
        monkeypatch.setattr(settings, "PROJECT_ROOT", tmp_path)

        calc = tmp_path / "calc.py"
        calc.write_text(
            "class Calculator:\n"
            "    def add(self, a, b):\n"
            "        return a + b\n"
        )
        snippet = (
            "def test_calculator_add():\n"
            "    c = Calculator()\n"
            "    assert c.add(2, 3) == 5\n"
        )
        passed, output, assembled = validate_four_gates(
            snippet=snippet,
            source_file=str(calc),
            target_file=str(tmp_path / "test_calc.py"),
            target_node_id="Calculator",
            operation_type="test",
            class_name="Calculator",
        )
        assert passed is True
        assert output == ""
        assert assembled is not None
        assert "from calc import Calculator" in assembled
        assert "test_calculator_add" in assembled

    def test_gate4_pytest_failure(self, tmp_path):
        """Gate 4: a refactored method that breaks the test fails pytest."""
        src = tmp_path / "calc.py"
        src.write_text(
            "def add(a, b):\n"
            "    return a + b\n"
            "\n"
            "\n"
            "def test_add():\n"
            "    assert add(2, 3) == 5\n"
        )
        passed, output, assembled = validate_four_gates(
            snippet="return a * b",
            source_file=str(src),
            target_file=str(src),
            target_node_id="add",
            operation_type="refactor",
            method_name="add",
        )
        assert passed is False
        assert assembled is not None
        assert "return a * b" in assembled
        assert output  # non-empty truncated error summary
        assert "assert" in output


# ---------------------------------------------------------------------------
# Controller thin-wrapper (operations/controllers/validator.py:execute)
# ---------------------------------------------------------------------------


class TestValidatorControllerExecute:
    """Smoke tests for the thin state-dict wrapper around validate_four_gates."""

    def test_empty_snippet_short_circuits(self):
        from orka.operations.controllers.validator import execute

        result = execute({"draft_snippet": ""})
        assert result == {
            "is_valid": False,
            "validation_output": "No draft snippet to validate.",
            "previous_validation_output": "",
        }
        # No draft_file_content key on the short-circuit path.
        assert "draft_file_content" not in result

    def test_dry_run_refactor_translates_state(self, tmp_path):
        from orka.operations.controllers.validator import execute

        src = tmp_path / "calc.py"
        src.write_text("def add(a, b):\n    return 0\n")
        state = {
            "draft_snippet": "return a + b",
            "source_file": str(src),
            "target_output_file": str(src),
            "target_node_id": "add",
            "prompt_template_name": "refactor",
            "method_name": "add",
            "dry_run": True,
        }
        result = execute(state)
        assert result["is_valid"] is True
        assert "Dry" in result["validation_output"]
        assert "draft_file_content" in result
        assert "return a + b" in result["draft_file_content"]
