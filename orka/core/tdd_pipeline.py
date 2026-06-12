"""
TDD StateGraph Pipeline — LangGraph Flow Engineering for iterative test generation.

This is **not** a ReAct agent.  There is no ``create_react_agent``, no
``ToolNode``, and no unbounded message array.

Instead, the control flow is a **deterministic state machine** built with
LangGraph's ``StateGraph``.  LLM calls are scoped to exactly two reasoning
nodes: the Generator and the Fixer.

Pipeline
--------
::

    START -> gather_context -> generate_initial_tests -> run_pytest
    -> (condition) if passed -> END
    -> if iteration_count >= max_iterations -> END
    -> else -> fix_tests -> run_pytest (loop)

State
-----
:class:`TestGenState` uses **explicit bounded fields** — not an append-only
``messages`` list — to prevent context-window explosion.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from typing import Any, Optional

import yaml
from langgraph.graph import END, StateGraph
from typing_extensions import TypedDict

from orka.clients import OrkaLangChainClient
from orka.config import settings
from orka.core.compiler import PromptCompiler
from orka.core.import_fixer import resolve_import
from orka.core.ingester import OrkaGraphDB
from orka.core.rule_resolver import resolve_rules
from orka.core.templates import InjectionPoint, PromptTemplate
from orka.core.validator import validate_code_snippet
from orka.surgery.synthesizer import extract_class_source, extract_method_source

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "prompts" / "templates"
_MAX_ERROR_SUMMARY_CHARS = 2000
_DEFAULT_MAX_ITERATIONS = 3


# ===================================================================
# State schema  (bounded fields — NOT an append-only messages list)
# ===================================================================


class TestGenState(TypedDict):
    """Bounded state for the TDD StateGraph pipeline.

    Every field is explicit and has a bounded size.  There is no unbounded
    ``messages`` list — the LLM is only invoked inside specific nodes with
    carefully scoped context.
    """

    # --- Input (set by caller, immutable during execution) ---
    source_file: str
    method_name: str
    class_name: Optional[str]
    output_path: Optional[str]
    max_iterations: int
    provider: str

    # --- Gathered context (set by ``node_gather_context``) ---
    existing_code: str
    class_context: str
    similar_tests: list[str]

    # --- Test code iteration (set by generate / fix nodes) ---
    current_test_code: str

    # --- Pytest results (set by ``node_run_pytest``) ---
    pytest_output: str
    pytest_passed: bool
    error_summary: str

    # --- Loop control ---
    iteration_count: int


# ===================================================================
# Internal helpers
# ===================================================================


def _load_template(name: str) -> PromptTemplate:
    """Load a :class:`PromptTemplate` from a YAML file in the templates dir."""
    path = _TEMPLATES_DIR / f"{name}.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if "injection_points" in data:
        data["injection_points"] = [InjectionPoint(ip) for ip in data["injection_points"]]
    return PromptTemplate(**data)


def _resolve_import_safe(
    file_path: str,
    class_name: Optional[str],
    method_name: str,
) -> str:
    """Resolve the import statement, returning empty string on failure."""
    graph_db = None
    try:
        cache_file = os.path.join(str(settings.PROJECT_ROOT), ".orka_cache.json")
        if os.path.exists(cache_file):
            graph_db = OrkaGraphDB(cache_file=cache_file)
    except Exception:
        pass

    stmt = resolve_import(
        file_path=file_path,
        class_name=class_name,
        method_name=method_name,
        workspace_dir=str(settings.PROJECT_ROOT),
        graph_db=graph_db,
    )
    if stmt is None:
        stmt = resolve_import(
            file_path=file_path,
            class_name=class_name,
            method_name=method_name,
            workspace_dir=str(settings.PROJECT_ROOT),
            graph_db=None,
        )
    return stmt or f"# (could not resolve import for {method_name})\n"


# ===================================================================
# Node 1: gather_context
# ===================================================================


def node_gather_context(state: TestGenState) -> dict[str, Any]:
    """Extract source code and find similar tests — purely deterministic.

    1. Extracts the target method/function source from the source file.
    2. Extracts the surrounding class context (if applicable).
    3. Queries ChromaDB for semantically similar tests via ``OrkaVectorDB``.
    """
    logger.info("Gathering context for %s in %s", state["method_name"], state["source_file"])

    file_path = state["source_file"]
    method_name = state["method_name"]
    class_name = state.get("class_name")

    existing_code = extract_method_source(file_path, method_name, class_name)
    if not existing_code:
        raise RuntimeError(
            f"Could not extract source for {method_name} in {file_path}. "
            f"Does the method exist?"
        )

    class_context = ""
    if class_name:
        extracted = extract_class_source(file_path, class_name)
        class_context = extracted or ""

    # Query ChromaDB via OrkaVectorDB
    similar_tests: list[str] = []
    try:
        from orka.core.vector_store import OrkaVectorDB

        chroma_dir = os.path.join(str(settings.PROJECT_ROOT), ".orka_chromadb")
        if os.path.isdir(chroma_dir):
            vector_db = OrkaVectorDB(persist_dir=chroma_dir)
            query_text = f"test_{method_name}" if class_name else method_name
            results = vector_db.search(query=query_text, n_results=3, node_type=None)
            similar_tests = [r.get("source", "") for r in results if r.get("source")]
            if similar_tests:
                logger.info("Found %d similar tests via ChromaDB", len(similar_tests))
    except Exception as exc:
        logger.warning("ChromaDB query failed (non-fatal): %s", exc)

    return {
        "existing_code": existing_code,
        "class_context": class_context,
        "similar_tests": similar_tests,
    }


# ===================================================================
# Node 2: generate_initial_tests
# ===================================================================


def node_generate_initial_tests(state: TestGenState) -> dict[str, Any]:
    """Generate the first draft of tests using the PromptCompiler + LLM.

    Reuses the existing ``test.yaml`` template, ``resolve_rules()``, and
    ``PromptCompiler`` — the same pipeline used by ``orka testgen``.
    The deterministic import statement is prepended after generation.
    """
    logger.info("Generating initial tests (iteration %d)", state["iteration_count"])

    file_path = state["source_file"]
    method_name = state["method_name"]
    class_name = state.get("class_name")
    existing_code = state["existing_code"]
    class_context = state["class_context"]
    provider = state.get("provider") or settings.DEFAULT_PROVIDER

    # 1. Compile prompt
    template = _load_template("test")
    resolved_rules = resolve_rules(
        template_name=template.name,
        injection_points=template.injection_points,
    )
    context_data = {
        "existing_code": existing_code,
        "class_context": class_context,
        "file_path": file_path,
    }
    compiler = PromptCompiler()
    prompt = compiler.compile(template, resolved_rules, context_data)

    # Append similar tests as extra context
    similar_tests = state.get("similar_tests", [])
    if similar_tests:
        prompt += "\n\n### SIMILAR EXISTING TESTS (for reference):\n"
        prompt += "\n---\n".join(similar_tests)

    system_instruction = (
        "You are a pytest specialist. Output ONLY raw Python test functions - "
        "no imports, no module docstrings, no markdown fences."
    )

    # 2. Invoke LLM
    llm_client = OrkaLangChainClient(provider=provider)
    raw_output = llm_client.generate_code(prompt=prompt, system_instruction=system_instruction)
    clean_tests = OrkaLangChainClient.fix_md_fences(raw_output)

    # Validate (non-fatal — the fix loop can repair it)
    result = validate_code_snippet(clean_tests, label=f"{method_name}_tests")
    if not result:
        logger.warning("LLM generated invalid test code (iteration %d): %s",
                       state["iteration_count"], result.error)

    # 3. Build import statement (deterministic)
    import_stmt = _resolve_import_safe(file_path, class_name, method_name)
    full_test_code = f"import pytest\n{import_stmt}{clean_tests}\n"

    return {
        "current_test_code": full_test_code,
        "iteration_count": state["iteration_count"] + 1,
    }


# ===================================================================
# Node 3: run_pytest  (pure Python — no LLM)
# ===================================================================


def node_run_pytest(state: TestGenState) -> dict[str, Any]:
    """Write tests to a temp file and run ``pytest --tb=short``.

    This is **pure Python** — no LLM call.  It:

    1. Writes ``current_test_code`` to a temporary ``.py`` file.
    2. Runs ``pytest --tb=short --exitfirst --no-header``.
    3. Extracts only the failure traceback into ``error_summary``.
    4. Truncates ``error_summary`` to 2000 characters.
    5. Sets ``pytest_passed`` based on the return code.
    """
    logger.info("Running pytest (iteration %d)", state["iteration_count"])

    test_code = state.get("current_test_code", "")
    if not test_code:
        return {
            "pytest_output": "",
            "pytest_passed": False,
            "error_summary": "No test code to run.",
        }

    tmp_dir = tempfile.mkdtemp(prefix="orka_tdd_")
    tmp_file = os.path.join(tmp_dir, "test_generated.py")
    try:
        with open(tmp_file, "w", encoding="utf-8") as f:
            f.write(test_code)
    except OSError as e:
        return {
            "pytest_output": f"Failed to write temp file: {e}",
            "pytest_passed": False,
            "error_summary": f"File write error: {e}",
        }

    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", tmp_file, "--exitfirst", "--tb=short", "--no-header"],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return {
            "pytest_output": "pytest timed out after 120 seconds.",
            "pytest_passed": False,
            "error_summary": "pytest timed out after 120 seconds. The tests may have an infinite loop.",
        }
    except FileNotFoundError:
        return {
            "pytest_output": "pytest not found. Is it installed?",
            "pytest_passed": False,
            "error_summary": "pytest executable not found. Install with: pip install pytest",
        }
    except Exception as e:
        return {
            "pytest_output": f"Subprocess error: {e}",
            "pytest_passed": False,
            "error_summary": f"Subprocess error: {e}",
        }

    pytest_output = result.stdout + "\n" + result.stderr
    passed = result.returncode == 0

    error_summary = ""
    if not passed:
        error_summary = _extract_error_summary(pytest_output)
        error_summary = _truncate_error_summary(error_summary, _MAX_ERROR_SUMMARY_CHARS)

    # Clean up
    try:
        os.remove(tmp_file)
        os.rmdir(tmp_dir)
    except OSError:
        pass

    return {
        "pytest_output": pytest_output,
        "pytest_passed": passed,
        "error_summary": error_summary,
    }


def _extract_error_summary(pytest_output: str) -> str:
    """Extract the most relevant error block from pytest output.

    Prioritises the ``FAILURES`` section, falling back to the last 40
    significant lines.
    """
    if "FAILURES" in pytest_output:
        idx = pytest_output.index("FAILURES")
        relevant = pytest_output[idx:]
        if "short test summary" in relevant:
            relevant = relevant[: relevant.index("short test summary")]
        return relevant.strip()

    lines = [l for l in pytest_output.strip().splitlines()
             if not l.startswith("===")
             and not l.startswith("collected")
             and not l.startswith("platform")
             and not l.startswith("rootdir")]
    return "\n".join(lines[-40:])


def _truncate_error_summary(summary: str, max_chars: int = _MAX_ERROR_SUMMARY_CHARS) -> str:
    """Truncate the error summary, keeping the most recent (bottom) portion."""
    if len(summary) <= max_chars:
        return summary
    truncated = summary[-max_chars:]
    first_nl = truncated.find("\n")
    if 0 < first_nl < 100:
        truncated = truncated[first_nl + 1:]
    return "... [traceback truncated] ...\n" + truncated


# ===================================================================
# Node 4: fix_tests  (LLM Fixer)
# ===================================================================


def node_fix_tests(state: TestGenState) -> dict[str, Any]:
    """Use an LLM Fixer prompt to repair failing tests.

    The fixer receives the current (failing) test code and the truncated
    error summary (≤2000 chars).  It returns corrected test code.
    """
    logger.info("Fixing tests (iteration %d)", state["iteration_count"])

    current_code = state.get("current_test_code", "")
    error_summary = state.get("error_summary", "")
    existing_code = state.get("existing_code", "")
    class_context = state.get("class_context", "")
    provider = state.get("provider") or settings.DEFAULT_PROVIDER

    fixer_prompt = textwrap.dedent(f"""\
        You are a pytest debugging specialist. Your task is to fix the
        failing tests below.

        ### ORIGINAL METHOD/FUNCTION UNDER TEST:
        ```python
        {existing_code}
        ```

        ### CLASS CONTEXT:
        {class_context if class_context else "(standalone function — no class context)"}

        ### CURRENT (FAILING) TEST CODE:
        ```python
        {current_code}
        ```

        ### PYTEST ERROR SUMMARY:
        {error_summary}

        ### FIX INSTRUCTIONS:
        1. Analyse the error message carefully — identify the root cause.
        2. Fix the test code so that all tests pass.
        3. Output ONLY raw Python test functions — no imports, no markdown
           fences, no explanations.
        4. Keep the same import statements if they are correct.
        5. Do not change the test structure unnecessarily.
        6. Use ``pytest.raises(...)`` for expected exceptions.
        7. Use ``pytest.approx()`` for float comparisons.

        ### FIXED TEST CODE (RAW PYTHON ONLY):
    """)

    system_instruction = (
        "You are a pytest debugging specialist. Analyse the error and fix the tests. "
        "Output ONLY raw Python test code — no markdown fences, no explanations."
    )

    llm_client = OrkaLangChainClient(provider=provider)
    try:
        raw_output = llm_client.generate_code(
            prompt=fixer_prompt,
            system_instruction=system_instruction,
        )
        fixed_tests = OrkaLangChainClient.fix_md_fences(raw_output)
    except Exception as e:
        logger.error("LLM fixer call failed: %s", e)
        return {
            "current_test_code": current_code,
            "iteration_count": state["iteration_count"] + 1,
        }

    # Ensure the import preamble is preserved
    if not fixed_tests.lstrip().startswith("import pytest"):
        import_stmt = _resolve_import_safe(
            state["source_file"],
            state.get("class_name"),
            state["method_name"],
        )
        fixed_tests = f"import pytest\n{import_stmt}{fixed_tests}\n"

    result = validate_code_snippet(fixed_tests, label=f"{state['method_name']}_fixed")
    if not result:
        logger.warning("Fixer produced invalid code: %s", result.error)

    return {
        "current_test_code": fixed_tests,
        "iteration_count": state["iteration_count"] + 1,
    }


# ===================================================================
# Conditional edge router
# ===================================================================


def router_should_continue(state: TestGenState) -> str:
    """Determine the next node after ``run_pytest``.

    Returns ``"end"`` if tests passed or max iterations reached,
    otherwise ``"fix_tests"``.
    """
    if state.get("pytest_passed", False):
        logger.info("Tests PASSED — ending pipeline.")
        return "end"

    if state["iteration_count"] >= state["max_iterations"]:
        logger.warning("Max iterations (%d) reached — ending pipeline.", state["max_iterations"])
        return "end"

    logger.info("Tests FAILED — running fix_tests (iteration %d/%d)",
                state["iteration_count"], state["max_iterations"])
    return "fix_tests"


# ===================================================================
# Graph builder
# ===================================================================


def build_tdd_pipeline(provider: Optional[str] = None) -> StateGraph:
    """Build and compile the TDD StateGraph pipeline.

    Parameters
    ----------
    provider
        LLM provider (e.g. ``"together_ai"``, ``"deepseek"``).
        Defaults to the project-wide default.

    Returns
    -------
    StateGraph
        A compiled LangGraph ``StateGraph`` ready for ``.invoke()``.
    """
    actual_provider = provider or settings.DEFAULT_PROVIDER

    workflow = StateGraph(TestGenState)

    workflow.add_node("gather_context", node_gather_context)
    workflow.add_node("generate_initial_tests", node_generate_initial_tests)
    workflow.add_node("run_pytest", node_run_pytest)
    workflow.add_node("fix_tests", node_fix_tests)

    workflow.set_entry_point("gather_context")
    workflow.add_edge("gather_context", "generate_initial_tests")
    workflow.add_edge("generate_initial_tests", "run_pytest")

    workflow.add_conditional_edges(
        "run_pytest",
        router_should_continue,
        {"end": END, "fix_tests": "fix_tests"},
    )

    workflow.add_edge("fix_tests", "run_pytest")

    graph = workflow.compile()

    logger.info(
        "TDD StateGraph pipeline built (provider=%s, max_iterations=%d)",
        actual_provider,
        _DEFAULT_MAX_ITERATIONS,
    )

    return graph


# ===================================================================
# Convenience runner
# ===================================================================


def run_tdd_pipeline(
    source_file: str,
    method_name: str,
    class_name: Optional[str] = None,
    output_path: Optional[str] = None,
    max_iterations: int = _DEFAULT_MAX_ITERATIONS,
    provider: Optional[str] = None,
) -> dict[str, Any]:
    """Convenience function to build and invoke the TDD pipeline.

    Parameters
    ----------
    source_file
        Absolute path to the source file containing the method to test.
    method_name
        Name of the method or function to generate tests for.
    class_name
        Class name containing the method (``None`` for standalone functions).
    output_path
        If provided, write the final test code to this file.
    max_iterations
        Maximum number of fix-attempt iterations (default 3).
    provider
        LLM provider to use.

    Returns
    -------
    dict
        The final ``TestGenState`` with results.
    """
    graph = build_tdd_pipeline(provider=provider)

    initial_state: TestGenState = {
        "source_file": source_file,
        "method_name": method_name,
        "class_name": class_name,
        "output_path": output_path,
        "max_iterations": max_iterations,
        "provider": provider or settings.DEFAULT_PROVIDER,
        "existing_code": "",
        "class_context": "",
        "similar_tests": [],
        "current_test_code": "",
        "pytest_output": "",
        "pytest_passed": False,
        "error_summary": "",
        "iteration_count": 0,
    }

    result = graph.invoke(initial_state)

    # Write to output path if provided
    current_code = result.get("current_test_code", "")
    if output_path and current_code:
        abs_output = (
            output_path
            if os.path.isabs(output_path)
            else os.path.join(str(settings.PROJECT_ROOT), output_path)
        )
        os.makedirs(os.path.dirname(abs_output), exist_ok=True)
        with open(abs_output, "w", encoding="utf-8") as f:
            f.write(current_code)
        logger.info("Tests written to %s", abs_output)

    return result
