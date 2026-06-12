"""
Draft generator node — compiles a prompt using the PromptCompiler engine
and invokes the LLM with structured output.

This is Node 2 of the surgery graph and one of only two LLM-invoking nodes.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from orka.clients import OrkaLangChainClient
from orka.config import settings
from orka.core.compiler import PromptCompiler
from orka.core.rule_resolver import resolve_rules
from orka.operations.helpers import load_template

logger = logging.getLogger(__name__)


def execute(state: dict[str, Any]) -> dict[str, Any]:
    """Generate a draft snippet using the PromptCompiler + LLM.

    Steps
    -----
    1. Load the appropriate YAML template (``"refactor"`` or ``"test"``).
    2. Resolve injection rules for the template.
    3. Compile the prompt with gathered context data.
    4. Invoke the LLM with a structured output schema.
    5. Clean markdown fences from the response.

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
    template_name = state["prompt_template_name"]
    source_file = state["source_file"]
    method_name = state["method_name"]
    class_name = state.get("class_name")
    existing_code = state["existing_code"]
    class_context = state.get("class_context", "")
    requirements = state.get("requirements", "")
    similar_examples = state.get("similar_examples", [])
    provider = state.get("provider") or settings.DEFAULT_PROVIDER

    logger.info(
        "Generating %s draft for %s (iteration %d)",
        template_name,
        state["target_node_id"],
        state["iteration_count"],
    )

    # ── 1. Load template ──────────────────────────────────────────────
    template = load_template(template_name)

    # ── 2. Resolve rules ──────────────────────────────────────────────
    resolved_rules = resolve_rules(
        template_name=template.name,
        injection_points=template.injection_points,
    )

    # ── 3. Build context data ─────────────────────────────────────────
    # Use a relative path for the prompt to avoid leaking local filesystem structure
    prompt_file_path = source_file
    workspace_dir = str(settings.PROJECT_ROOT)
    if workspace_dir and source_file.startswith(workspace_dir):
        prompt_file_path = os.path.relpath(source_file, workspace_dir)

    # Escape curly braces in code so Jinja2 doesn't interpret them as
    # template variables (e.g. f"{head}" in existing_code would be consumed).
    escaped_code = existing_code.replace("{", "{{").replace("}", "}}")
    escaped_class = class_context.replace("{", "{{").replace("}", "}}")

    context_data = {
        "existing_code": escaped_code,
        "class_context": escaped_class,
        "business_requirements": requirements,
        "graph_constraints": "",
        "file_path": prompt_file_path,
    }

    # Append similar examples as extra context (appended at the end)
    compiler = PromptCompiler()
    compiled_prompt = compiler.compile(template, resolved_rules, context_data)

    if similar_examples:
        compiled_prompt += "\n\n### SIMILAR EXISTING TESTS (for reference):\n"
        compiled_prompt += "\n---\n".join(similar_examples[:3])

    # ── 4. Build system instruction based on operation type ────────────
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

    # ── 5. Invoke LLM ─────────────────────────────────────────────────
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

    # Validate snippet (non-fatal — the fix loop can repair it)
    from orka.core.validator import validate_code_snippet

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
