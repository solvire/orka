"""Tests for orka.core.validator — syntax validation utilities."""

import textwrap
from pathlib import Path

import pytest

from orka.core.validator import (
    ValidationResult,
    validate_code_snippet,
    validate_file,
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
