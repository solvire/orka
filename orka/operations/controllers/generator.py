"""
Draft generator node — invokes the LLM with a pre-compiled prompt.

This is Node 3 of the surgery graph and one of only two LLM-invoking nodes.
The prompt is pre-compiled by the ``compile_prompt`` node and stored in
``state["compiled_prompt"]``.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from orka.clients import OrkaLangChainClient
from orka.config import settings
from orka.core.validator import validate_code_snippet

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# Sanitization pipeline — multi-pass cleaning of untrusted LLM output
# ═══════════════════════════════════════════════════════════════════════

_PREAMBLE_PATTERNS = [
    re.compile(
        r"^(?:here(?:'s| is)\s+(?:the\s+)?(?:code|implementation|solution|"
        r"refactored\s+(?:code|body|method|function))[:\.\-]?\s*\n)",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:sure[,\s!]+(?:here(?:'s| is)\s+)?(?:the\s+)?(?:code|"
        r"implementation)[:\.\-]?\s*\n)",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:certainly[,\s!]+(?:here(?:'s| is)\s+)?(?:the\s+)?(?:code)[:\.\-]?\s*\n)",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:below\s+is\s+(?:the\s+)?(?:code|implementation|refactored\s+code)[:\.\-]?\s*\n)",
        re.IGNORECASE,
    ),
]

_CODE_LINE_STARTERS = (
    "def ", "class ", "if ", "for ", "while ", "return ", "raise ",
    "import ", "from ", "try:", "try :", "except", "with ", "elif",
    "else:", "yield ", "assert ", "pass", "break", "continue",
    "#", "@", '"""', "'''", ")", "]", "}", "print(", "self.",
    "logger.", "result", "return", "    ", "\t",
)


def _sanitize_llm_output(raw: str) -> str:
    """Multi-pass sanitization of untrusted LLM output.

    LLMs violate "RAW PYTHON ONLY" instructions in predictable ways.
    This function handles each failure mode in sequence:

    1. **Fence extraction** — Remove ```python ... ``` wrappers.
       Handles single blocks, multiple blocks (keeps largest), and
       unclosed fences.
    2. **Preamble removal** — Strip "Here's the code:" etc. before code.
    3. **Trailing prose removal** — Strip explanations after the code.
    4. **Whitespace normalization** — Collapse 3+ consecutive blank lines.

    Parameters
    ----------
    raw
        The raw string returned by the LLM.

    Returns
    -------
    str
        Cleaned Python code. May be empty if nothing recoverable was found.
    """
    if not raw or not raw.strip():
        return ""

    text = raw.strip()

    # ── Pass 1: Detect and extract fenced code blocks ────────────────
    fence_pattern = re.compile(
        r"```(?:python|py)?\s*\n(.*?)```",
        re.DOTALL,
    )
    fenced_blocks = fence_pattern.findall(text)

    if fenced_blocks:
        if len(fenced_blocks) == 1:
            text = fenced_blocks[0].strip()
        else:
            largest = max(fenced_blocks, key=len)
            logger.warning(
                "LLM emitted %d code blocks — extracted largest (%d chars)",
                len(fenced_blocks),
                len(largest),
            )
            text = largest.strip()
    else:
        # No complete fences — check for an unclosed opening fence
        unclosed = re.match(r"^```(?:python|py)?\s*\n", text)
        if unclosed:
            text = text[unclosed.end():].strip()

    # ── Pass 2: Strip preamble text before the code ──────────────────
    for pattern in _PREAMBLE_PATTERNS:
        text = pattern.sub("", text)
    text = text.strip()

    # ── Pass 3: Strip trailing prose after the code ──────────────────
    # Walk backwards from the end, skipping blank and comment-only lines.
    # Find the last line that looks like actual Python code.
    # If we find a non-code line (prose) at the end, strip it.
    lines = text.split("\n")
    last_code_line = -1
    trailing_prose_lines: list[str] = []
    for i in range(len(lines) - 1, -1, -1):
        stripped = lines[i].strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            continue
        # Check if this line looks like actual code vs prose
        if any(stripped.startswith(s) for s in _CODE_LINE_STARTERS):
            last_code_line = i
            break
        else:
            # This line is non-blank, non-comment, non-code — probably prose
            trailing_prose_lines.insert(0, lines[i])

    if trailing_prose_lines:
        logger.debug(
            "Stripped trailing prose (%d lines): %s",
            len(trailing_prose_lines),
            " ".join(l.strip()[:60] for l in trailing_prose_lines),
        )
        text = "\n".join(lines[:last_code_line + 1]) if last_code_line >= 0 else ""

    # ── Pass 4: Collapse excessive blank lines ───────────────────────
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


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
    except Exception as e:
        logger.error("LLM generation failed: %s", e)
        return {
            "draft_snippet": "",
            "original_draft_code": state.get("original_draft_code", ""),
            "fatal_error": f"LLM generation failed: {e}",
        }

    # ── 3. Multi-pass sanitization pipeline ───────────────────────────
    draft_snippet = _sanitize_llm_output(raw_output)
    # Final safety net — strip any remaining fences the multi-pass missed
    draft_snippet = OrkaLangChainClient.fix_md_fences(draft_snippet)

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



