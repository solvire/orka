"""
Context gatherer node — extracts source code, finds similar examples, and
backs up the target file.

This is Node 1 of the surgery graph. It calls a **fast LLM** (HyDE technique)
to generate a semantic search query for ChromaDB, then applies a self-exclusion
filter to avoid feeding the LLM its own target code as a "similar example".
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from orka.config import settings
from orka.operations.graph_helpers import (
    extract_dependency_signatures,
    get_graph_db,
)
from orka.operations.helpers import load_template
from orka.surgery.synthesizer import extract_class_source, extract_method_source

logger = logging.getLogger(__name__)

# ── Internal helpers ──────────────────────────────────────────────────


def _extract_docblock(source: str) -> str:
    """Extract the first triple-quoted docstring from *source*.

    Uses a simple regex — no LibCST needed at this stage.
    Returns the docblock body (stripped) or an empty string.
    """
    match = re.search(r'"""(.*?)"""', source, re.DOTALL)
    if match:
        return match.group(1).strip()
    match = re.search(r"'''(.*?)'''", source, re.DOTALL)
    if match:
        return match.group(1).strip()
    return ""


def _generate_smart_query(
    method_name: str,
    prompt_template_name: str,
    docblock: str,
    requirements: str,
    existing_code: str = "",
) -> str | None:
    """Use the fast LLM (HyDE) to generate a semantic search query for ChromaDB.

    Falls back to ``None`` if the LLM call fails or times out (caller should
    use the deterministic fallback).

    Parameters
    ----------
    method_name
        The name of the target function/method.
    prompt_template_name
        ``"test"`` or ``"refactor"``.
    docblock
        The extracted docblock text (may be empty).
    requirements
        Business requirements text (may be empty).
    existing_code
        The full source of the target function.  The first 2000 chars are
        injected into the prompt so the LLM can infer architectural patterns.

    Returns
    -------
    str or None
        A single-line semantic search query, or ``None`` on failure.
    """
    try:
        from orka.core.compiler import PromptCompiler
        from orka.core.rule_resolver import resolve_rules

        template = load_template("hyde_query")
        resolved_rules = resolve_rules(
            template_name=template.name,
            injection_points=template.injection_points,
        )

        # Preview: first 2000 chars of the source, skipping whitespace-only lines
        preview = existing_code[:2000].strip() if existing_code else ""

        context_data: dict[str, str] = {
            "prompt_template_name": prompt_template_name,
            "method_name": method_name,
            "docblock": docblock or "No docstring available.",
            "requirements": requirements or "No additional requirements.",
            "existing_code_preview": preview,
        }

        compiler = PromptCompiler()
        compiled = compiler.compile(template, resolved_rules, context_data)

        # Strip the system section — we only send the user message to the LLM
        # Extract the user block from the compiled prompt
        user_part = compiled
        if "### STRICT CONSTRAINTS:" in compiled:
            # Rules were injected — find the user content after them
            parts = compiled.split("### STRICT CONSTRAINTS:")
            if len(parts) > 1:
                user_part = parts[1]

        # Invoke the fast LLM
        # The compiled prompt already includes the system rules (from the
        # hyde_query template), so we pass it as the main prompt without
        # a separate system_instruction.
        from orka.clients import OrkaLangChainClient

        client = OrkaLangChainClient(model_tier="fast")
        query = client.generate_code(prompt=compiled)
        cleaned = query.strip().strip('"').strip("'").strip()
        if cleaned:
            logger.debug("HyDE query generated: %s", cleaned[:120])
            return cleaned
    except Exception as exc:
        logger.warning("Smart query generation failed (non-fatal): %s", exc)
    return None


def _build_fallback_query(
    method_name: str,
    prompt_template_name: str,
    docblock: str,
    requirements: str,
) -> str:
    """Deterministic fallback query when the fast LLM is unavailable.

    Produces a simple natural-language string based on the available data.
    """
    if prompt_template_name == "test":
        phrase = docblock or method_name
        return f"pytest test function testing {phrase}"
    else:
        parts = [p for p in (requirements, docblock) if p]
        phrase = parts[0] if parts else method_name
        return f"refactoring a function that {phrase}"


def _self_exclusion_filter(
    results: list[dict[str, Any]],
    existing_code: str,
    class_context: str,
    max_results: int = 2,
) -> list[str]:
    """Filter out results that duplicate the target's own code.

    Discards any result whose ``source`` is an exact substring of
    *existing_code* or *class_context*.

    Parameters
    ----------
    results
        Raw results from ``OrkaVectorDB.search()``.
    existing_code
        The target function's source code.
    class_context
        The surrounding class source (may be empty).
    max_results
        Maximum number of results to keep.

    Returns
    -------
    list[str]
        Filtered source code strings.
    """
    filtered: list[str] = []
    for r in results:
        source = r.get("source", "")
        if not source:
            continue
        # Self-exclusion: skip if this is the target's own code
        if existing_code and source.strip() in existing_code.strip():
            continue
        if class_context and source.strip() in class_context.strip():
            continue
        filtered.append(source)
        if len(filtered) >= max_results:
            break
    return filtered


# ── Entry point ───────────────────────────────────────────────────────


def execute(state: dict[str, Any]) -> dict[str, Any]:
    """Gather context for the surgery operation.

    Steps
    -----
    1. Extract the target method/function source from ``source_file``.
    2. If ``class_name`` is provided, extract the surrounding class context.
    3. Generate a semantic search query via the fast LLM (HyDE), with a
       deterministic fallback.
    4. Query ChromaDB for similar code examples.
    5. Apply a self-exclusion filter to discard the target's own code.
    6. Create an in-memory backup of the ``target_output_file`` (if it exists).

    Parameters
    ----------
    state
        The current :class:`~orka.operations.state.SurgeryState`.

    Returns
    -------
    dict
        Updated state keys: ``existing_code``, ``class_context``,
        ``similar_examples``, ``original_file_backup``.
    """
    source_file = state["source_file"]
    method_name = state["method_name"]
    class_name = state.get("class_name")
    prompt_template_name = state["prompt_template_name"]
    target_output_file = state["target_output_file"]
    requirements = state.get("requirements", "")

    logger.info(
        "Gathering context for %s in %s",
        state["target_node_id"],
        source_file,
    )

    # ── 1. Extract method source ──────────────────────────────────────
    existing_code = extract_method_source(source_file, method_name, class_name)
    if not existing_code:
        raise RuntimeError(
            f"Could not extract source for {state['target_node_id']} in {source_file}. "
            f"Does the method exist?"
        )

    # ── 2. Extract class context (if applicable) ───────────────────────
    class_context = ""
    if class_name:
        extracted = extract_class_source(source_file, class_name)
        class_context = extracted or ""

    # ── 3. Build semantic query (HyDE → fallback) ──────────────────────
    docblock = _extract_docblock(existing_code)

    query_text = _generate_smart_query(
        method_name, prompt_template_name, docblock, requirements, existing_code,
    )
    if not query_text:
        query_text = _build_fallback_query(
            method_name, prompt_template_name, docblock, requirements,
        )
        logger.info("Using fallback query: %s", query_text)
    else:
        logger.info("Using HyDE-generated query: %s", query_text[:120])

    # ── 4. Query ChromaDB for similar examples ─────────────────────────
    similar_examples: list[str] = []
    try:
        from orka.core.vector_store import OrkaVectorDB

        chroma_dir = os.path.join(str(settings.PROJECT_ROOT), ".orka_chromadb")
        if os.path.isdir(chroma_dir):
            vector_db = OrkaVectorDB(persist_dir=chroma_dir)
            results = vector_db.search(query=query_text, n_results=5, node_type=None)
            # ── 5. Self-exclusion filter ──────────────────────────────
            similar_examples = _self_exclusion_filter(
                results, existing_code, class_context, max_results=2,
            )
            if similar_examples:
                logger.info(
                    "Found %d similar examples via ChromaDB (filtered from %d)",
                    len(similar_examples),
                    len(results),
                )
    except Exception as exc:
        logger.warning("ChromaDB query failed (non-fatal): %s", exc)

    # ── 6. Backup target file (if it exists) ───────────────────────────
    original_file_backup: str | None = None
    if os.path.exists(target_output_file):
        try:
            with open(target_output_file, "r", encoding="utf-8") as f:
                original_file_backup = f.read()
        except OSError as e:
            logger.warning("Could not read target file for backup: %s", e)

    # ── 7. Graph DB — Dependency Signature Injection (GAG) ────────────
    dependency_signatures = ""
    try:
        graph_db = get_graph_db()
        if graph_db is not None:
            dependency_signatures = extract_dependency_signatures(
                graph_db, source_file, method_name, class_name,
            )
    except Exception as exc:
        logger.warning("Graph DB dependency lookup failed (non-fatal): %s", exc)

    return {
        "existing_code": existing_code,
        "class_context": class_context,
        "similar_examples": similar_examples,
        "dependency_signatures": dependency_signatures,
        "original_file_backup": original_file_backup,
    }

