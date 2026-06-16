"""
Validation node — the heavy lifter of the surgery graph.

Performs four stages of validation:

1. **Gate 1 (Snippet AST)** — Validate ``draft_snippet`` via ``ast.parse``.
2. **Assembly** — Patch the snippet into the full file (LibCST for refactor,
   import_fixer + assembly for testgen).
3. **Gate 2 (File AST)** — Validate the assembled ``draft_file_content``.
4. **Disk Write + Pytest** — Write to real path, run pytest, truncate output.

This is Node 3 of the surgery graph. It is pure Python (no LLM call).
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from typing import Any, Optional

import libcst as cst

from orka.config import settings
from orka.core.import_fixer import resolve_import
from orka.core.validator import validate_code_snippet
from orka.operations.helpers import extract_error_summary, truncate_error_summary
from orka.surgery.modifier import SnippetImportExtractor

logger = logging.getLogger(__name__)


def execute(state: dict[str, Any]) -> dict[str, Any]:
    """Validate the current draft through all four gates.

    Parameters
    ----------
    state
        The current :class:`~orka.operations.state.SurgeryState`.

    Returns
    -------
    dict
        Updated state keys: ``draft_file_content``, ``validation_output``,
        ``is_valid``, (``original_file_backup`` on first write).
    """
    snippet = state.get("draft_snippet", "")
    target_file = state["target_output_file"]
    node_id = state["target_node_id"]
    operation_type = state["prompt_template_name"]  # "refactor" or "test"
    dry_run = state.get("dry_run", False)
    source_file = state["source_file"]

    if not snippet:
        return {
            "is_valid": False,
            "validation_output": "No draft snippet to validate.",
        }

    # ── Gate 1: Snippet AST validation ────────────────────────────────
    snippet_result = validate_code_snippet(snippet, label=node_id)

    if not snippet_result:
        logger.warning("Gate 1 (snippet AST) failed: %s", snippet_result.error)
        return {
            "is_valid": False,
            "validation_output": f"Syntax error in generated code:\n{snippet_result.error}",
        }

    logger.debug("Gate 1 (snippet AST) PASSED for %s", node_id)

    # ── Gate 2: Assembly ──────────────────────────────────────────────
    try:
        draft_file_content = _assemble_file(
            operation_type=operation_type,
            snippet=snippet,
            source_file=source_file,
            target_file=target_file,
            target_node_id=node_id,
            class_name=state.get("class_name"),
            method_name=state.get("method_name"),
        )
    except Exception as e:
        logger.error("Gate 2 (assembly) failed: %s", e)
        return {
            "is_valid": False,
            "validation_output": f"Failed to assemble file: {e}",
        }

    # ── Gate 3: File AST validation ───────────────────────────────────
    try:
        import ast

        ast.parse(draft_file_content)
    except SyntaxError as e:
        logger.warning("Gate 3 (file AST) failed: %s", e)
        return {
            "draft_file_content": draft_file_content,
            "is_valid": False,
            "validation_output": f"Syntax error in assembled file:\n{e.msg} (line {e.lineno})",
        }

    logger.debug("Gate 3 (file AST) PASSED for %s", node_id)

    # ── If dry-run, stop here (no disk write, no pytest) ──────────────
    if dry_run:
        return {
            "draft_file_content": draft_file_content,
            "is_valid": True,
            "validation_output": "Dry-run mode — validation skipped after AST pass.",
        }

    # ── Gate 4: Disk write + Pytest ───────────────────────────────────
    result = _write_and_validate(state, draft_file_content, target_file)
    return result


# ═══════════════════════════════════════════════════════════════════════
# Assembly helpers
# ═══════════════════════════════════════════════════════════════════════


def _assemble_file(
    operation_type: str,
    snippet: str,
    source_file: str,
    target_file: str,
    target_node_id: str,
    class_name: Optional[str],
    method_name: Optional[str],
) -> str:
    """Assemble the full file content from the snippet.

    For ``"refactor"``: LibCST-patch the snippet into the source file.
    For ``"testgen"``: Build a complete test file with imports.
    """
    if operation_type == "refactor":
        return _assemble_refactor_file(
            source_file=source_file,
            target_file=target_file,
            snippet=snippet,
            target_node_id=target_node_id,
            class_name=class_name,
            method_name=method_name,
        )
    elif operation_type == "test":
        return _assemble_test_file(
            snippet=snippet,
            source_file=source_file,
            class_name=class_name,
            method_name=method_name,
        )
    else:
        raise ValueError(f"Unknown operation type: {operation_type}")


def _assemble_refactor_file(
    source_file: str,
    target_file: str,
    snippet: str,
    target_node_id: str,
    class_name: Optional[str],
    method_name: Optional[str],
) -> str:
    """Use LibCST to patch the snippet into the source file.

    Returns the full patched file content as a string.
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
    return patched


