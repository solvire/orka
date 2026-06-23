"""
Shared utility functions for the surgery pipeline controllers.

Extracted from ``orka/core/tdd_pipeline.py`` and generalised for both
refactoring and test generation workflows.
"""

from __future__ import annotations

import logging
import os
import textwrap
from pathlib import Path
from typing import Optional

import yaml

from orka.core.templates import InjectionPoint, PromptTemplate
from orka.core.validator import extract_error_summary, truncate_error_summary

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "prompts" / "templates"


# ── Template loading ───────────────────────────────────────────────────


def load_template(name: str) -> PromptTemplate:
    """Load a :class:`PromptTemplate` from a YAML file in the templates dir.

    Parameters
    ----------
    name
        Template name (e.g. ``"refactor"``, ``"test"``).  Corresponds to
        ``<name>.yaml`` in :data:`_TEMPLATES_DIR`.

    Returns
    -------
    PromptTemplate
        The deserialised template.

    Raises
    ------
    FileNotFoundError
        If the template file does not exist.
    """
    path = _TEMPLATES_DIR / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(
            f"Template {name!r} not found at {path}. "
            f"Available: {list(_TEMPLATES_DIR.glob('*.yaml'))}"
        )

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if "injection_points" in data:
        data["injection_points"] = [InjectionPoint(ip) for ip in data["injection_points"]]
    return PromptTemplate(**data)


# ── Error truncation ───────────────────────────────────────────────────
#
# ``extract_error_summary`` and ``truncate_error_summary`` were relocated to
# :mod:`orka.core.validator` — the canonical home for validation utilities —
# so that ``validate_four_gates`` can use them without an ``operations`` ->
# ``core`` dependency inversion.  They are re-exported from the import block
# above for backward compatibility with existing callers.


# ── Fixer prompt construction ──────────────────────────────────────────


def build_fixer_prompt(
    operation_type: str,
    draft_snippet: str,
    validation_output: str,
    existing_code: str,
    class_context: str,
    requirements: str,
    test_file_target: Optional[str] = None,
) -> tuple[str, str]:
    """Construct the fixer prompt and system instruction.

    Returns
    -------
    tuple[str, str]
        ``(fixer_prompt, system_instruction)``
    """
    if operation_type == "testgen":
        prompt = textwrap.dedent(f"""\
            You are a pytest debugging specialist. Your task is to fix the
            failing tests below.

            ### ORIGINAL METHOD/FUNCTION UNDER TEST:
            ```python
            {existing_code}
            ```

            ### CLASS CONTEXT:
            {class_context if class_context else "(standalone function — no class context)"}

            ### ORIGINAL INTENT / REQUIREMENTS:
            {requirements}

            ### CURRENT (FAILING) TEST CODE:
            ```python
            {draft_snippet}
            ```

            ### PYTEST ERROR SUMMARY:
            {validation_output}

            ### FIX INSTRUCTIONS:
            1. Analyse the error message carefully — identify the root cause.
            2. Fix the test code so that all tests pass.
            3. The function MUST start with "def test_" and accept (tmp_path)
               as its only parameter — no monkeypatch, no mocker, no fixtures.
            4. ALL code must be INSIDE the function body. No module-level
               statements, no monkeypatch.setattr(), no imports.
            5. Output ONLY raw Python test functions — no imports, no markdown
               fences, no explanations.
            6. Use ``pytest.raises(...)`` for expected exceptions.
            7. Use ``pytest.approx()`` for float comparisons.

            ### FIXED TEST CODE (RAW PYTHON ONLY):
        """)
        system = (
            "You are a pytest debugging specialist. Analyse the error and fix the tests. "
            "Output ONLY raw Python test code — no markdown fences, no explanations. "
            "The function MUST start with \"def test_\" and accept (tmp_path) as its "
            "only parameter. ALL code must be INSIDE the function body. "
            "No module-level statements, no monkeypatch.setattr(), no imports."
        )

    else:  # refactor
        prompt = textwrap.dedent(f"""\
            You are an elite Python backend architect. Your task is to fix the
            code body below.

            ### EXISTING METHOD SIGNATURE & CODE:
            ```python
            {existing_code}
            ```

            ### CLASS CONTEXT:
            {class_context if class_context else "(standalone function)"}

            ### YOUR PREVIOUS (FAILING) DRAFT:
            ```python
            {draft_snippet}
            ```

            ### ERROR / VALIDATION OUTPUT:
            {validation_output}

            ### ORIGINAL REQUIREMENTS:
            {requirements}

            ### FIX INSTRUCTIONS:
            1. Analyse the error — identify the root cause.
            2. Fix the body logic so it is syntactically valid and correct.
            3. Output ONLY raw Python code at the base indentation level.
            4. DO NOT output the method signature, decorators, or markdown fences.

            ### FIXED BODY LOGIC (RAW PYTHON ONLY):
        """)
        system = (
            "You are a pure code synthesis engine. Output ONLY raw Python code "
            "at the base indentation level. No signatures, decorators, or explanations."
        )

    return prompt, system

