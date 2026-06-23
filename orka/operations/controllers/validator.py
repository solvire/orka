"""
Validation node — thin wrapper around orka.core.validator.validate_four_gates.

The heavy lifting of the 4-gate pipeline (snippet AST -> assembly -> file AST
-> pytest) now lives in :func:`orka.core.validator.validate_four_gates`.  This
controller only translates the bounded :class:`~orka.operations.state.SurgeryState`
dict to/from that flat API, and retains :func:`rollback_file` which is invoked
by the terminal node in :mod:`orka.operations.graph`.

This is Node 3 of the surgery graph.  It is pure Python (no LLM call).
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from orka.core.validator import validate_four_gates
from orka.operations.graph_helpers import get_graph_db

logger = logging.getLogger(__name__)


def execute(state: dict[str, Any]) -> dict[str, Any]:
    """Validate the current draft through all four gates.

    Thin wrapper around :func:`orka.core.validator.validate_four_gates`: maps
    the :class:`~orka.operations.state.SurgeryState` dict onto the flat
    function signature and translates the returned ``(passed, output,
    assembled)`` tuple back into state keys.

    Parameters
    ----------
    state
        The current :class:`~orka.operations.state.SurgeryState`.

    Returns
    -------
    dict
        Updated state keys: ``is_valid``, ``validation_output``, and
        ``draft_file_content`` (set whenever assembly produced content).
    """
    snippet = state.get("draft_snippet", "")
    if not snippet:
        return {
            "is_valid": False,
            "validation_output": "No draft snippet to validate.",
            "previous_validation_output": state.get("validation_output", ""),
        }

    passed, output, assembled = validate_four_gates(
        snippet=snippet,
        source_file=state["source_file"],
        target_file=state["target_output_file"],
        target_node_id=state["target_node_id"],
        operation_type=state["prompt_template_name"],
        class_name=state.get("class_name"),
        method_name=state.get("method_name"),
        graph_db=get_graph_db(),
        dry_run=state.get("dry_run", False),
        test_file_target=state.get("test_file_target"),
    )

    result: dict[str, Any] = {
        "is_valid": passed,
        "validation_output": output,
        "previous_validation_output": state.get("validation_output", ""),
    }
    if assembled is not None:
        result["draft_file_content"] = assembled
    return result


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
