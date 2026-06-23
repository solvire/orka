"""Reusable code validation utilities for Orka's surgery pipeline.

Validates Python code at two stages:
  1. Raw snippet validation — run on LLM output before patching.
  2. File validation — run on disk after a surgical patch is applied.

This module is also the canonical home of the unified **4-gate validation
pipeline** (:func:`validate_four_gates`), which consolidates the logic that
used to live inline in ``operations/controllers/validator.py``.  The
pytest-output error helpers (:func:`extract_error_summary`,
:func:`truncate_error_summary`) live here too so the pipeline does not have
to reach back up into the ``operations`` layer (which would invert the
``core`` ← ``operations`` dependency direction); ``operations.helpers``
re-exports them for backward compatibility.

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

    # Run the whole 4-gate pipeline (snippet AST -> assembly -> file AST ->
    # pytest) without the full SurgeryState dict:
    passed, output, assembled = validate_four_gates(
        snippet=clean_logic,
        source_file="src/payments/processor.py",
        target_file="src/payments/processor.py",
        target_node_id="OrderController.process",
        operation_type="refactor",
        method_name="process",
        class_name="OrderController",
    )
"""

from __future__ import annotations

import ast
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional, Union

from orka.config import settings
from orka.core.import_injector import (
    auto_import,
    extract_imports,
    resolve_import_for_test,
)


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


# ---------------------------------------------------------------------------
# Pytest-output error helpers (canonical location)
# ---------------------------------------------------------------------------
#
# These were originally defined in ``orka.operations.helpers``.  They are pure
# string utilities with no ``operations``-layer dependencies, so they belong
# here — next to the validator that uses them — and ``operations.helpers``
# re-exports them for backward compatibility.  Keeping them in ``core`` is
# what lets :func:`validate_four_gates` be self-contained without inverting
# the ``core`` ← ``operations`` dependency direction.

_MAX_ERROR_SUMMARY_CHARS = 2000


def extract_error_summary(pytest_output: str) -> str:
    """Extract the most relevant error block from pytest output.

    Prioritises the ``FAILURES`` section, falling back to the last 40
    significant lines (skipping ``===`` headers, collection info, etc.).
    """
    if "FAILURES" in pytest_output:
        idx = pytest_output.index("FAILURES")
        relevant = pytest_output[idx:]
        if "short test summary" in relevant:
            relevant = relevant[: relevant.index("short test summary")]
        return relevant.strip()

    lines = [
        l
        for l in pytest_output.strip().splitlines()
        if not l.startswith("===")
        and not l.startswith("collected")
        and not l.startswith("platform")
        and not l.startswith("rootdir")
    ]
    return "\n".join(lines[-40:])


def truncate_error_summary(
    summary: str,
    max_chars: int = _MAX_ERROR_SUMMARY_CHARS,
) -> str:
    """Truncate error summary, keeping the most recent (bottom) portion.

    Uses the same pattern as ``tdd_pipeline.py``: keep the first 1500
    characters and the last 500, with a truncation marker in between.
    """
    if len(summary) <= max_chars:
        return summary

    head_chars = int(max_chars * 0.75)  # ~1500 of 2000
    tail_chars = max_chars - head_chars  # ~500

    head = summary[:head_chars]
    tail = summary[-tail_chars:]

    # Ensure we don't break in the middle of a line
    last_newline = head.rfind("\n")
    if last_newline > 0:
        head = summary[:last_newline]

    first_newline = tail.find("\n")
    if first_newline > 0:
        tail = tail[first_newline + 1 :]

    return f"{head}\n... [traceback truncated] ...\n{tail}"


# ---------------------------------------------------------------------------
# 4-gate validation pipeline
# ---------------------------------------------------------------------------


