"""Reusable code validation utilities for Orka's surgery pipeline.

Validates Python code at two stages:
  1. Raw snippet validation — run on LLM output before patching.
  2. File validation — run on disk after a surgical patch is applied.

Usage:
    from orka.core.validator import validate_code_snippet, validate_file

    # After LLM generates code, before LibCST patch:
    result = validate_code_snippet(clean_logic, label="MyClass.my_method")
    if not result:
        logger.error(f"LLM produced invalid code: {result.error}")

    # After LibCST applies patch to disk:
    result = validate_file("/path/to/file.py")
    if not result:
        logger.error(f"Patch broke syntax: {result.error}")
"""

import ast
import logging
from pathlib import Path
from typing import Optional, Union


logger = logging.getLogger("Validator")


class ValidationResult:
    """Structured result from code validation.

    Attributes:
        passed: Whether validation succeeded.
        error: Human-readable error description.
        lineno: Line number where the error occurred (if applicable).
        msg: Raw exception message from the parser (if applicable).
    """

    def __init__(
        self,
        passed: bool,
        error: Optional[str] = None,
        lineno: Optional[int] = None,
        msg: Optional[str] = None,
    ):
        self.passed = passed
        self.error = error
        self.lineno = lineno
        self.msg = msg

    def __bool__(self) -> bool:
        return self.passed

    def __repr__(self) -> str:
        if self.passed:
            return "<ValidationResult: PASSED>"
        return f"<ValidationResult: FAILED line {self.lineno} — {self.msg}>"


# ---------------------------------------------------------------------------
# Snippet validation
# ---------------------------------------------------------------------------

def validate_code_snippet(code: str, label: str = "snippet") -> ValidationResult:
    """Validate a raw Python code snippet (e.g., LLM output).

    The snippet is expected to be *body-level* code (no signature, no class
    wrapper) at the base indentation level — exactly what the LLM returns in
    the refactoring pipeline.  We wrap it in a dummy function so that
    ``ast.parse`` can handle bare statements like ``return x`` correctly.

    Args:
        code: Raw Python source string.
        label: A short label for error messages (e.g. ``"OrderController.process"``).

    Returns:
        A ``ValidationResult`` with ``passed=True`` if the snippet is valid.
    """
    if not code or not code.strip():
        return ValidationResult(False, error="Empty code snippet")

    try:
        # Wrap the body-level snippet in a dummy function so that bare
        # statements (return, raise, etc.) parse correctly.
        indented = _indent_body(code.strip())
        wrapped = f"def _orka_validation_wrapper():\n{indented}"
        ast.parse(wrapped)
        return ValidationResult(True)
    except SyntaxError as e:
        return ValidationResult(
            passed=False,
            lineno=e.lineno,
            msg=e.msg,
            error=f"Syntax error in {label}: {e.msg}",
        )


# ---------------------------------------------------------------------------
# File validation
# ---------------------------------------------------------------------------

def validate_file(file_path: Union[str, Path]) -> ValidationResult:
    """Validate a Python file on disk with ``ast.parse``.

    Use this **after** a surgical patch has been applied to confirm the file
    is still syntactically valid Python.

    Args:
        file_path: Path to the Python file to validate.

    Returns:
        A ``ValidationResult`` with ``passed=True`` if the file is valid.
    """
    path = Path(file_path)
    if not path.exists():
        return ValidationResult(False, error=f"File not found: {file_path}")

    try:
        with open(path, "r", encoding="utf-8") as f:
            ast.parse(f.read())
        return ValidationResult(True)
    except SyntaxError as e:
        return ValidationResult(
            passed=False,
            lineno=e.lineno,
            msg=e.msg,
            error=f"Syntax error in {path.name} (line {e.lineno}): {e.msg}",
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _indent_body(code: str, indent: str = "    ") -> str:
    """Indent every line of *code* by *indent*.

    Handles Windows and Unix line endings transparently.
    """
    lines = code.splitlines()
    if not lines:
        return ""
    return "\n".join(f"{indent}{line}" if line.strip() else line for line in lines)