def _assemble_test_file(
    snippet: str,
    source_file: str,
    class_name: Optional[str],
    method_name: Optional[str],
) -> str:
    """Build a complete test file with imports prepended.

    Uses the deterministic ``resolve_import`` (no LLM involved).

    Before assembling the final file, the snippet is passed through
    ``SnippetImportExtractor`` (CST-based) to strip any import statements
    that the LLM may have erroneously included.  This prevents duplicate
    imports in the output.
    """
    import_stmt = resolve_import(
        file_path=source_file,
        class_name=class_name,
        method_name=method_name,
        workspace_dir=str(settings.PROJECT_ROOT),
        graph_db=None,
    )

    if import_stmt is None:
        # Fallback — try with graph DB
        try:
            from orka.core.ingester import OrkaGraphDB

            cache_file = os.path.join(str(settings.PROJECT_ROOT), ".orka_cache.json")
            if os.path.exists(cache_file):
                graph_db = OrkaGraphDB(cache_file=cache_file)
                import_stmt = resolve_import(
                    file_path=source_file,
                    class_name=class_name,
                    method_name=method_name,
                    workspace_dir=str(settings.PROJECT_ROOT),
                    graph_db=graph_db,
                )
        except Exception:
            pass

    if import_stmt is None:
        raise RuntimeError(
            f"Could not resolve import for {method_name} in {source_file}."
        )

    # NOTE: no need to put `import pytest` at the top of the file anymore. It's outdated convention

    # ── Strip import statements from LLM snippet ──────────────────────
    # The LLM sometimes emits import statements inside the snippet despite
    # being instructed not to.  Use SnippetImportExtractor (CST-based) to
    # remove them cleanly — handles edge cases like ``import os; x = 1``.
    try:
        tree = cst.parse_module(snippet)
        extractor = SnippetImportExtractor()
        clean_tree = tree.visit(extractor)
        clean_snippet = clean_tree.code
    except Exception:
        clean_snippet = snippet  # fallback — keep snippet as-is

    result = f"{import_stmt}{clean_snippet}\n"
    return result


# ═══════════════════════════════════════════════════════════════════════
# Disk write + Pytest
# ═══════════════════════════════════════════════════════════════════════


def _write_and_validate(
    state: dict[str, Any],
    draft_file_content: str,
    target_file: str,
) -> dict[str, Any]:
    """Write the assembled file to disk and run pytest.

    If the file doesn't exist yet (new test file), capture that it didn't
    exist so we can clean it up on rollback.
    """
    # ── Write to disk ─────────────────────────────────────────────────
    try:
        os.makedirs(os.path.dirname(target_file), exist_ok=True)
        with open(target_file, "w", encoding="utf-8") as f:
            f.write(draft_file_content)
    except OSError as e:
        return {
            "draft_file_content": draft_file_content,
            "is_valid": False,
            "validation_output": f"Failed to write {target_file}: {e}",
        }

    # ── Run pytest ────────────────────────────────────────────────────
    pytest_passed, pytest_output = _run_pytest(state, target_file)

    if pytest_passed:
        logger.info("All validations PASSED for %s", state["target_node_id"])
        return {
            "draft_file_content": draft_file_content,
            "is_valid": True,
            "validation_output": "",
        }

    # Tests failed — truncate and return
    error_summary = extract_error_summary(pytest_output)
    error_summary = truncate_error_summary(error_summary)

    logger.warning(
        "Tests FAILED for %s (iteration %d/%d)",
        state["target_node_id"],
        state["iteration_count"],
        state["max_iterations"],
    )

    return {
        "draft_file_content": draft_file_content,
        "is_valid": False,
        "validation_output": error_summary,
    }


def _run_pytest(state: dict[str, Any], target_file: str) -> tuple[bool, str]:
    """Run pytest against the target file (or its associated test file).

    Returns
    -------
    tuple[bool, str]
        ``(passed, output)`` where ``output`` is the full stdout+stderr.
    """
    test_target = state.get("test_file_target") or target_file

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


# ═══════════════════════════════════════════════════════════════════════
# Rollback
# ═══════════════════════════════════════════════════════════════════════


def rollback_file(target_file: str, backup_content: Optional[str]) -> None:
    """Revert the target file to its original content.

    Called when the pipeline exhausts max iterations without producing
    valid code.

    Parameters
    ----------
    target_file
        Path to the file to revert.
    backup_content
        The original content. If ``None``, the file is deleted (it didn't
        exist before the pipeline started).
    """
    if backup_content is not None:
        try:
            with open(target_file, "w", encoding="utf-8") as f:
                f.write(backup_content)
            logger.info("Rolled back %s from backup", target_file)
        except OSError as e:
            logger.error("Failed to rollback %s: %s", target_file, e)
    else:
        # File didn't exist before — delete it
        try:
            if os.path.exists(target_file):
                os.remove(target_file)
                logger.info("Deleted %s (rollback — file didn't exist before)", target_file)
        except OSError as e:
            logger.error("Failed to delete %s during rollback: %s", target_file, e)
