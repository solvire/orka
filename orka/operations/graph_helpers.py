"""
Shared graph DB helpers for the surgery pipeline.

Provides a **lazy singleton** (:func:`get_graph_db`) so that the large
``.orka_cache.graph.json`` is loaded at most once per process, plus the
prompt-facing rendering helpers built on top of the resolution layer in
:mod:`orka.core.dependency_resolver`.

Functions
---------
- :func:`get_graph_db` — lazy singleton for ``OrkaGraphDB``
- :func:`clear_graph_db_cache` — reset the singleton
- :func:`render_dependency_map_table` — markdown table for prompts
- :func:`render_caller_constraints_table` — markdown table for prompts
- :func:`extract_dependency_signatures` — formatted signatures for GAG

Symbol resolution (``resolve_target``, ``build_dependency_map``,
``build_caller_constraints``, ``resolve_symbol``, ``resolve_undefined_names``)
lives in :mod:`orka.core.dependency_resolver`.
"""

from __future__ import annotations

import logging
import os

from orka.config import settings
from orka.core.dependency_resolver import build_dependency_map

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════
# Lazy singleton
# ═══════════════════════════════════════════════════════════════════════

_GRAPH_DB_INSTANCE: object | None = None
"""Module-level singleton — loaded at most once per process."""


def get_graph_db() -> object | None:
    """Return the cached ``OrkaGraphDB`` instance, or ``None`` if unavailable.

    The graph files (``.orka_cache.json`` / ``.orka_cache.graph.json``) are
    loaded exactly once — subsequent calls return the same object.

    Returns
    -------
    object or None
        An ``OrkaGraphDB`` instance with a populated ``graph`` attribute,
        or ``None`` if the cache file is missing or loading fails.
    """
    global _GRAPH_DB_INSTANCE

    if _GRAPH_DB_INSTANCE is not None:
        return _GRAPH_DB_INSTANCE

    cache_file = os.path.join(str(settings.PROJECT_ROOT), ".orka_cache.json")
    if not os.path.exists(cache_file):
        logger.debug("Graph DB cache file not found at %s", cache_file)
        return None

    try:
        from orka.core.ingester import OrkaGraphDB

        g = OrkaGraphDB(cache_file=cache_file)
        if g.graph.number_of_nodes() > 0:
            logger.debug(
                "Graph DB loaded: %d nodes, %d edges",
                g.graph.number_of_nodes(),
                g.graph.number_of_edges(),
            )
            _GRAPH_DB_INSTANCE = g
        else:
            logger.debug("Graph DB loaded but has zero nodes — treating as unavailable")
        return _GRAPH_DB_INSTANCE
    except Exception as exc:
        logger.warning("Failed to load Graph DB: %s", exc)
        return None


def clear_graph_db_cache() -> None:
    """Reset the singleton (e.g. after a fresh ``orka scan`` in the same process)."""
    global _GRAPH_DB_INSTANCE
    _GRAPH_DB_INSTANCE = None


# ═══════════════════════════════════════════════════════════════════════
# Rendering (markdown tables for templates)
# ═══════════════════════════════════════════════════════════════════════


def render_dependency_map_table(deps: list[dict[str, str]]) -> str:
    """Render the dependency map as a compact markdown table.

    Columns: Name, Import Path, Type.
    """
    if not deps:
        return "No resolvable dependencies found."
    lines = [
        "| Name | Import Path | Type |",
        "|------|-------------|------|",
    ]
    for d in deps:
        lines.append(f"| {d['name']} | `{d['import_path']}` | {d['node_type']} |")
    return "\n".join(lines)


def render_caller_constraints_table(callers: list[dict[str, str]]) -> str:
    """Render the caller constraints as a compact markdown table.

    Columns: Caller, Import Path.
    """
    if not callers:
        return "No known callers in the internal graph."
    lines = [
        "| Caller | Import Path |",
        "|--------|-------------|",
    ]
    for c in callers:
        lines.append(f"| {c['name']} | `{c['import_path']}` |")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════
# GAG — Dependency Signature Injection
# ═══════════════════════════════════════════════════════════════════════


def extract_dependency_signatures(
    graph_db: object,
    source_file: str,
    method_name: str,
    class_name: str | None,
) -> str:
    """Query the graph for the target's in-scope dependencies and return a
    formatted signature block the LLM can use to avoid hallucinating arguments.

    This builds on :func:`orka.core.dependency_resolver.build_dependency_map` —
    it collects every function/method/class in the same module and imported
    modules, then formats their names, import paths, and docstrings into a
    compact block suitable for template injection.

    Returns an empty string if no dependencies are found or the graph DB
    is unavailable.
    """
    deps = build_dependency_map(
        source_file,
        method_name,
        class_name,
        graph_db,
        base_dir=str(settings.PROJECT_ROOT),
    )
    if not deps:
        logger.debug("No dependencies found for dependency signatures")
        return ""

    dep_parts: list[str] = []
    visited: set[str] = set()

    for dep in deps:
        name = dep["name"]
        if name in visited:
            continue
        visited.add(name)

        # Try to get docstring from graph node metadata
        dep_docstring = ""
        for node, attrs in graph_db.graph.nodes(data=True):
            if attrs.get("name") == name:
                ds = attrs.get("docstring", "")
                if ds:
                    dep_docstring = ds.strip()[:300]
                break

        lines = [f"DEPENDENCY: {name}"]
        lines.append(f"TYPE: {dep['node_type']}")
        lines.append(f"IMPORT: {dep['import_path']}")
        if dep.get("file_path"):
            lines.append(f"LOCATION: {dep['file_path']}:{dep.get('lineno', '?')}")
        if dep_docstring:
            lines.append(f"DOCSTRING: {dep_docstring}")
        dep_parts.append("\n".join(lines))

    if not dep_parts:
        return ""

    block = "\n---\n".join(dep_parts)
    logger.info("Built %d dependency signature(s) for GAG", len(dep_parts))
    return block
