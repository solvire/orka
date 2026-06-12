"""
Context gatherer node — extracts source code, finds similar examples, and
backs up the target file.

This is Node 1 of the surgery graph. It is pure Python (no LLM call).
"""

from __future__ import annotations

import logging
import os
from typing import Any

from orka.config import settings
from orka.surgery.synthesizer import extract_class_source, extract_method_source

logger = logging.getLogger(__name__)


def execute(state: dict[str, Any]) -> dict[str, Any]:
    """Gather context for the surgery operation.

    Steps
    -----
    1. Extract the target method/function source from ``source_file``.
    2. If ``class_name`` is provided, extract the surrounding class context.
    3. Query ChromaDB for semantically similar code/tests via ``OrkaVectorDB``.
    4. Create an in-memory backup of the ``target_output_file`` (if it exists).

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
    target_output_file = state["target_output_file"]

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

    # ── 3. Query ChromaDB for similar examples ─────────────────────────
    similar_examples: list[str] = []
    try:
        from orka.core.vector_store import OrkaVectorDB

        chroma_dir = os.path.join(str(settings.PROJECT_ROOT), ".orka_chromadb")
        if os.path.isdir(chroma_dir):
            vector_db = OrkaVectorDB(persist_dir=chroma_dir)
            query_text = f"test_{method_name}" if class_name else method_name
            results = vector_db.search(query=query_text, n_results=3, node_type=None)
            similar_examples = [
                r.get("source", "")
                for r in results
                if r.get("source")
            ]
            if similar_examples:
                logger.info("Found %d similar examples via ChromaDB", len(similar_examples))
    except Exception as exc:
        logger.warning("ChromaDB query failed (non-fatal): %s", exc)

    # ── 4. Backup target file (if it exists) ───────────────────────────
    original_file_backup: str | None = None
    if os.path.exists(target_output_file):
        try:
            with open(target_output_file, "r", encoding="utf-8") as f:
                original_file_backup = f.read()
        except OSError as e:
            logger.warning("Could not read target file for backup: %s", e)

    return {
        "existing_code": existing_code,
        "class_context": class_context,
        "similar_examples": similar_examples,
        "original_file_backup": original_file_backup,
    }
