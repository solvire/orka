"""
End-to-end smoke test for the LangGraph surgery pipeline.

Executes a REAL LLM call (no mocks) against the configured DEFAULT_PROVIDER,
proving that gather_context → compile_prompt → generate_draft → validate_draft
all route correctly and produce valid output.

Requires an active API key (e.g. DEEPSEEK_API_KEY or TOGETHER_API_KEY).
Automatically skipped when no key is available.
"""

import pytest

from orka.config import settings
from orka.operations.graph import run_surgery


# Skip the entire module when no API key is configured
pytestmark = pytest.mark.skipif(
    not settings.get_api_key(),
    reason="No API key configured — skipping live E2E test.",
)


DUMMY_CALCULATOR = """\
class Calculator:
    def multiply(self, a: int, b: int) -> int:
        return a * b
"""


def test_live_refactor_pipeline_dry_run(tmp_path):
    """Execute a full surgery pipeline (dry-run) against a real LLM.

    Creates a dummy Calculator.multiply method, asks the LLM to add 10
    to the return value, and asserts the pipeline succeeds without
    writing to disk.
    """
    # ── 1. Arrange: create a temporary source file ──────────────────
    source_file = tmp_path / "dummy_math.py"
    source_file.write_text(DUMMY_CALCULATOR, encoding="utf-8")

    # ── 2. Act: invoke the full surgery graph ───────────────────────
    state = run_surgery(
        source_file=str(source_file),
        method_name="multiply",
        class_name="Calculator",
        requirements=(
            "Modify this method to add 10 to the result before "
            "returning it. Do not change the method signature."
        ),
        prompt_template_name="refactor",
        dry_run=True,
    )

    # ── 3. Assert: pipeline succeeded ────────────────────────────────
    assert state.get("is_valid") is True, (
        f"Pipeline failed — validation_output: {state.get('validation_output')!r}"
    )
    assert state.get("fatal_error") is None, (
        f"Pipeline hit fatal error: {state.get('fatal_error')!r}"
    )

    # ── 4. Assert: LLM produced the expected logic ──────────────────
    snippet = state.get("draft_snippet", "")
    assert "+ 10" in snippet or "+10" in snippet, (
        f"Expected '+ 10' or '+10' in draft_snippet, got:\n{snippet!r}"
    )

    # ── 5. Assert: assembled file is valid and contains the change ───
    draft_file = state.get("draft_file_content", "")
    assert len(draft_file) > 0, "draft_file_content should not be empty"
    assert "Calculator" in draft_file, (
        "Assembled file should still contain the Calculator class"
    )

    # ── 6. Assert: original file was NOT modified (dry-run) ─────────
    original = source_file.read_text(encoding="utf-8")
    assert original.strip() == DUMMY_CALCULATOR.strip(), (
        "Source file must not be modified in dry-run mode"
    )