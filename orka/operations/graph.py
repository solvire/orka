"""
LangGraph state machine for the Orka surgery pipeline.

This is **not** a ReAct agent. There is no ``ToolNode``, no unbounded
``messages`` array, and no tool-calling LLM. The control flow is a
deterministic state machine with exactly two LLM-invoking nodes
(generator and fixer).

Pipeline
--------
::

    START -> gather_context -> generate_draft -> validate_draft
    -> (condition) if is_valid -> END
    -> if iteration_count >= max_iterations -> END (with rollback)
    -> else -> fix_draft -> validate_draft (loop)
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from langgraph.graph import END, StateGraph

from orka.operations.controllers import context, generator, validator, fixer
from orka.operations.state import SurgeryState

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────

_DEFAULT_MAX_ITERATIONS = 3


# ═══════════════════════════════════════════════════════════════════════
# Graph builder
# ═══════════════════════════════════════════════════════════════════════


def build_surgery_graph() -> StateGraph:
    """Build and compile the surgery StateGraph pipeline.

    Returns
    -------
    StateGraph
        A compiled LangGraph ``StateGraph`` ready for ``.invoke()``.
    """
    workflow = StateGraph(SurgeryState)

    # ── Add nodes ─────────────────────────────────────────────────────
    workflow.add_node("gather_context", context.execute)
    workflow.add_node("generate_draft", generator.execute)
    workflow.add_node("validate_draft", validator.execute)
    workflow.add_node("fix_draft", fixer.execute)

    # ── Edges ─────────────────────────────────────────────────────────
    workflow.set_entry_point("gather_context")
    workflow.add_edge("gather_context", "generate_draft")
    workflow.add_edge("generate_draft", "validate_draft")

    # Conditional routing after validation
    workflow.add_conditional_edges(
        "validate_draft",
        _router,
        {"end": "end", "fix_draft": "fix_draft"},
    )

    workflow.add_edge("fix_draft", "validate_draft")

    # Terminal node (handles rollback if needed)
    workflow.add_node("end", _terminal_node)
    workflow.add_edge("end", END)

    graph = workflow.compile()
    logger.debug("Surgery graph compiled successfully")
    return graph


# ═══════════════════════════════════════════════════════════════════════
# Router
# ═══════════════════════════════════════════════════════════════════════


def _router(state: SurgeryState) -> str:
    """Determine the next node after ``validate_draft``.

    Returns
    -------
    str
        ``"end"`` if validation passed or max iterations reached.
        ``"fix_draft"`` if validation failed and we can retry.
    """
    if state.get("is_valid", False):
        logger.info(
            "Validation PASSED for %s — ending pipeline.",
            state.get("target_node_id", "unknown"),
        )
        return "end"

    if state.get("fatal_error"):
        logger.error(
            "Fatal error in pipeline for %s: %s",
            state.get("target_node_id", "unknown"),
            state["fatal_error"],
        )
        return "end"

    if state["iteration_count"] >= state["max_iterations"]:
        logger.warning(
            "Max iterations (%d) reached for %s — ending with rollback.",
            state["max_iterations"],
            state.get("target_node_id", "unknown"),
        )
        return "end"

    logger.info(
        "Validation FAILED for %s — running fix_draft (iteration %d/%d)",
        state.get("target_node_id", "unknown"),
        state["iteration_count"],
        state["max_iterations"],
    )
    return "fix_draft"


# ═══════════════════════════════════════════════════════════════════════
# Terminal node (handles rollback)
# ═══════════════════════════════════════════════════════════════════════


def _terminal_node(state: SurgeryState) -> dict[str, Any]:
    """Terminal node — performs rollback if the pipeline failed.

    If ``is_valid`` is ``False`` and the file was modified, this node
    reverts the target file to its original backup.
    """
    if not state.get("is_valid", False) and not state.get("dry_run", False):
        backup = state.get("original_file_backup")
        target = state.get("target_output_file")
        if target:
            validator.rollback_file(target, backup)
            logger.info(
                "Pipeline failed for %s — rolled back %s",
                state.get("target_node_id", "unknown"),
                target,
            )

    return {}


# ═══════════════════════════════════════════════════════════════════════
# Convenience runner
# ═══════════════════════════════════════════════════════════════════════


def run_surgery(
    source_file: str,
    method_name: str,
    requirements: str,
    prompt_template_name: str = "refactor",
    class_name: Optional[str] = None,
    target_output_file: Optional[str] = None,
    test_file_target: Optional[str] = None,
    max_iterations: int = _DEFAULT_MAX_ITERATIONS,
    dry_run: bool = False,
    provider: Optional[str] = None,
) -> dict[str, Any]:
    """Convenience wrapper to build and invoke the surgery pipeline.

    Parameters
    ----------
    source_file
        Path to the source file containing the method/function to operate on.
    method_name
        Name of the method or function to refactor or generate tests for.
    requirements
        Business requirements or description of what to generate.
    prompt_template_name
        Which template to use (``"refactor"`` or ``"test"``).
    class_name
        Class name containing the method (``None`` for standalone functions).
    target_output_file
        Where to write the output. Defaults to ``source_file`` for refactoring.
    test_file_target
        If set, pytest runs against this file instead of ``target_output_file``.
    max_iterations
        Maximum number of fix-attempt iterations (default 3).
    dry_run
        If ``True``, compute diffs but never write to disk or run pytest.
    provider
        LLM provider to use. Defaults to the project-wide default.

    Returns
    -------
    dict
        The final :class:`SurgeryState` with results.
    """
    from orka.config import settings

    actual_provider = provider or settings.DEFAULT_PROVIDER
    actual_target = target_output_file or source_file

    target_node_id = f"{class_name}.{method_name}" if class_name else method_name

    initial_state: SurgeryState = {
        # Inputs
        "source_file": source_file,
        "target_output_file": actual_target,
        "prompt_template_name": prompt_template_name,
        "requirements": requirements,
        "target_node_id": target_node_id,
        "dry_run": dry_run,
        "max_iterations": max_iterations,
        "provider": actual_provider,
        "class_name": class_name,
        "method_name": method_name,
        # Gathered context
        "existing_code": "",
        "class_context": "",
        "similar_examples": [],
        "original_file_backup": None,
        # Draft code
        "draft_snippet": "",
        "draft_file_content": "",
        # Validation
        "validation_output": "",
        "is_valid": False,
        "original_draft_code": "",
        "test_file_target": test_file_target,
        # Loop control
        "iteration_count": 0,
        "fatal_error": None,
    }

    graph = build_surgery_graph()
    result = graph.invoke(initial_state)
    return result