def validate_four_gates(
    snippet: str,
    source_file: str,
    target_file: str,
    target_node_id: str,
    operation_type: str = "refactor",
    class_name: Optional[str] = None,
    method_name: Optional[str] = None,
    graph_db: Optional[object] = None,
    dry_run: bool = False,
    test_file_target: Optional[str] = None,
) -> tuple[bool, str, Optional[str]]:
    """Run a code snippet through all four validation gates.

    This is the unified entry point for the 4-gate pipeline.  It consolidates
    the logic previously inline in
    ``operations/controllers/validator.py:execute()`` and is callable without
    the full :class:`~orka.operations.state.SurgeryState` dict.

    Gates:
    1. **Snippet AST** — :func:`validate_code_snippet`.
    2. **Assembly** — ``preview_patch`` + ``auto_import`` (refactor) or
       ``resolve_import_for_test`` + ``extract_imports`` (test).
    3. **File AST** — ``ast.parse`` on the assembled content.
    4. **Pytest** — subprocess pytest (skipped when *dry_run* is ``True``).

    Parameters
    ----------
    snippet
        Raw LLM output — a method body (refactor) or test functions (test).
    source_file
        Path to the source file containing the target method/function.
    target_file
        Path where the assembled output is written (the file being modified
        or created).  Ignored when *dry_run* is ``True``.
    target_node_id
        e.g. ``"MyClass.my_method"`` — used as the snippet label.
    operation_type
        ``"refactor"`` or ``"test"``.
    class_name
        Enclosing class name (``None`` for standalone functions).
    method_name
        Method or function name to operate on.
    graph_db
        An ``OrkaGraphDB`` instance (or ``None``) used by auto-import and
        test-import resolution.
    dry_run
        If ``True``, stop after Gate 3 — no disk write, no pytest.
    test_file_target
        If set, pytest runs against this file instead of *target_file*.

    Returns
    -------
    tuple[bool, str, Optional[str]]
        ``(passed, output_message, assembled_content)`` where:

        - *passed* — whether all run gates passed.
        - *output_message* — error description on failure, ``""`` on success
          (or a dry-run notice).
        - *assembled_content* — the full assembled file content, or ``None``
          if assembly (Gate 2) never succeeded.
    """
    if not snippet:
        return False, "No draft snippet to validate.", None

    # ── Gate 1: Snippet AST validation ────────────────────────────────
    snippet_result = validate_code_snippet(snippet, label=target_node_id)
    if not snippet_result:
        logger.warning("Gate 1 (snippet AST) failed: %s", snippet_result.error)
        return (
            False,
            f"Syntax error in generated code:\n{snippet_result.error}",
            None,
        )
    logger.debug("Gate 1 (snippet AST) PASSED for %s", target_node_id)

    # ── Gate 2: Assembly ──────────────────────────────────────────────
    try:
        if operation_type == "refactor":
            assembled = _assemble_refactor_file(
                source_file=source_file,
                snippet=snippet,
                target_node_id=target_node_id,
                class_name=class_name,
                method_name=method_name,
                graph_db=graph_db,
            )
        elif operation_type == "test":
            assembled = _assemble_test_file(
                snippet=snippet,
                source_file=source_file,
                class_name=class_name,
                method_name=method_name,
                graph_db=graph_db,
            )
        else:
            raise ValueError(f"Unknown operation type: {operation_type}")
    except Exception as e:
        logger.error("Gate 2 (assembly) failed: %s", e)
        return False, f"Failed to assemble file: {e}", None

    # ── Gate 3: File AST validation ───────────────────────────────────
    try:
        ast.parse(assembled)
    except SyntaxError as e:
        logger.warning("Gate 3 (file AST) failed: %s", e)
        return (
            False,
            f"Syntax error in assembled file:\n{e.msg} (line {e.lineno})",
            assembled,
        )
    logger.debug("Gate 3 (file AST) PASSED for %s", target_node_id)

    # ── Dry-run: stop before disk write + pytest ──────────────────────
    if dry_run:
        return True, "Dry-run mode — validation skipped after AST pass.", assembled

    # ── Gate 4: Disk write + Pytest ───────────────────────────────────
    passed, output = _write_and_run_pytest(assembled, target_file, test_file_target)
    if passed:
        logger.info("All validations PASSED for %s", target_node_id)
    else:
        logger.warning("Gate 4 (pytest) FAILED for %s", target_node_id)
    return passed, output, assembled


# ---------------------------------------------------------------------------
# 4-gate internal helpers
# ---------------------------------------------------------------------------


