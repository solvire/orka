"""
Surgery state schema — bounded fields, no unbounded ``messages`` list.

Every field is explicitly sized to prevent context-window explosion.
There is no generic ``messages`` key — LLM calls are scoped to specific
generation/fixer nodes with carefully constructed prompts.
"""

from __future__ import annotations

from typing import Optional

from typing_extensions import TypedDict


class SurgeryState(TypedDict):
    """Bounded state for the LangGraph surgery pipeline.

    The graph handles two operation types:
    - ``"refactor"`` — Replace a method body via LibCST patching
    - ``"testgen"`` — Generate pytest tests for a method/function

    Notes
    -----
    - ``original_file_backup`` is an in-memory string of the target file
      *before* any writes. If the pipeline fails (max iterations exhausted
      without valid code), the file is reverted to this backup.
    - ``test_file_target`` is optional. If provided, pytest is run against
      this file instead of ``target_output_file`` (e.g., when refactoring
      a source method and we want to validate against its existing tests).
    """

    # ── Inputs (set by caller, immutable during execution) ──────────────
    source_file: str
    """Path to the source file containing the method/function to operate on."""

    target_output_file: str
    """Path where the output is written (the file being modified or created)."""

    prompt_template_name: str
    """Which template to use: ``"refactor"``, ``"test"``."""

    requirements: str
    """Business requirements or description of what to generate."""

    target_node_id: str
    """e.g. ``"MyClass.my_method"`` or just ``"my_function"`` — used by LibCST patcher."""

    dry_run: bool
    """If True, compute diffs but never write to disk or run pytest."""

    max_iterations: int
    """Maximum fix-attempt loops before giving up and rolling back."""

    provider: str
    """LLM provider name (e.g. ``"together_ai"``, ``"deepseek"``)."""

    class_name: Optional[str]
    """Class name containing the method (None for standalone functions)."""

    method_name: str
    """Method or function name to operate on."""

    # ── Gathered context (set by node_gather_context) ──────────────────
    existing_code: str
    """Extracted source code of the target method/function."""

    class_context: str
    """Full class source (empty string for standalone functions)."""

    similar_examples: list[str]
    """Up to 3 semantically similar tests or code examples from ChromaDB."""

    dependency_signatures: str
    """Formatted signatures & docstrings of the target's 1st-degree
    outbound dependencies (functions/classes called or instantiated
    by the target). Empty string if no dependencies found in the
    graph DB. Populated by ``gather_context``."""

    data_construction_guide: str
    """Fast LLM-generated guide explaining how to construct valid inputs
    for the target function. Empty string if generation failed or was
    skipped. Populated by ``gather_context``."""

    original_file_backup: Optional[str]
    """Backup of target_output_file before any writes. None if file didn't exist."""

    # ── Draft code (set by generate / fix nodes) ───────────────────────
    draft_snippet: str
    """Raw LLM output — a method body (refactor) or test functions (testgen)."""

    draft_file_content: str
    """The full file content after LibCST patching or import assembly."""

    # ── Validation (set by node_validate_draft) ────────────────────────
    validation_output: str
    """Truncated error output from AST parse or pytest."""

    is_valid: bool
    """True when both AST gates pass and (if not dry-run) pytest passes."""

    original_draft_code: str
    """Copy of the first draft, saved so the fixer can understand the original intent."""

    test_file_target: Optional[str]
    """If set, pytest runs against this file instead of target_output_file."""

    # ── Compiled prompt (set by compile_prompt node) ───────────────────
    compiled_prompt: str
    """The fully compiled prompt string, ready for the LLM."""

    compiled_prompt_sections: dict
    """Structured breakdown of the compiled prompt (template name, rules,
    signature analysis, graph summary). Useful for introspection and
    the ``prompt`` CLI command."""

    # ── Loop control ───────────────────────────────────────────────────
    iteration_count: int
    """How many fix attempts have been made (0-indexed)."""

    fatal_error: Optional[str]
    """If set, the pipeline aborts immediately with this error message."""

