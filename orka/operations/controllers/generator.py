"""
Draft generator node — invokes the LLM with a pre-compiled prompt.

This is Node 3 of the surgery graph and one of only two LLM-invoking nodes.
The prompt is pre-compiled by the ``compile_prompt`` node and stored in
``state["compiled_prompt"]``.
"""

from __future__ import annotations

import logging
from typing import Any

from orka.clients import OrkaLangChainClient
from orka.config import settings
from orka.core.validator import validate_code_snippet

logger = logging.getLogger(__name__)


def execute(state: dict[str, Any]) -> dict[str, Any]:
    """Generate a draft snippet by sending the compiled prompt to the LLM.

    The prompt is pre-compiled by the ``compile_prompt`` node. This node
    only invokes the LLM and cleans the response.

    Parameters
    ----------
    state
        The current :class:`~orka.operations.state.SurgeryState`.

    Returns
    -------
    dict
        Updated state keys: ``draft_snippet``, ``original_draft_code``,
        ``iteration_count``.
    """
    compiled_prompt = state.get("compiled_prompt", "")
    template_name = state["prompt_template_name"]
    provider = state.get("provider") or settings.DEFAULT_PROVIDER

    logger.info(
        "Generating %s draft for %s (iteration %d, prompt=%d chars)",
        template_name,
        state["target_node_id"],
        state["iteration_count"],
        len(compiled_prompt),
    )

    if not compiled_prompt:
        logger.error("No compiled_prompt in state — cannot generate draft.")
        return {
            "draft_snippet": "",
            "original_draft_code": "",
            "fatal_error": "No compiled_prompt in state — compile_prompt node may not have run.",
        }

    # ── 1. Build system instruction based on operation type ────────────
    if template_name == "test":
        system_instruction = (
            "You are a pytest specialist. You will receive a compiled prompt "
            "from the template engine below. "
            "Output ONLY a single raw Python test function. "
            "The function MUST be a valid pytest test function starting with "
            "\"def test_\" and accepting (tmp_path) as its only parameter — "
            "no monkeypatch, no mocker, no other fixtures. "
            "ALL code must be INSIDE the function body. "
            "No module-level statements, no monkeypatch.setattr(), no imports. "
            "No docstrings, no module docstrings, no markdown fences."
        )
    else:
        system_instruction = (
            "You are a pure code synthesis engine. Output ONLY raw Python code "
            "at the base indentation level. Do not include signatures, decorators, "
            "or explanations."
        )

    # ── 2. Invoke LLM ─────────────────────────────────────────────────
    llm_client = OrkaLangChainClient(provider=provider)
    try:
        raw_output = llm_client.generate_code(
            prompt=compiled_prompt,
            system_instruction=system_instruction,
        )
        draft_snippet = OrkaLangChainClient.fix_md_fences(raw_output)
    except Exception as e:
        logger.error("LLM generation failed: %s", e)
        return {
            "draft_snippet": "",
            "original_draft_code": "",
            "fatal_error": f"LLM generation failed: {e}",
        }

    # ── 3. Validate snippet (non-fatal — fix loop can repair it) ───────
    result = validate_code_snippet(draft_snippet, label=state["target_node_id"])
    if not result:
        logger.warning(
            "LLM generated invalid code (iteration %d): %s",
            state["iteration_count"],
            result.error,
        )

    return {
        "draft_snippet": draft_snippet,
        "original_draft_code": draft_snippet,
        "iteration_count": state["iteration_count"] + 1,
    }