def _assemble_refactor_file(
    source_file: str,
    snippet: str,
    target_node_id: str,
    class_name: Optional[str],
    method_name: Optional[str],
    graph_db: Optional[object],
) -> str:
    """Use LibCST to patch the snippet into the source file (Gate 2).

    After patching, runs :func:`auto_import` to detect undefined names in the
    patched code and inject the correct imports via the Graph DB + LibCST's
    ``AddImportsVisitor``.  Returns the full patched file content.
    """
    from orka.surgery.modifier import preview_patch

    patched = preview_patch(
        file_path=source_file,
        target_method=method_name,
        new_logic=snippet,
        target_class=class_name,
    )

    if patched is None:
        raise RuntimeError(
            f"LibCST could not find {target_node_id} in {source_file}. "
            "The method/function may have been renamed or removed."
        )

    # ── Auto-import step ──────────────────────────────────────────────
    # Detect undefined names in the patched file and resolve them via the
    # Graph DB before file AST validation and pytest.
    try:
        patched = auto_import(patched, file_path=source_file, graph_db=graph_db)
    except Exception:
        logger.debug("Auto-import step failed — continuing with patched code as-is.")

    return patched


def _assemble_test_file(
    snippet: str,
    source_file: str,
    class_name: Optional[str],
    method_name: Optional[str],
    graph_db: Optional[object],
) -> str:
    """Build a complete test file with imports prepended (Gate 2).

    Uses the deterministic :func:`resolve_import_for_test` (no LLM involved).
    The snippet is passed through :func:`extract_imports` (CST-based) to strip
    any import statements the LLM may have erroneously included, preventing
    duplicate imports in the output.
    """
    import_stmt = resolve_import_for_test(
        file_path=source_file,
        class_name=class_name,
        method_name=method_name,
        workspace_dir=str(settings.PROJECT_ROOT),
        graph_db=graph_db,
    )

    if import_stmt is None:
        # Fallback — try loading the Graph DB from the on-disk cache.
        try:
            from orka.core.ingester import OrkaGraphDB

            cache_file = os.path.join(str(settings.PROJECT_ROOT), ".orka_cache.json")
            if os.path.exists(cache_file):
                fallback_db = OrkaGraphDB(cache_file=cache_file)
                import_stmt = resolve_import_for_test(
                    file_path=source_file,
                    class_name=class_name,
                    method_name=method_name,
                    workspace_dir=str(settings.PROJECT_ROOT),
                    graph_db=fallback_db,
                )
        except Exception:
            pass

    if import_stmt is None:
        raise RuntimeError(
            f"Could not resolve import for {method_name or class_name} in "
            f"{source_file}."
        )

    # ── Strip import statements from the LLM snippet ──────────────────
    # The LLM sometimes emits import statements inside the snippet despite
    # being instructed not to.  extract_imports handles edge cases like
    # ``import os; x = 1`` cleanly.
    try:
        clean_snippet, _ = extract_imports(snippet)
    except Exception:
        clean_snippet = snippet  # fallback — keep snippet as-is

    return f"{import_stmt}{clean_snippet}\n"


def _run_pytest(test_target: str) -> tuple[bool, str]:
    """Run pytest against *test_target* in a subprocess (Gate 4).

    Returns ``(passed, output)`` where *output* is the full stdout+stderr.
    """
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                test_target,
                "--exitfirst",
                "--tb=short",
                "--no-header",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return False, "pytest timed out after 120 seconds."
    except FileNotFoundError:
        return False, "pytest not found. Is it installed?"
    except Exception as e:
        return False, f"Subprocess error: {e}"

    output = result.stdout + "\n" + result.stderr
    return (result.returncode == 0), output


def _write_and_run_pytest(
    draft_file_content: str,
    target_file: str,
    test_file_target: Optional[str],
) -> tuple[bool, str]:
    """Write the assembled file to disk and run pytest (Gate 4).

    Returns ``(passed, output_message)`` — ``""`` on success, a truncated
    error summary on pytest failure, or a write-error message on OSError.
    """
    try:
        directory = os.path.dirname(target_file)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(target_file, "w", encoding="utf-8") as f:
            f.write(draft_file_content)
    except OSError as e:
        return False, f"Failed to write {target_file}: {e}"

    test_target = test_file_target or target_file
    pytest_passed, pytest_output = _run_pytest(test_target)
    if pytest_passed:
        return True, ""

    error_summary = extract_error_summary(pytest_output)
    error_summary = truncate_error_summary(error_summary)
    return False, error_summary
