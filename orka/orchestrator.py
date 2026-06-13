"""Orchestrator — The central controller for Orka's refactoring pipeline.

This module is now a thin wrapper around the LangGraph surgery graph.
All refactoring and test generation logic lives in ``orka.operations.graph``.
"""

import difflib
import logging
from dataclasses import dataclass
from typing import Optional

from orka.operations.graph import run_surgery

logger = logging.getLogger("Orchestrator")


def _target_label(class_name: Optional[str], method_name: str) -> str:
    """Build a human-readable label like 'MyClass.my_method' or 'my_function'."""
    if class_name:
        return f"{class_name}.{method_name}"
    return method_name


@dataclass
class RefactorResult:
    """Structured result from a refactoring operation.

    Attributes:
        success: Whether the refactoring succeeded.
        label: Human-readable name of the refactored target.
        file_path: Absolute path to the modified file.
        diff: Unified diff string showing what changed (empty on failure).
        dry_run: Whether this was a dry run (file not modified).
        error: Human-readable error description (``None`` on success).
    """
    success: bool
    label: str
    file_path: str
    diff: str = ""
    dry_run: bool = False
    error: Optional[str] = None
    tests_content: str = ""


class Orchestrator:
    """Thin wrapper around the surgery graph pipeline.

    Kept for backward compatibility with existing tests. New code should
    call ``orka.operations.graph.run_surgery()`` directly.
    """

    def __init__(self, workspace_dir: str, provider: str = "together_ai"):
        self.workspace_dir = workspace_dir
        self.provider = provider
        logger.info("Orchestrator initialised (delegating to surgery graph)")

    def refactor_method(
        self,
        file_path: str,
        method_name: str,
        requirements: str,
        class_name: Optional[str] = None,
        dry_run: bool = False,
    ) -> RefactorResult:
        """Delegate to the surgery graph's ``run_surgery`` pipeline.

        Returns a ``RefactorResult`` for backward compatibility.
        """
        label = _target_label(class_name, method_name)
        logger.info("Orchestrator delegating refactor of %s to surgery graph", label)

        # Capture file content before changes for diff
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                before = f.read()
        except OSError as e:
            return RefactorResult(False, label, file_path, error=str(e))

        result = run_surgery(
            source_file=file_path,
            method_name=method_name,
            requirements=requirements,
            prompt_template_name="refactor",
            class_name=class_name,
            dry_run=dry_run,
            provider=self.provider,
                    )

        if result.get("fatal_error"):
            return RefactorResult(
                False, label, file_path,
                error=result["fatal_error"],
                dry_run=dry_run,
            )

        if result.get("is_valid", False):
            # Read the patched file
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    after = f.read()
            except OSError:
                after = before

            diff = _compute_diff(before, after, file_path)
            return RefactorResult(
                True, label, file_path,
                diff=diff,
                dry_run=dry_run,
            )

        error = result.get("validation_output", "Unknown error")
        return RefactorResult(
            False, label, file_path,
            error=error,
            dry_run=dry_run,
        )

    def generate_tests(
        self,
        file_path: str,
        method_name: str,
        class_name: Optional[str] = None,
        output_path: Optional[str] = None,
        dry_run: bool = False,
        run_pytest: bool = False,
    ) -> RefactorResult:
        """Delegate to the surgery graph's ``run_surgery`` pipeline.

        Returns a ``RefactorResult`` for backward compatibility.
        """
        label = _target_label(class_name, method_name)
        logger.info("Orchestrator delegating testgen of %s to surgery graph", label)

        target_output = output_path if output_path else None

        result = run_surgery(
            source_file=file_path,
            method_name=method_name,
            requirements=f"Generate a pytest test function for {label}.",
            prompt_template_name="test",
            class_name=class_name,
            target_output_file=target_output,
            test_file_target=output_path if run_pytest else None,
            dry_run=dry_run,
            provider=self.provider,
        )

        if result.get("fatal_error"):
            return RefactorResult(
                False, label, file_path,
                error=result["fatal_error"],
                dry_run=dry_run,
            )

        if result.get("is_valid", False):
            tests_content = result.get("draft_file_content", "")
            return RefactorResult(
                True, label,
                result.get("target_output_file", file_path),
                tests_content=tests_content,
                dry_run=dry_run,
            )

        error = result.get("validation_output", "Unknown error")
        return RefactorResult(
            False, label, file_path,
            error=error,
            dry_run=dry_run,
        )


def _compute_diff(before: str, after: str, file_path: str = "") -> str:
    """Return a unified diff string between *before* and *after*."""
    lines = list(difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=file_path,
        tofile=file_path,
    ))
    return "".join(lines)