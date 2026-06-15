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
from orka.core.snippet_utils import sanitize_llm_output
from orka.core.validator import validate_code_snippet

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# Sanitization pipeline — multi-pass cleaning of untrusted LLM output
# ═══════════════════════════════════════════════════════════════════════



def _sanitize_llm_output(raw: str) -> str:
    """Multi-pass sanitization of untrusted LLM output.

    Delegates to :func:`orka.core.snippet_utils.sanitize_llm_output`.

    Parameters
    ----------
    raw
        The raw string returned by the LLM.

    Returns
    -------
    str
        Cleaned Python code. May be empty if nothing recoverable was found.
    """
    return sanitize_llm_output(raw)


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
    compiled_prompt_sections = state.get("compiled_prompt_sections", {})
    system_instruction = compiled_prompt_sections.get("system", "")
    user_prompt = compiled_prompt_sections.get("user", state.get("compiled_prompt", ""))

    template_name = state["prompt_template_name"]
    provider = state.get("provider") or settings.DEFAULT_PROVIDER

    logger.info(
        "Generating %s draft for %s (iteration %d, prompt=%d chars)",
        template_name,
        state["target_node_id"],
        state["iteration_count"],
        len(user_prompt),
    )

    if not user_prompt:
        logger.error("No compiled_prompt in state — cannot generate draft.")
        return {
            "draft_snippet": "",
            "original_draft_code": "",
            "fatal_error": "No compiled_prompt in state — compile_prompt node may not have run.",
        }

    # ── 2. Invoke LLM ─────────────────────────────────────────────────
    llm_client = OrkaLangChainClient(provider=provider)
    try:
        raw_output = llm_client.generate_code(
            prompt=user_prompt,
            system_instruction=system_instruction,
        )
    except Exception as e:
        logger.error("LLM generation failed: %s", e)
        return {
            "draft_snippet": "",
            "original_draft_code": state.get("original_draft_code", ""),
            "fatal_error": f"LLM generation failed: {e}",
        }

    # ── 3. Multi-pass sanitization pipeline ───────────────────────────
    draft_snippet = _sanitize_llm_output(raw_output)

    if not draft_snippet:
        logger.error(
            "Sanitization produced empty output (raw was %d chars)",
            len(raw_output),
        )
        return {
            "draft_snippet": "",
            "original_draft_code": state.get("original_draft_code", ""),
            "fatal_error": "LLM output was empty after sanitization.",
        }

    logger.debug(
        "Sanitization: raw=%d chars -> clean=%d chars (delta=%d)",
        len(raw_output), len(draft_snippet),
        len(raw_output) - len(draft_snippet),
    )

    # ── 4. Validate snippet (non-fatal — fix loop can repair it) ───────
    result = validate_code_snippet(draft_snippet, label=state["target_node_id"])
    if not result:
        logger.warning(
            "LLM generated invalid code (iteration %d): %s",
            state["iteration_count"],
            result.error,
        )

    # ── 5. Intent preservation — only set original_draft_code once ────
    existing_original = state.get("original_draft_code", "")
    original_draft_code = existing_original if existing_original else draft_snippet

    return {
        "draft_snippet": draft_snippet,
        "original_draft_code": original_draft_code,
        "iteration_count": state["iteration_count"] + 1,
    }



