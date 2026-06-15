"""
Fixer node — the second (and only other) LLM-invoking node.

Takes the current failing draft + validation output and asks the LLM to
fix the code. Uses the same prompt compiler pattern as the generator but
with a specialised fixer prompt.
"""

from __future__ import annotations

import logging
from typing import Any

from orka.clients import OrkaLangChainClient
from orka.config import settings
from orka.core.snippet_utils import sanitize_llm_output
from orka.core.validator import validate_code_snippet
from orka.operations.helpers import build_fixer_prompt

logger = logging.getLogger(__name__)


def execute(state: dict[str, Any]) -> dict[str, Any]:
    """Fix the current draft based on validation errors.

    Steps
    -----
    1. Build a fixer prompt containing the original context, the failing
       draft, and the validation error.
    2. Invoke the LLM with a structured fix instruction.
    3. Clean markdown fences and validate the fixed snippet.
    4. Increment ``iteration_count``.

    Parameters
    ----------
    state
        The current :class:`~orka.operations.state.SurgeryState`.

    Returns
    -------
    dict
        Updated state keys: ``draft_snippet``, ``iteration_count``,
        ``fatal_error`` (if LLM fails).
    """
    snippet = state.get("draft_snippet", "")
    validation_output = state.get("validation_output", "")
    existing_code = state.get("existing_code", "")
    class_context = state.get("class_context", "")
    requirements = state.get("requirements", "")
    operation_type = state["prompt_template_name"]
    provider = state.get("provider") or settings.DEFAULT_PROVIDER

    logger.info(
        "Fixing %s draft for %s (iteration %d/%d)",
        operation_type,
        state["target_node_id"],
        state["iteration_count"],
        state["max_iterations"],
    )

    # ── 1. Build fixer prompt ─────────────────────────────────────────
    fixer_prompt, system_instruction = build_fixer_prompt(
        operation_type=operation_type,
        draft_snippet=snippet,
        validation_output=validation_output,
        existing_code=existing_code,
        class_context=class_context,
        requirements=requirements,
    )

    # ── 2. Invoke LLM ─────────────────────────────────────────────────
    llm_client = OrkaLangChainClient(provider=provider)
    try:
        raw_output = llm_client.generate_code(
            prompt=fixer_prompt,
            system_instruction=system_instruction,
        )
        fixed_snippet = sanitize_llm_output(raw_output)
    except Exception as e:
        logger.error("LLM fixer call failed: %s", e)
        return {
            "draft_snippet": snippet,
            "iteration_count": state["iteration_count"] + 1,
            "fatal_error": f"LLM fixer failed: {e}",
        }

    # ── 3. Validate the fixed snippet ─────────────────────────────────
    result = validate_code_snippet(fixed_snippet, label=state["target_node_id"])
    if not result:
        logger.warning(
            "Fixer produced invalid code (iteration %d): %s",
            state["iteration_count"],
            result.error,
        )

    return {
        "draft_snippet": fixed_snippet,
        "iteration_count": state["iteration_count"] + 1,
    }
